"""How message objects are reported: replies to stories and forward origins."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.tl.types import (
    Message,
    MessageFwdHeader,
    MessageReplyHeader,
    MessageReplyStoryHeader,
    PeerChannel,
    PeerChat,
    PeerUser,
)

from .test_tools_support import NOW

STORY_REPLY = MessageReplyStoryHeader(peer=PeerUser(2), story_id=3)


def _message(mid: int, **fields: Any) -> Message:
    return Message(id=mid, peer_id=PeerUser(5), date=NOW, message="hi", **fields)


async def test_a_reply_to_a_story_does_not_break_a_page_of_messages(holder, make_entity) -> None:
    """Regression: a story reply header has no reply_to_msg_id, so reading
    it raised AttributeError and failed the whole get_messages page."""
    from app.tools import REGISTRY

    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(
        return_value=[
            _message(3, reply_to=MessageReplyHeader(reply_to_msg_id=42)),
            _message(2, reply_to=STORY_REPLY),
            _message(1),
        ]
    )
    params = REGISTRY["get_messages"].params_model.model_validate({"chat": "1", "limit": 10})

    result = await REGISTRY["get_messages"].handler(holder, params)

    assert [m["reply_to_msg_id"] for m in result] == [42, None, None]
    json.dumps(result)


async def test_a_reply_to_a_story_does_not_break_a_page_of_dialogs(holder, make_entity) -> None:
    from app.tools import REGISTRY

    dialog = MagicMock()
    dialog.entity = make_entity(1)
    dialog.unread_count = 0
    dialog.pinned = False
    dialog.message = _message(2, reply_to=STORY_REPLY)
    holder._client.get_dialogs = AsyncMock(return_value=[dialog])
    params = REGISTRY["get_dialogs"].params_model.model_validate({"limit": 10})

    result = await REGISTRY["get_dialogs"].handler(holder, params)

    assert result[0]["last_message"]["reply_to_msg_id"] is None


@pytest.mark.parametrize(
    "origin,expected",
    [
        (PeerUser(5), 5),
        (PeerChat(9), -9),
        (PeerChannel(7), -1000000000007),
        (None, None),
    ],
    ids=["user", "basic_group", "channel", "hidden_sender"],
)
async def test_a_forward_reports_the_origin_as_a_peer_id_or_null(
    holder, make_entity, origin, expected
) -> None:
    """Regression: the origin came out as str(PeerUser(...)) when known and
    as the string "None" when the sender was hidden."""
    from app.tools import REGISTRY

    forwarded = _message(
        4, fwd_from=MessageFwdHeader(date=NOW, from_id=origin, from_name="Someone")
    )
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(return_value=[forwarded])
    params = REGISTRY["get_message"].params_model.model_validate({"chat": "1", "message_id": 4})

    result = await REGISTRY["get_message"].handler(holder, params)

    assert result["fwd_from"]["from_id"] == expected
    assert result["fwd_from"]["from_name"] == "Someone"
    json.dumps(result)


def _dialog(name: str, username: str | None, entity_id: int) -> Any:
    from types import SimpleNamespace

    entity = SimpleNamespace(id=entity_id, username=username, title=name)
    return SimpleNamespace(name=name, entity=entity, unread_count=0, pinned=False, message=None)


async def test_dialog_search_matches_title_or_username_and_stops_at_the_limit(holder: Any) -> None:
    from app.tools import REGISTRY

    dialogs = [
        _dialog("Weather", None, 1),
        _dialog("Trading Room", "tradingroom", 2),
        _dialog("Other", "trade_alerts", 3),
        _dialog("Trade News", None, 4),
    ]

    def iter_dialogs(**kwargs: Any) -> Any:
        async def gen() -> Any:
            for d in dialogs:
                yield d

        return gen()

    holder._client.iter_dialogs = iter_dialogs
    params = REGISTRY["get_dialogs"].params_model.model_validate({"search": "TRAD", "limit": 2})
    found = await REGISTRY["get_dialogs"].handler(holder, params)
    assert [d["id"] for d in found] == [2, 3]


async def test_dialog_search_charges_a_read_slot_per_page_scanned(holder: Any) -> None:
    from app.tools import REGISTRY

    dialogs = [_dialog(f"chat {i}", None, i) for i in range(250)]

    def iter_dialogs(**kwargs: Any) -> Any:
        async def gen() -> Any:
            for d in dialogs:
                yield d

        return gen()

    holder._client.iter_dialogs = iter_dialogs
    charged: list[tuple[str, int]] = []
    holder.throttle.record_extra = lambda bucket, count: charged.append((bucket, count))
    params = REGISTRY["get_dialogs"].params_model.model_validate({"search": "nomatch"})
    assert await REGISTRY["get_dialogs"].handler(holder, params) == []
    # 250 dialogs are 3 pages of 100; the guard already charged the first.
    assert charged == [("read", 2)]
