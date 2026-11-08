"""Admin actions in the suite's own group. Acting on someone needs a second
account, so those tests run only with TEST_PEER_USER (a user or bot you
control) or TEST_BOT_TOKEN in tests/.env. The refusals run everywhere: the server rejects them
before calling Telegram."""

from __future__ import annotations

import httpx
import pytest
from conftest import ok


def _admin(http: httpx.Client, group: str, action: str, **body: object) -> httpx.Response:
    return http.post(f"/api/chats/{group}/admin/{action}", json=body)


def test_promote_with_no_right_is_refused(http: httpx.Client, group: str) -> None:
    """Telegram reads an empty set of rights as "not an admin", so this would
    demote instead of promote."""
    resp = _admin(http, group, "promote", user="me")
    assert resp.status_code == 400
    assert "at least one right" in resp.text


def test_a_channel_is_refused_as_the_user(http: httpx.Client, group: str) -> None:
    resp = _admin(http, group, "promote", user=group, change_info=True)
    assert resp.status_code == 400


def test_promote_demote_ban_unban_and_kick_a_peer(
    http: httpx.Client, env: dict[str, str], group: str, request: pytest.FixtureRequest
) -> None:
    peer = env["TEST_PEER_USER"]
    if not peer:
        peer = f"@{request.getfixturevalue('bot').username}"

    assert (
        ok(_admin(http, group, "promote", user=peer, change_info=True, title="tester"))["ok"]
        is True
    )
    admins = ok(http.get("/api/participants", params={"chat": group, "limit": 50}))
    peer_id = ok(http.get("/api/entities", params={"chat": peer}))["id"]
    assert peer_id in [m["id"] for m in admins]

    assert ok(_admin(http, group, "demote", user=peer))["ok"] is True
    assert ok(_admin(http, group, "ban", user=peer, until_seconds=120))["ok"] is True
    assert ok(_admin(http, group, "unban", user=peer))["ok"] is True
    assert ok(_admin(http, group, "kick", user=peer))["ok"] is True
