"""Integration fixtures: one telethon-plus container on the real account.

Only one container runs at a time. Two clients on the same session string at
once make Telegram end the session with AUTH_KEY_DUPLICATED, so a module that
needs other settings restarts the one container (``server.ensure``) instead
of starting a second.

The container runs on the host network, so it reaches the local server below
on 127.0.0.1: that server hands out the files ``send_file`` fetches and
receives the webhook.

Every chat a test writes to is one the suite owns: ``TEST_CHAT`` (Saved
Messages by default) and the groups it creates, which carry
``TEST_GROUP_PREFIX`` and are deleted afterwards. Chats that belong to
someone else are read only, or come from optional settings in tests/.env; a
test whose setting is empty is skipped.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

import httpx
import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "psyb0t/telethon-plus:latest-test"
CONTAINER = "docker-telethon-plus-tests"
FIXTURES = Path(__file__).resolve().parent / "unit" / "fixtures"
# Holds the entity cache across the container restarts, inside the checkout
# so the path is the same on the host and in the dev container.
CACHE_DIR = ROOT / ".testing" / "integration-cache"
# Every group the suite creates starts with this; leftovers from an
# interrupted run are deleted at the start of the next one.
TEST_GROUP_PREFIX = "telethon-plus-it-"
READY_TIMEOUT_SECONDS = 90.0
# Optional settings: a test that needs one is skipped while it is empty.
OPTIONAL_SETTINGS = {
    # A public channel to read. Read only.
    "TEST_PUBLIC_CHANNEL": "@telegram",
    # A public channel or group the account is NOT in. The join test joins
    # and leaves it again.
    "TEST_JOIN_CHAT": "",
    # A t.me/+hash invite to a chat the account is NOT in. Joined and left.
    "TEST_INVITE_LINK": "",
    # A user or bot you control. The admin tests ban, unban, promote, demote
    # and kick it in a group the suite creates and deletes. Defaults to the
    # TEST_BOT_TOKEN bot.
    "TEST_PEER_USER": "",
    # The token of a bot you own (from @BotFather). The update tests make it
    # message the account, because what the server does itself raises no
    # event. The account sends the bot /start so the bot may write to it.
    "TEST_BOT_TOKEN": "",
}
BOT_API = "https://api.telegram.org"


def _load_env() -> dict[str, str]:
    # Root .env is the base; tests/.env overlays on top (tests-specific overrides).
    root_env = ROOT / ".env"
    tests_env = ROOT / "tests" / ".env"

    if not root_env.exists() and not tests_env.exists():
        pytest.skip(
            ".env not found, copy .env.example to .env and fill it in",
            allow_module_level=True,
        )

    values: dict[str, str] = {}
    for path in (root_env, tests_env):
        if path.exists():
            values.update({k: v for k, v in dotenv_values(path).items() if v is not None})

    for required in ("TELETHON_API_ID", "TELETHON_API_HASH", "TELETHON_SESSION"):
        if not values.get(required):
            pytest.skip(
                f"{required} missing, set it in .env or tests/.env",
                allow_module_level=True,
            )
    values.setdefault("TEST_CHAT", "me")
    for name, default in OPTIONAL_SETTINGS.items():
        values.setdefault(name, default)
    return values


def _build_image() -> None:
    subprocess.run(["docker", "build", "-t", IMAGE, str(ROOT)], check=True)


def _stop_container() -> None:
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def container_logs() -> str:
    logs = subprocess.run(
        ["docker", "logs", CONTAINER], capture_output=True, text=True, check=False
    )
    return logs.stdout + logs.stderr


def _wait_ready(base_url: str, timeout: float = READY_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/healthz", timeout=2.0)
            if resp.status_code == 200 and resp.json().get("authorized"):
                return
            last_err = RuntimeError(f"not ready: {resp.status_code} {resp.text}")
        except (httpx.HTTPError, ValueError) as exc:
            last_err = exc
        time.sleep(1.0)
    raise RuntimeError(
        f"container never became ready: {last_err}\n--- container logs ---\n{container_logs()}"
    )


# ---------- local file and webhook server -----------------------------------------


@dataclass
class LocalServer:
    """Serves ``files`` at /files/<name> and records webhook POSTs to /hook."""

    files: Dict[str, bytes] = field(default_factory=dict)
    hooks: List[Dict[str, Any]] = field(default_factory=list)
    port: int = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def file_url(self, name: str) -> str:
        return f"{self.url}/files/{name}"

    def wait_for_hook(
        self, match: Callable[[Dict[str, Any]], bool], timeout: float = 30.0
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for payload in list(self.hooks):
                if match(payload):
                    return payload
            time.sleep(0.2)
        raise AssertionError(f"no matching webhook in {timeout}s; got {self.hooks[-5:]}")


def _handler_for(local: LocalServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server's name)
            name = self.path.removeprefix("/files/")
            body = local.files.get(name)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            if self.path == "/hook":
                local.hooks.append(json.loads(raw))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


@pytest.fixture(scope="session")
def local() -> Iterator[LocalServer]:
    local = LocalServer()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(local))
    local.port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield local
    finally:
        httpd.shutdown()


# ---------- the container -----------------------------------------------------------


class Server:
    """The one telethon-plus container. ``ensure`` returns its URL, restarting
    it first when the requested settings differ from the running ones."""

    def __init__(self, env: dict[str, str], local: LocalServer) -> None:
        self.env = env
        self.local = local
        self.settings: Optional[Dict[str, str]] = None
        self.url = ""

    def defaults(self) -> Dict[str, str]:
        return {
            "TELETHON_UPDATES_ENABLED": "true",
            "TELETHON_POST_TO_URL": f"{self.local.url}/hook",
            "TELETHON_LOG_JSON": "true",
            "TELETHON_LOG_LEVEL": self.env.get("TELETHON_LOG_LEVEL", "INFO"),
        }

    def ensure(self, **overrides: str) -> str:
        settings = {**self.defaults(), **overrides}
        if settings == self.settings:
            return self.url
        self.stop()
        port = _free_port()
        settings_with_port = {
            **settings,
            "TELETHON_API_ID": self.env["TELETHON_API_ID"],
            "TELETHON_API_HASH": self.env["TELETHON_API_HASH"],
            "TELETHON_SESSION": self.env["TELETHON_SESSION"],
            "TELETHON_HTTP_LISTEN_ADDRESS": f"127.0.0.1:{port}",
        }
        cmd = ["docker", "run", "-d", "--rm", "--name", CONTAINER, "--network", "host"]
        cmd += ["-v", f"{CACHE_DIR}:/cache"]
        for key, value in settings_with_port.items():
            cmd += ["-e", f"{key}={value}"]
        subprocess.run([*cmd, IMAGE], check=True, stdout=subprocess.DEVNULL)
        self.url = f"http://127.0.0.1:{port}"
        _wait_ready(self.url)
        self.settings = settings
        return self.url

    def stop(self) -> None:
        _stop_container()
        self.settings = None


@pytest.fixture(scope="session")
def env() -> dict[str, str]:
    return _load_env()


@pytest.fixture(scope="session")
def server(env: dict[str, str], local: LocalServer) -> Iterator[Server]:
    if not os.environ.get("TELETHON_TESTS_SKIP_BUILD"):
        _build_image()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.chmod(0o777)
    srv = Server(env, local)
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture()
def base_url(server: Server) -> str:
    """The container with the default settings."""
    return server.ensure()


@pytest.fixture()
def http(base_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=base_url, timeout=120.0) as client:
        yield client


def ok(resp: httpx.Response) -> Any:
    """The JSON body of a 2xx answer; fails with the body otherwise."""
    assert (
        resp.is_success
    ), f"{resp.request.method} {resp.request.url} -> {resp.status_code} {resp.text}"
    return resp.json()


def marker(label: str) -> str:
    """A unique text, so a test finds its own message and nothing else."""
    return f"telethon-plus-it {label} {uuid.uuid4().hex[:10]}"


def require(env: dict[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value:
        pytest.skip(f"set {name} in tests/.env to run this test")
    return value


# ---------- chats the suite owns ----------------------------------------------------


def _delete_leftover_groups(client: httpx.Client) -> None:
    dialogs = ok(client.get("/api/dialogs", params={"search": TEST_GROUP_PREFIX, "limit": 50}))
    for dialog in dialogs:
        if str(dialog.get("title", "")).startswith(TEST_GROUP_PREFIX):
            client.request("DELETE", "/api/chats", json={"chat": str(dialog["id"])})


@pytest.fixture(scope="session")
def group(server: Server) -> Iterator[str]:
    """A supergroup the suite creates, shared by the tests that need a group,
    and deleted at the end of the session."""
    with httpx.Client(base_url=server.ensure(), timeout=120.0) as client:
        _delete_leftover_groups(client)
        created = ok(client.post("/api/chats", json={"title": f"{TEST_GROUP_PREFIX}group"}))
    chat = str(created["id"])
    try:
        yield chat
    finally:
        with httpx.Client(base_url=server.ensure(), timeout=120.0) as client:
            client.request("DELETE", "/api/chats", json={"chat": chat})


@pytest.fixture()
def cleanup(http: httpx.Client) -> Iterator[Callable[[str, int], None]]:
    """``cleanup(chat, message_id)`` deletes the message after the test,
    whether it passed or not."""
    sent: List[tuple[str, int]] = []
    yield lambda chat, message_id: sent.append((chat, message_id))
    by_chat: Dict[str, List[int]] = {}
    for chat, message_id in sent:
        by_chat.setdefault(chat, []).append(message_id)
    for chat, ids in by_chat.items():
        http.request("DELETE", "/api/messages", json={"chat": chat, "message_ids": ids})


@pytest.fixture(scope="session")
def media() -> Dict[str, bytes]:
    return {
        "clip.mp4": (FIXTURES / "video-64x48-2s.mp4").read_bytes(),
        "song.mp3": (FIXTURES / "audio-2s.mp3").read_bytes(),
        "notes.txt": b"telethon-plus integration test file\n",
    }


# ---------- a bot the developer owns ----------------------------------------------


@dataclass
class Bot:
    """A bot the developer owns, driven through the Bot API."""

    token: str
    id: int
    username: str

    def call(self, method: str, **params: Any) -> Any:
        resp = httpx.post(f"{BOT_API}/bot{self.token}/{method}", json=params, timeout=30.0)
        body = resp.json()
        assert body.get("ok"), f"Bot API {method} failed: {body.get('description')}"
        return body["result"]


@pytest.fixture(scope="session")
def bot(env: dict[str, str], server: Server) -> Bot:
    """The TEST_BOT_TOKEN bot, after the account sent it /start so it may
    write to the account."""
    token = require(env, "TEST_BOT_TOKEN")
    me = httpx.get(f"{BOT_API}/bot{token}/getMe", timeout=30.0).json()
    assert me.get("ok"), "TEST_BOT_TOKEN is not a valid bot token"
    found = Bot(token=token, id=me["result"]["id"], username=me["result"]["username"])
    with httpx.Client(base_url=server.ensure(), timeout=120.0) as client:
        start = ok(
            client.post("/api/messages", json={"chat": f"@{found.username}", "text": "/start"})
        )
        client.request(
            "DELETE",
            "/api/messages",
            json={"chat": f"@{found.username}", "message_ids": [start["id"]]},
        )
    return found
