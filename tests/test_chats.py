"""Chats on the real account: lookups, dialogs, groups and channels the suite
creates, members, and joining. Joining touches chats the suite does not own,
so those tests run only when tests/.env names the chat."""

from __future__ import annotations

from typing import Iterator

import httpx
import pytest
from conftest import TEST_GROUP_PREFIX, Server, ok, require


def test_get_entity_resolves_the_account_and_a_public_channel(
    http: httpx.Client, env: dict[str, str]
) -> None:
    me = ok(http.get("/api/me"))
    assert ok(http.get("/api/entities", params={"chat": "me"}))["id"] == me["id"]

    channel = ok(http.get("/api/entities", params={"chat": env["TEST_PUBLIC_CHANNEL"]}))
    assert channel["type"] == "Channel"
    assert isinstance(channel["id"], int)


def test_bulk_resolve_reports_hits_and_misses_separately(
    http: httpx.Client, env: dict[str, str]
) -> None:
    body = ok(
        http.post(
            "/api/entities/bulk",
            json={
                "chats": [
                    "me",
                    env["TEST_PUBLIC_CHANNEL"],
                    "@this_username_should_not_exist_xyzzy_42",
                ]
            },
        )
    )
    assert body["count"] == 2
    assert body["failed"] == 1
    assert len(body["resolved"]) == 2


def test_dialogs_take_limit_and_archived(http: httpx.Client) -> None:
    dialogs = ok(http.get("/api/dialogs", params={"limit": 3}))
    assert len(dialogs) <= 3
    assert isinstance(ok(http.get("/api/dialogs", params={"limit": 3, "archived": "true"})), list)


def test_the_suite_group_is_a_supergroup_with_the_account_in_it(
    http: httpx.Client, group: str
) -> None:
    entity = ok(http.get("/api/entities", params={"chat": group}))
    assert entity["type"] == "Channel"
    assert entity["title"].startswith(TEST_GROUP_PREFIX)

    me = ok(http.get("/api/me"))
    members = ok(http.get("/api/participants", params={"chat": group, "limit": 10}))
    assert me["id"] in [m["id"] for m in members]
    if me.get("first_name"):
        searched = ok(
            http.get("/api/participants", params={"chat": group, "search": me["first_name"]})
        )
        assert me["id"] in [m["id"] for m in searched]


@pytest.fixture(scope="module")
def channel(server: Server) -> Iterator[dict]:
    """One broadcast channel for the module: Telegram limits how often an
    account may create channels."""
    with httpx.Client(base_url=server.ensure(), timeout=120.0) as http:
        created = ok(
            http.post(
                "/api/chats", json={"title": f"{TEST_GROUP_PREFIX}channel", "megagroup": False}
            )
        )
    try:
        yield created
    finally:
        with httpx.Client(base_url=server.ensure(), timeout=120.0) as http:
            http.request("DELETE", "/api/chats", json={"chat": str(created["id"])})


def test_megagroup_false_creates_a_broadcast_channel(http: httpx.Client, channel: dict) -> None:
    assert channel["type"] == "Channel"
    assert channel["title"] == f"{TEST_GROUP_PREFIX}channel"
    sent = ok(
        http.post("/api/messages", json={"chat": str(channel["id"]), "text": "a channel post"})
    )
    assert sent["views"] is not None


def test_a_new_channel_has_no_linked_chat(http: httpx.Client, channel: dict) -> None:
    linked = ok(http.get(f"/api/chats/{channel['id']}/linked"))
    assert linked == {"linked_chat_id": None, "linked_entity": None}


def test_create_and_delete_a_group(http: httpx.Client) -> None:
    created = ok(http.post("/api/chats", json={"title": f"{TEST_GROUP_PREFIX}delete"}))
    chat = str(created["id"])

    assert ok(http.request("DELETE", "/api/chats", json={"chat": chat}))["ok"] is True
    dialogs = ok(http.get("/api/dialogs", params={"search": f"{TEST_GROUP_PREFIX}delete"}))
    assert created["id"] not in [d["id"] for d in dialogs]


def _is_member(http: httpx.Client, entity: dict) -> bool:
    """Whether the chat is among the account's dialogs, searched by name so an
    account with more dialogs than one page still finds it."""
    query = entity.get("username") or entity["title"]
    for archived in ("false", "true"):
        dialogs = ok(
            http.get("/api/dialogs", params={"search": query, "limit": 200, "archived": archived})
        )
        if entity["id"] in [d["id"] for d in dialogs]:
            return True
    return False


def test_join_and_leave_a_public_chat(http: httpx.Client, env: dict[str, str]) -> None:
    chat = require(env, "TEST_JOIN_CHAT")
    target = ok(http.get("/api/entities", params={"chat": chat}))
    if _is_member(http, target):
        pytest.skip(f"the account is already in {chat}; leaving it would undo your membership")

    assert ok(http.post("/api/chats/join", json={"chat": chat}))["ok"] is True
    try:
        assert _is_member(http, target)
    finally:
        assert ok(http.post("/api/chats/leave", json={"chat": chat}))["ok"] is True
    assert not _is_member(http, target)


def test_join_via_invite_and_leave(http: httpx.Client, env: dict[str, str]) -> None:
    """If the account is already in the chat, Telegram refuses the join and the
    test fails before it leaves anything."""
    invite = require(env, "TEST_INVITE_LINK")
    joined = ok(http.post("/api/chats/invite", json={"invite": invite}))
    assert joined["ok"] is True
    (chat,) = joined["joined"]
    try:
        assert _is_member(http, chat)
    finally:
        ok(http.post("/api/chats/leave", json={"chat": str(chat["id"])}))
    assert not _is_member(http, chat)
