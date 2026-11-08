"""Live updates from the real account: the /ws/updates stream and the webhook.

What the server does itself raises no event: Telethon applies the answers to
its own requests without dispatching them. So the events here come from the
TEST_BOT_TOKEN bot, which messages the account through the Bot API, edits
that message and deletes it. Without a bot token the tests are skipped."""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, Iterator

import httpx
import pytest
from conftest import Bot, LocalServer, marker, ok
from websockets.sync.client import ClientConnection, connect

EVENT_TIMEOUT_SECONDS = 30.0


@pytest.fixture()
def updates(base_url: str) -> Iterator[ClientConnection]:
    url = base_url.replace("http://", "ws://") + "/ws/updates"
    with connect(url, open_timeout=10) as ws:
        yield ws


def _next_event(ws: ClientConnection, match: Callable[[Dict[str, Any]], bool]) -> Dict[str, Any]:
    deadline = time.monotonic() + EVENT_TIMEOUT_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        assert remaining > 0, "no matching event arrived"
        event = json.loads(ws.recv(timeout=remaining))
        if match(event):
            return event


def _has_message(message_id: int) -> Callable[[Dict[str, Any]], bool]:
    return lambda event: event.get("message", {}).get("id") == message_id


def _has_text(text: str) -> Callable[[Dict[str, Any]], bool]:
    # Each side of a private chat numbers its messages itself, so the id the
    # bot gets back is not the account's id for the message.
    return lambda event: event.get("message", {}).get("text") == text


def test_new_edited_and_deleted_messages_arrive_with_their_real_type(
    http: httpx.Client, bot: Bot, updates: ClientConnection
) -> None:
    """Regression: every event said "type": "Event"."""
    me = ok(http.get("/api/me"))
    text = marker("ws")
    sent = bot.call("sendMessage", chat_id=me["id"], text=text)
    try:
        new = _next_event(updates, _has_text(text))
        message_id = new["message"]["id"]
        assert new["type"] == "NewMessage"
        assert new["message"]["text"] == text
        assert new["message"]["out"] is False
        assert new["message"]["sender_id"] == bot.id

        bot.call(
            "editMessageText",
            chat_id=me["id"],
            message_id=sent["message_id"],
            text=text + " edited",
        )
        edited = _next_event(
            updates, lambda e: e.get("type") == "MessageEdited" and _has_message(message_id)(e)
        )
        assert edited["message"]["text"] == text + " edited"
    finally:
        bot.call("deleteMessage", chat_id=me["id"], message_id=sent["message_id"])

    deleted = _next_event(
        updates,
        lambda e: e.get("type") == "MessageDeleted" and message_id in e.get("deleted_ids", []),
    )
    assert deleted["type"] == "MessageDeleted"


def test_the_webhook_receives_new_messages(
    http: httpx.Client, bot: Bot, local: LocalServer
) -> None:
    me = ok(http.get("/api/me"))
    text = marker("webhook")
    sent = bot.call("sendMessage", chat_id=me["id"], text=text)
    try:
        hook = local.wait_for_hook(_has_text(text))
        assert hook["type"] == "NewMessage"
        assert hook["message"]["text"] == text
    finally:
        bot.call("deleteMessage", chat_id=me["id"], message_id=sent["message_id"])


def test_what_the_server_sends_itself_raises_no_event(
    http: httpx.Client, env: dict[str, str], updates: ClientConnection
) -> None:
    """The documented limit: a message sent through telethon-plus is not echoed
    back as an event."""
    chat = env["TEST_CHAT"]
    sent = ok(
        http.post("/api/messages", json={"chat": chat, "text": marker("echo"), "silent": True})
    )
    try:
        with pytest.raises(TimeoutError):
            while True:
                event = json.loads(updates.recv(timeout=5))
                assert not _has_message(sent["id"])(event), event
    finally:
        http.request("DELETE", "/api/messages", json={"chat": chat, "message_ids": [sent["id"]]})
