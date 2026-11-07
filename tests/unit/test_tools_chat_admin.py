"""Channel-only tools, kick, promote and create_group, run through Telethon's
real request pipeline (see test_tools_support.make_wire)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from telethon.extensions import BinaryReader
from telethon.tl.types import InputUserSelf, Updates

from .test_tools_support import (
    NOW,
    empty_updates,
    entity_table,
    make_basic_group,
    make_supergroup,
    make_user,
    make_wire,
)

CHAT_REF = "1"
USER_REF = "2"

# (tool, extra params) for every tool whose request takes an InputChannel.
CHANNEL_ONLY_TOOLS: List[Tuple[str, Dict[str, Any]]] = [
    ("join_chat", {}),
    ("leave_chat", {}),
    ("delete_chat", {}),
    ("get_linked_chat", {}),
    ("ban_user", {"user": USER_REF}),
    ("unban_user", {"user": USER_REF}),
    ("kick_user", {"user": USER_REF}),
    ("promote_user", {"user": USER_REF, "ban_users": True}),
    ("demote_user", {"user": USER_REF}),
]

# Every channel-only tool against a user and a basic group, except leave_chat
# on a basic group, which it supports.
REJECTED_COMBINATIONS = [
    pytest.param(name, extra, entity, id=f"{name}-{kind}")
    for name, extra in CHANNEL_ONLY_TOOLS
    for kind, entity in (("user", make_user()), ("basic_group", make_basic_group()))
    if not (name == "leave_chat" and kind == "basic_group")
]

REQUEST_OF_TOOL = {
    "join_chat": "JoinChannelRequest",
    "leave_chat": "LeaveChannelRequest",
    "delete_chat": "DeleteChannelRequest",
    "get_linked_chat": "GetFullChannelRequest",
    "ban_user": "EditBannedRequest",
    "unban_user": "EditBannedRequest",
    "kick_user": "EditBannedRequest",
    "promote_user": "EditAdminRequest",
    "demote_user": "EditAdminRequest",
}


def _tool(name: str) -> Any:
    from app.tools import REGISTRY

    return REGISTRY[name]


def _params(name: str, extra: Dict[str, Any]) -> Any:
    return _tool(name).params_model.model_validate({"chat": CHAT_REF, **extra})


def _answer(request: Any) -> Any:
    """GetFullChannel needs a full_chat to read; everything else just an ack."""
    if type(request).__name__ == "GetFullChannelRequest":
        return SimpleNamespace(full_chat=SimpleNamespace(linked_chat_id=None), chats=[])
    return empty_updates()


def _holder_on_wire(holder: Any, chat: Any) -> Any:
    wire = make_wire(_answer)
    wire.client.get_entity = AsyncMock(
        side_effect=entity_table({CHAT_REF: chat, USER_REF: make_user()})
    )
    holder._client = wire.client
    return wire


def _roundtrip(request: Any) -> Any:
    return BinaryReader(request._bytes()).tgread_object()


@pytest.fixture
def dry_run_holder(base_env: None, monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.client import TelethonHolder
    from app.config import Config

    monkeypatch.setenv("TELETHON_DRY_RUN", "true")
    return TelethonHolder(Config.from_env())


# ---------- channel-only tools ----------------------------------------------


@pytest.mark.parametrize("name,extra,entity", REJECTED_COMBINATIONS)
async def test_channel_only_tool_rejects_a_user_and_a_basic_group(
    holder, name, extra, entity
) -> None:
    """Regression: Telethon raised TypeError "Cannot cast ... to any kind of
    InputChannel", which the server reported as a 500."""
    wire = _holder_on_wire(holder, entity)

    with pytest.raises(ValueError, match="channel or supergroup"):
        await _tool(name).handler(holder, _params(name, extra))

    assert wire.sent == []


async def test_a_channel_only_tool_checks_the_chat_in_dry_run_too(dry_run_holder) -> None:
    dry_run_holder._client = AsyncMock()
    dry_run_holder._client.get_entity = AsyncMock(return_value=make_user())

    with pytest.raises(ValueError, match="channel or supergroup"):
        await _tool("join_chat").handler(dry_run_holder, _params("join_chat", {}))


@pytest.mark.parametrize("name,extra", CHANNEL_ONLY_TOOLS, ids=[n for n, _ in CHANNEL_ONLY_TOOLS])
async def test_channel_only_tool_still_works_on_a_supergroup(holder, name, extra) -> None:
    wire = _holder_on_wire(holder, make_supergroup())

    await _tool(name).handler(holder, _params(name, extra))

    assert {type(r).__name__ for r in wire.sent} == {REQUEST_OF_TOOL[name]}


async def test_leave_chat_on_a_basic_group_removes_yourself_from_it(holder) -> None:
    wire = _holder_on_wire(holder, make_basic_group(eid=7))

    result = await _tool("leave_chat").handler(holder, _params("leave_chat", {}))

    assert result == {"ok": True}
    assert len(wire.sent) == 1
    request = wire.sent[0]
    assert type(request).__name__ == "DeleteChatUserRequest"
    assert request.chat_id == 7
    assert isinstance(_roundtrip(request).user_id, InputUserSelf)


# ---------- kick_user -------------------------------------------------------


async def test_kick_user_bans_then_unbans(holder) -> None:
    wire = _holder_on_wire(holder, make_supergroup())

    await _tool("kick_user").handler(holder, _params("kick_user", {"user": USER_REF}))

    ban, unban = (_roundtrip(r).banned_rights for r in wire.sent)
    assert ban.view_messages
    assert not unban.view_messages


async def test_kick_user_charges_each_of_its_two_requests(holder) -> None:
    """Regression: both EditBanned requests ran under one guard, so the bucket
    saw a single slot used."""
    wire = _holder_on_wire(holder, make_supergroup())
    holder.cache.put(CHAT_REF, make_supergroup())
    used_before = holder.throttle.snapshot()["buckets"]["other"]["used"]

    await _tool("kick_user").handler(holder, _params("kick_user", {"user": USER_REF}))

    used_after = holder.throttle.snapshot()["buckets"]["other"]["used"]
    resolutions = wire.client.get_entity.await_count
    assert len(wire.sent) == 2
    assert used_after - used_before == resolutions + len(wire.sent)


# ---------- promote_user ----------------------------------------------------


NO_RIGHTS = [
    "change_info",
    "post_messages",
    "edit_messages",
    "delete_messages",
    "ban_users",
    "invite_users",
    "pin_messages",
    "add_admins",
    "anonymous",
    "manage_call",
]


@pytest.mark.parametrize("dry_run", [False, True])
async def test_promote_user_with_no_right_granted_is_rejected(
    holder, dry_run_holder, dry_run
) -> None:
    """Regression: an empty ChatAdminRights is how Telegram removes an admin,
    so promoting with every right false demoted the user."""
    target = dry_run_holder if dry_run else holder
    target._client = AsyncMock()
    target._client.get_entity = AsyncMock(return_value=make_supergroup())

    with pytest.raises(ValueError, match="at least one right"):
        await _tool("promote_user").handler(
            target, _params("promote_user", {"user": USER_REF, "title": "boss"})
        )

    target._client.assert_not_awaited()


@pytest.mark.parametrize("right", NO_RIGHTS)
async def test_promote_user_sends_the_granted_right_and_only_that_one(holder, right) -> None:
    wire = _holder_on_wire(holder, make_supergroup())

    await _tool("promote_user").handler(
        holder, _params("promote_user", {"user": USER_REF, right: True, "title": "boss"})
    )

    sent = _roundtrip(wire.sent[0])
    granted = {name for name in NO_RIGHTS if getattr(sent.admin_rights, name)}
    assert granted == {right}
    assert sent.rank == "boss"


# ---------- create_group ----------------------------------------------------


@pytest.mark.parametrize(
    "megagroup,broadcast", [(True, False), (False, True)], ids=["supergroup", "broadcast"]
)
async def test_create_group_sets_exactly_one_of_broadcast_and_megagroup(
    holder, megagroup, broadcast
) -> None:
    """Regression: megagroup=false sent neither flag, which is not a kind of
    channel Telegram can create."""
    created = Updates(updates=[], users=[], chats=[make_supergroup()], date=NOW, seq=0)
    wire = make_wire(lambda request: created)
    holder._client = wire.client

    await _tool("create_group").handler(
        holder,
        _tool("create_group").params_model.model_validate({"title": "new", "megagroup": megagroup}),
    )

    sent = _roundtrip(wire.sent[0])
    assert bool(sent.megagroup) is megagroup
    assert bool(sent.broadcast) is broadcast


def test_create_group_title_is_capped_at_what_telegram_accepts() -> None:
    model = _tool("create_group").params_model
    model.model_validate({"title": "t" * 128})
    with pytest.raises(ValidationError):
        model.model_validate({"title": "t" * 129})
