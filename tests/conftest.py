"""Test fixtures: build image, run container, expose base URL."""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Iterator

import httpx
import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "psyb0t/telethon-plus:latest-test"
CONTAINER = "docker-telethon-plus-tests"


def _load_env() -> dict[str, str]:
    # Root .env is the base; tests/.env overlays on top (tests-specific overrides).
    root_env = ROOT / ".env"
    tests_env = ROOT / "tests" / ".env"

    if not root_env.exists() and not tests_env.exists():
        pytest.skip(
            ".env not found — copy .env.example to .env and fill it in",
            allow_module_level=True,
        )

    values: dict[str, str] = {}
    for path in (root_env, tests_env):
        if path.exists():
            values.update({k: v for k, v in dotenv_values(path).items() if v is not None})

    for required in ("TELETHON_API_ID", "TELETHON_API_HASH", "TELETHON_SESSION"):
        if not values.get(required):
            pytest.skip(
                f"{required} missing — set it in .env or tests/.env",
                allow_module_level=True,
            )
    values.setdefault("TEST_CHAT", "me")
    return values


def _build_image() -> None:
    subprocess.run(
        ["docker", "build", "-t", IMAGE, str(ROOT)],
        check=True,
    )


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


def _start_container(env: dict[str, str]) -> str:
    _stop_container()
    port = _free_port()
    cmd = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        CONTAINER,
        "-p",
        f"{port}:8080",
        "-e",
        f"TELETHON_API_ID={env['TELETHON_API_ID']}",
        "-e",
        f"TELETHON_API_HASH={env['TELETHON_API_HASH']}",
        "-e",
        f"TELETHON_SESSION={env['TELETHON_SESSION']}",
        "-e",
        f"TELETHON_LOG_LEVEL={env.get('TELETHON_LOG_LEVEL', 'INFO')}",
        IMAGE,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    return f"http://127.0.0.1:{port}"


def _wait_ready(base_url: str, timeout: float = 60.0) -> None:
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

    logs = subprocess.run(
        ["docker", "logs", CONTAINER],
        capture_output=True,
        text=True,
        check=False,
    )
    raise RuntimeError(
        f"container never became ready: {last_err}\n"
        f"--- container logs ---\n{logs.stdout}\n{logs.stderr}"
    )


@pytest.fixture(scope="session")
def env() -> dict[str, str]:
    return _load_env()


@pytest.fixture(scope="session")
def base_url(env: dict[str, str]) -> Iterator[str]:
    if not os.environ.get("TELETHON_TESTS_SKIP_BUILD"):
        _build_image()
    url = _start_container(env)
    try:
        _wait_ready(url)
        yield url
    finally:
        _stop_container()


@pytest.fixture()
def http(base_url: str) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        yield client
