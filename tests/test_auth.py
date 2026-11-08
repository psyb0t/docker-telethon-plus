"""Auth: the bearer token guards every route except /healthz and /metrics."""

from __future__ import annotations

from typing import Iterator

import httpx
import pytest
from conftest import Server
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

AUTH_KEY = "test-auth-key-do-not-use-in-prod"


@pytest.fixture()
def authed_url(server: Server) -> Iterator[str]:
    yield server.ensure(TELETHON_AUTH_KEY=AUTH_KEY)


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


def test_the_updates_socket_needs_the_token(authed_url: str) -> None:
    ws_url = authed_url.replace("http://", "ws://") + "/ws/updates"
    with pytest.raises(InvalidStatus) as refused:
        with connect(f"{ws_url}?token=wrong-key", open_timeout=10):
            pass
    assert refused.value.response.status_code == 403
    with connect(f"{ws_url}?token={AUTH_KEY}", open_timeout=10):
        pass


def test_metrics_stay_public(authed_url: str) -> None:
    resp = httpx.get(f"{authed_url}/metrics", timeout=10.0)
    assert resp.status_code == 200
    assert "telethon" in resp.text
