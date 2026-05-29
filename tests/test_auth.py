"""Auth middleware — Bearer token enforced on all endpoints except /healthz."""

from __future__ import annotations

import subprocess
from typing import Iterator

import httpx
import pytest

from conftest import IMAGE, CONTAINER, _free_port, _wait_ready, _stop_container


AUTH_KEY = "test-auth-key-do-not-use-in-prod"


def _start_authed_container(env: dict[str, str]) -> str:
    _stop_container()
    port = _free_port()
    subprocess.run(
        [
            "docker", "run", "-d", "--rm",
            "--name", CONTAINER,
            "-p", f"{port}:8080",
            "-e", f"TELETHON_API_ID={env['TELETHON_API_ID']}",
            "-e", f"TELETHON_API_HASH={env['TELETHON_API_HASH']}",
            "-e", f"TELETHON_SESSION={env['TELETHON_SESSION']}",
            "-e", f"TELETHON_AUTH_KEY={AUTH_KEY}",
            IMAGE,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    return f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def authed_url(env: dict[str, str]) -> Iterator[str]:
    url = _start_authed_container(env)
    try:
        _wait_ready(url)
        yield url
    finally:
        _stop_container()


def test_no_token_rejected(authed_url: str) -> None:
    resp = httpx.get(f"{authed_url}/api/me", timeout=10.0)
    assert resp.status_code == 401


def test_wrong_token_rejected(authed_url: str) -> None:
    resp = httpx.get(
        f"{authed_url}/api/me",
        headers={"Authorization": "Bearer wrong-key"},
        timeout=10.0,
    )
    assert resp.status_code == 401


def test_correct_token_accepted(authed_url: str) -> None:
    resp = httpx.get(
        f"{authed_url}/api/me",
        headers={"Authorization": f"Bearer {AUTH_KEY}"},
        timeout=10.0,
    )
    assert resp.status_code == 200
    body = resp.json()
    # Payload returned directly, no result wrapper.
    assert "id" in body
    assert "type" in body


def test_healthz_always_public(authed_url: str) -> None:
    resp = httpx.get(f"{authed_url}/healthz", timeout=10.0)
    assert resp.status_code == 200
    assert resp.json()["authorized"] is True


def test_mcp_no_token_rejected(authed_url: str) -> None:
    resp = httpx.post(
        f"{authed_url}/mcp/",
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}},
        timeout=10.0,
    )
    assert resp.status_code == 401


def test_mcp_correct_token_passes_through(authed_url: str) -> None:
    resp = httpx.post(
        f"{authed_url}/mcp/",
        headers={"Authorization": f"Bearer {AUTH_KEY}"},
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}},
        timeout=10.0,
    )
    assert resp.status_code != 401
