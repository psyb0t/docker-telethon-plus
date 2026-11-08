"""HTTP surface: auth, middleware order, download headers, and how tool
errors are counted and mapped on REST and MCP."""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Callable, Dict, Tuple
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult
from starlette.websockets import WebSocketDisconnect
from telethon.errors import ChatAdminRequiredError, FloodWaitError

import app.server as server_mod
from app.client import TelethonHolder
from app.config import Config
from app.tools import REGISTRY

AUTH_KEY = "correct-key"
NON_ASCII_KEY = "kéy-да"
WS_UNAUTHORIZED_CLOSE = 4401
FLOOD_SECONDS = 33
TOOL = "get_me"
TOOL_URL = "/api/me"
DOWNLOAD = "download_media"
DOWNLOAD_URL = "/api/messages/7/media?chat=somechat"
THROTTLE_HEADER_PREFIXES = ("x-throttle-", "x-ratelimit-")

MakeApp = Callable[..., Tuple[FastAPI, TelethonHolder]]


@pytest.fixture
def make_app(base_env: None, monkeypatch: pytest.MonkeyPatch) -> MakeApp:
    """build_app() over a holder that never connects to Telegram."""

    def make(**env: str) -> Tuple[FastAPI, TelethonHolder]:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        holders: list[TelethonHolder] = []

        class OfflineHolder(TelethonHolder):
            def __init__(self, cfg: Config) -> None:
                super().__init__(cfg)
                holders.append(self)

        monkeypatch.setattr(server_mod, "TelethonHolder", OfflineHolder)
        return server_mod.build_app(Config.from_env()), holders[0]

    return make


def _throttle_headers(headers: Any) -> Dict[str, str]:
    return {k: v for k, v in headers.items() if k.startswith(THROTTLE_HEADER_PREFIXES)}


def _bearer(token: str) -> list[tuple[bytes, bytes]]:
    """An Authorization header carrying the token as UTF-8 bytes, which is
    what a client sends for a non-ASCII value."""
    return [(b"Authorization", b"Bearer " + token.encode())]


# ---------- auth ---------------------------------------------------------------


def test_a_non_ascii_bearer_token_is_refused_not_a_server_error(make_app: MakeApp) -> None:
    """Regression: secrets.compare_digest(str, str) raises TypeError on
    non-ASCII input, which surfaced as a 500."""
    app, _ = make_app(TELETHON_AUTH_KEY=AUTH_KEY)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get(TOOL_URL, headers=_bearer("töken-да"))

    assert resp.status_code == 401


def test_a_non_ascii_auth_key_accepts_its_own_token(make_app: MakeApp) -> None:
    app, _ = make_app(TELETHON_AUTH_KEY=NON_ASCII_KEY)
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/api/throttle/status", headers=_bearer(NON_ASCII_KEY)).status_code == 200
    assert client.get("/api/throttle/status", headers=_bearer("kéy")).status_code == 401


def test_an_ascii_auth_key_still_works(make_app: MakeApp) -> None:
    app, _ = make_app(TELETHON_AUTH_KEY=AUTH_KEY)
    client = TestClient(app)

    assert client.get("/api/throttle/status", headers=_bearer(AUTH_KEY)).status_code == 200
    assert client.get("/api/throttle/status", headers=_bearer("wrong")).status_code == 401
    assert client.get("/api/throttle/status").status_code == 401
    assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize(
    ("auth_key", "token", "accepted"),
    [
        (AUTH_KEY, AUTH_KEY, True),
        (AUTH_KEY, "töken", False),
        (NON_ASCII_KEY, NON_ASCII_KEY, True),
        (NON_ASCII_KEY, "kéy", False),
    ],
)
def test_the_updates_socket_compares_the_token_as_bytes(
    make_app: MakeApp, auth_key: str, token: str, accepted: bool
) -> None:
    app, _ = make_app(TELETHON_AUTH_KEY=auth_key, TELETHON_UPDATES_ENABLED="true")
    client = TestClient(app)
    url = f"/ws/updates?token={quote(token)}"

    if accepted:
        with client.websocket_connect(url):
            return
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(url):
            pass
    assert closed.value.code == WS_UNAUTHORIZED_CLOSE


# ---------- middleware order -----------------------------------------------------


def test_an_unauthenticated_401_carries_no_throttle_headers(make_app: MakeApp) -> None:
    """Regression: the throttle-header middleware wrapped the auth check, so a
    401 disclosed the account's flood state."""
    app, _ = make_app(TELETHON_AUTH_KEY=AUTH_KEY)
    client = TestClient(app)

    resp = client.get("/api/throttle/status")

    assert resp.status_code == 401
    assert _throttle_headers(resp.headers) == {}


def test_an_authenticated_response_still_carries_the_throttle_headers(make_app: MakeApp) -> None:
    app, _ = make_app(TELETHON_AUTH_KEY=AUTH_KEY)
    client = TestClient(app)

    resp = client.get("/api/throttle/status", headers=_bearer(AUTH_KEY))

    assert resp.status_code == 200
    assert "x-throttle-multiplier" in resp.headers
    assert "x-ratelimit-remaining-read" in resp.headers


def test_a_read_only_refusal_still_carries_the_throttle_headers(make_app: MakeApp) -> None:
    app, _ = make_app(TELETHON_AUTH_KEY=AUTH_KEY, TELETHON_READ_ONLY="true")
    client = TestClient(app)

    resp = client.post("/api/messages", json={}, headers=_bearer(AUTH_KEY))

    assert resp.status_code == 403
    assert "x-throttle-multiplier" in resp.headers


# ---------- download Content-Disposition -------------------------------------------


def _download_info(filename: str) -> Dict[str, Any]:
    data = b"payload"
    return {
        "data": data,
        "size": len(data),
        "mime_type": "application/octet-stream",
        "filename": filename,
        "media_type": "MessageMediaDocument",
    }


def _serve_download(monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    async def fake(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return _download_info(filename)

    monkeypatch.setattr(server_mod, "download_media_bytes", fake)


def test_a_plain_filename_keeps_its_quoted_form(
    make_app: MakeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _ = make_app()
    _serve_download(monkeypatch, "report.pdf")

    resp = TestClient(app).get(DOWNLOAD_URL)

    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == (
        "attachment; filename=\"report.pdf\"; filename*=UTF-8''report.pdf"
    )


def test_a_non_latin1_filename_is_sent_not_a_server_error(
    make_app: MakeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the raw filename went into a Latin-1 header, so a Cyrillic
    or CJK name raised UnicodeEncodeError and the download returned 500."""
    name = "файл-文件.txt"
    app, _ = make_app()
    _serve_download(monkeypatch, name)

    resp = TestClient(app, raise_server_exceptions=False).get(DOWNLOAD_URL)

    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert disposition.isascii()
    assert f"filename*=UTF-8''{quote(name, safe='')}" in disposition
    assert 'filename="____-__.txt"' in disposition


def test_quotes_and_line_breaks_in_a_filename_cannot_inject_parameters(
    make_app: MakeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = 'a"; filename="evil.exe\r\nSet-Cookie: x=1\\.txt'
    app, _ = make_app()
    _serve_download(monkeypatch, name)

    resp = TestClient(app).get(DOWNLOAD_URL)

    disposition = resp.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    fallback = disposition.split('filename="', 1)[1].split('"; filename*=', 1)[0]
    assert '"' not in fallback and "\\" not in fallback
    assert disposition.count('filename="') == 1
    assert f"filename*=UTF-8''{quote(name, safe='')}" in disposition
    assert "set-cookie" not in resp.headers


# ---------- tool errors: counted and mapped the same on REST and MCP -----------------


def _raising(error: BaseException) -> Callable[..., Any]:
    async def handler(holder: Any, params: Any) -> Any:
        raise error

    return handler


def _fail_with(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    monkeypatch.setitem(
        REGISTRY, TOOL, dataclasses.replace(REGISTRY[TOOL], handler=_raising(error))
    )


def _flood() -> FloodWaitError:
    return FloodWaitError(request=None, capture=FLOOD_SECONDS)


def _errors(holder: TelethonHolder, tool: str = TOOL) -> int:
    return holder.metrics.snapshot()["tool_errors"].get(tool, 0)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (PermissionError("read-only mode"), 403),
        (ValueError("bad argument"), 400),
        (ChatAdminRequiredError(request=None), 502),
        (_flood(), 429),
        (RuntimeError("boom"), 500),
    ],
    ids=["permission", "value_error", "rpc_error", "flood_wait", "unexpected"],
)
def test_rest_counts_every_tool_error_and_maps_it(
    make_app: MakeApp,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    status: int,
) -> None:
    """Regression: REST skipped PermissionError and unexpected exceptions."""
    _fail_with(monkeypatch, error)
    app, holder = make_app()

    resp = TestClient(app, raise_server_exceptions=False).get(TOOL_URL)

    assert resp.status_code == status
    assert _errors(holder) == 1
    assert holder.metrics.snapshot()["tool_calls"][TOOL] == 1


def _mcp_answer(result: Any) -> Dict[str, Any]:
    """The JSON of an MCP refusal, which must be flagged as an error.
    Regression: refusals came back as plain results, which told the agent
    the call had worked."""
    assert isinstance(result, CallToolResult)
    assert result.isError is True
    (content,) = result.content
    answer: Dict[str, Any] = json.loads(content.text)
    return answer


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PermissionError("read-only mode"), {"error": "read_only"}),
        (ValueError("bad argument"), {"error": "invalid_argument"}),
        (
            ChatAdminRequiredError(request=None),
            {"error": "telegram_rpc", "type": "ChatAdminRequiredError"},
        ),
        (_flood(), {"error": "flood_wait", "seconds": FLOOD_SECONDS}),
    ],
    ids=["permission", "value_error", "rpc_error", "flood_wait"],
)
async def test_mcp_counts_every_tool_error_and_maps_it(
    base_env: None,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    expected: Dict[str, Any],
) -> None:
    _fail_with(monkeypatch, error)
    holder = TelethonHolder(Config.from_env())
    mcp = server_mod.build_mcp(holder, "127.0.0.1", 1)

    answer = _mcp_answer(await mcp.call_tool(TOOL, {}))

    assert expected.items() <= answer.items()
    assert _errors(holder) == 1


async def test_mcp_counts_an_unexpected_exception_and_still_raises_it(
    base_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_with(monkeypatch, RuntimeError("boom"))
    holder = TelethonHolder(Config.from_env())
    mcp = server_mod.build_mcp(holder, "127.0.0.1", 1)

    with pytest.raises(ToolError, match="boom"):
        await mcp.call_tool(TOOL, {})

    assert _errors(holder) == 1


# ---------- FloodWaitError as 429 -------------------------------------------------


def test_a_flood_wait_is_a_429_with_retry_after_and_the_seconds(
    make_app: MakeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: FloodWaitError reached REST callers as a 502."""
    _fail_with(monkeypatch, _flood())
    app, _ = make_app()

    resp = TestClient(app).get(TOOL_URL)

    assert resp.status_code == 429
    assert resp.headers["retry-after"] == str(FLOOD_SECONDS)
    assert resp.json()["detail"] == {
        "telegram_error": "FloodWaitError",
        "message": str(_flood()),
        "seconds": FLOOD_SECONDS,
    }


def test_another_rpc_error_stays_a_502_without_retry_after(
    make_app: MakeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_with(monkeypatch, ChatAdminRequiredError(request=None))
    app, _ = make_app()

    resp = TestClient(app).get(TOOL_URL)

    assert resp.status_code == 502
    assert resp.json()["detail"]["telegram_error"] == "ChatAdminRequiredError"
    assert "retry-after" not in resp.headers


async def test_an_mcp_flood_wait_reports_the_seconds_and_the_message(
    base_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_with(monkeypatch, _flood())
    holder = TelethonHolder(Config.from_env())
    mcp = server_mod.build_mcp(holder, "127.0.0.1", 1)

    answer = _mcp_answer(await mcp.call_tool(TOOL, {}))

    assert answer == {
        "error": "flood_wait",
        "seconds": FLOOD_SECONDS,
        "message": str(_flood()),
    }


# ---------- the download route counts and maps the same way ------------------------------


def _fail_download(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    async def fake(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        raise error

    monkeypatch.setattr(server_mod, "download_media_bytes", fake)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (PermissionError("read-only mode"), 403),
        (ValueError("message has no media attachment"), 400),
        (ChatAdminRequiredError(request=None), 502),
        (_flood(), 429),
        (RuntimeError("boom"), 500),
    ],
    ids=["permission", "value_error", "rpc_error", "flood_wait", "unexpected"],
)
def test_the_download_route_counts_every_error_and_maps_it(
    make_app: MakeApp,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    status: int,
) -> None:
    _fail_download(monkeypatch, error)
    app, holder = make_app()

    resp = TestClient(app, raise_server_exceptions=False).get(DOWNLOAD_URL)

    assert resp.status_code == status
    assert _errors(holder, DOWNLOAD) == 1
    assert holder.metrics.snapshot()["tool_calls"][DOWNLOAD] == 1


def test_a_successful_download_is_not_counted_as_an_error(
    make_app: MakeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve_download(monkeypatch, "ok.bin")
    app, holder = make_app()

    assert TestClient(app).get(DOWNLOAD_URL).status_code == 200
    assert _errors(holder, DOWNLOAD) == 0


def test_a_flood_wait_on_download_carries_retry_after(
    make_app: MakeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_download(monkeypatch, _flood())
    app, _ = make_app()

    resp = TestClient(app).get(DOWNLOAD_URL)

    assert resp.status_code == 429
    assert resp.headers["retry-after"] == str(FLOOD_SECONDS)


def test_a_successful_tool_call_is_counted_once_and_not_as_an_error(
    make_app: MakeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok(holder: Any, params: Any) -> Dict[str, Any]:
        return {"id": 1}

    monkeypatch.setitem(REGISTRY, TOOL, dataclasses.replace(REGISTRY[TOOL], handler=ok))
    app, holder = make_app()

    resp = TestClient(app).get(TOOL_URL)

    assert resp.json() == {"id": 1}
    snap = holder.metrics.snapshot()
    assert snap["tool_calls"] == {TOOL: 1}
    assert snap["tool_errors"] == {}
