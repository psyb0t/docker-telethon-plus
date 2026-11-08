"""Messages on the real account: send, read, edit, reply, forward, pin, read
marks and reactions. Everything is sent to TEST_CHAT (Saved Messages by
default) or the suite's own group, and deleted afterwards."""

from __future__ import annotations

from typing import Callable

import httpx
from conftest import marker, ok

Cleanup = Callable[[str, int], None]


def _send(http: httpx.Client, cleanup: Cleanup, chat: str, text: str, **fields: object) -> dict:
    sent = ok(
        http.post("/api/messages", json={"chat": chat, "text": text, "silent": True, **fields})
    )
    cleanup(chat, sent["id"])
    return sent


def test_markdown_is_rendered_and_get_message_returns_it(
    http: httpx.Client, env: dict[str, str], cleanup: Cleanup
) -> None:
    chat = env["TEST_CHAT"]
    text = marker("markdown")
    sent = _send(http, cleanup, chat, f"**{text}**", parse_mode="markdown")
    assert sent["text"] == text
    assert sent["out"] is True

    fetched = ok(http.get(f"/api/messages/{sent['id']}", params={"chat": chat}))
    assert fetched["id"] == sent["id"]
    assert fetched["text"] == text


def test_a_reply_points_at_the_message_it_answers(
    http: httpx.Client, env: dict[str, str], cleanup: Cleanup
) -> None:
    chat = env["TEST_CHAT"]
    first = _send(http, cleanup, chat, marker("reply-target"))
    reply = _send(http, cleanup, chat, marker("reply"), reply_to=first["id"])

    fetched = ok(http.get(f"/api/messages/{reply['id']}", params={"chat": chat}))
    assert fetched["reply_to_msg_id"] == first["id"]


def test_search_and_offset_find_the_right_messages(
    http: httpx.Client, env: dict[str, str], cleanup: Cleanup
) -> None:
    chat = env["TEST_CHAT"]
    word = marker("search").split()[-1]
    older = _send(http, cleanup, chat, f"older {word}")
    newer = _send(http, cleanup, chat, f"newer {word}")

    found = ok(http.get("/api/messages", params={"chat": chat, "search": word, "limit": 10}))
    assert [m["id"] for m in found] == [newer["id"], older["id"]]

    before_newer = ok(
        http.get("/api/messages", params={"chat": chat, "limit": 1, "offset_id": newer["id"]})
    )
    assert before_newer[0]["id"] < newer["id"]


def test_edit_changes_the_text(http: httpx.Client, env: dict[str, str], cleanup: Cleanup) -> None:
    chat = env["TEST_CHAT"]
    sent = _send(http, cleanup, chat, marker("edit"))

    edited = ok(
        http.patch(f"/api/messages/{sent['id']}", json={"chat": chat, "text": "edited text"})
    )
    assert edited["text"] == "edited text"
    fetched = ok(http.get(f"/api/messages/{sent['id']}", params={"chat": chat}))
    assert fetched["text"] == "edited text"


def test_a_forward_carries_its_origin_as_a_number(
    http: httpx.Client, env: dict[str, str], group: str, cleanup: Cleanup
) -> None:
    """A channel post forwarded into the suite's group. Telegram puts no
    forward header on a forward of the account's own message."""
    channel = env["TEST_PUBLIC_CHANNEL"]
    (post,) = ok(http.get("/api/messages", params={"chat": channel, "limit": 1}))
    source = ok(http.get("/api/entities", params={"chat": channel}))

    forwarded = ok(
        http.post(
            "/api/messages/forward",
            json={"from_chat": channel, "to_chat": group, "message_ids": [post["id"]]},
        )
    )
    assert len(forwarded) == 1
    copy = forwarded[0]
    cleanup(group, copy["id"])
    assert copy["fwd_from"] is not None
    # The marked id Telethon uses for a channel: -100 followed by its id.
    assert copy["fwd_from"]["from_id"] == int(f"-100{source['id']}")
    assert copy["fwd_from"]["channel_post"] == post["id"]


def test_delete_removes_the_message(http: httpx.Client, env: dict[str, str]) -> None:
    chat = env["TEST_CHAT"]
    sent = ok(
        http.post("/api/messages", json={"chat": chat, "text": marker("delete"), "silent": True})
    )

    deleted = ok(
        http.request("DELETE", "/api/messages", json={"chat": chat, "message_ids": [sent["id"]]})
    )
    assert deleted["requested"] == 1
    gone = http.get(f"/api/messages/{sent['id']}", params={"chat": chat})
    assert gone.status_code == 400
    assert "not found" in gone.text


def test_mark_read_answers_ok(http: httpx.Client, env: dict[str, str]) -> None:
    assert ok(http.post("/api/messages/read", json={"chat": env["TEST_CHAT"]}))["ok"] is True


def test_pin_and_unpin(http: httpx.Client, group: str, cleanup: Cleanup) -> None:
    sent = _send(http, cleanup, group, marker("pin"))

    assert ok(http.post(f"/api/messages/{sent['id']}/pin", json={"chat": group}))["ok"] is True
    assert ok(http.post(f"/api/messages/{sent['id']}/unpin", json={"chat": group}))["ok"] is True


def test_set_and_remove_a_reaction(http: httpx.Client, group: str, cleanup: Cleanup) -> None:
    sent = _send(http, cleanup, group, marker("reaction"))

    reacted = http.post(
        f"/api/messages/{sent['id']}/reactions", json={"chat": group, "emoji": "👍"}
    )
    assert ok(reacted)["ok"] is True
    removed = http.request("DELETE", f"/api/messages/{sent['id']}/reactions", json={"chat": group})
    assert ok(removed)["ok"] is True
