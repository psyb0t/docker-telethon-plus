"""Properties every tool must have, checked across the whole registry so a
tool added later is covered without anyone remembering to:

- the set of tools that change something is exactly WRITE_TOOLS;
- each of them refuses under TELETHON_READ_ONLY before touching Telegram;
- each of them answers {"dry_run": true, ...} under TELETHON_DRY_RUN and
  sends nothing;
- every tool runs end to end against a mocked client with typical input.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.tl.types import Channel, User

WRITE_TOOLS = {
    "send_message",
    "send_file",
    "forward_messages",
    "delete_messages",
    "edit_message",
    "mark_read",
    "create_group",
    "delete_chat",
    "join_chat",
    "leave_chat",
    "join_via_invite",
    "set_reaction",
    "remove_reaction",
    "pin_message",
    "unpin_message",
    "ban_user",
    "unban_user",
    "kick_user",
    "promote_user",
    "demote_user",
    "create_poll",
    "vote_poll",
}

# Typical arguments per field name; every required field of every tool is here.
SAMPLE = {
    "chat": "@testgroup",
    "from_chat": "@testgroup",
    "to_chat": "@othergroup",
    "user": "@testuser",
    "text": "hello",
    "message_id": 7,
    "message_ids": [7],
    "file_url": "https://files.example.invalid/a.png",
    "title": "test group",
    "chats": ["@testgroup"],
    "emoji": "x",
    "invite": "https://t.me/+abcdef",
    "question": "q?",
    "options": ["a", "b"],
}
OVERRIDES = {
    "vote_poll": {"options": [0]},
    "promote_user": {"pin_messages": True},
}

# Client methods and calls that change something on Telegram.
WRITE_METHODS = (
    "send_message",
    "send_file",
    "forward_messages",
    "delete_messages",
    "edit_message",
    "send_read_acknowledge",
    "pin_message",
    "unpin_message",
)


def _channel() -> Channel:
    return Channel(
        id=1001, title="test group", photo=None, date=None, access_hash=1, megagroup=True
    )


def _user() -> User:
    return User(id=42, access_hash=2, username="testuser")


def _entity(ref: Any) -> Any:
    return _user() if "user" in str(ref) else _channel()


def _params(name: str) -> Any:
    from app.tools import REGISTRY

    model = REGISTRY[name].params_model
    body = {
        field: SAMPLE[field] for field, info in model.model_fields.items() if info.is_required()
    }
    body.update(OVERRIDES.get(name, {}))
    return model.model_validate(body)


async def _holder(monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    from app.client import TelethonHolder
    from app.config import Config

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    holder = TelethonHolder(Config.from_env())
    client = AsyncMock()
    client.get_entity = AsyncMock(side_effect=_entity)
    client.get_input_entity = AsyncMock(side_effect=_entity)
    holder._client = client
    return holder


def _wrote(client: Any) -> list[str]:
    """Names of the write calls the mocked client received."""
    calls = [name for name in WRITE_METHODS if getattr(client, name).await_count]
    if client.await_count:  # client(SomeRequest(...))
        calls.append("raw request")
    return calls


def test_the_write_tools_are_exactly_the_ones_that_check_read_only() -> None:
    import inspect

    from app.tools import REGISTRY

    checking = {
        name
        for name, tool in REGISTRY.items()
        if "assert_writeable" in inspect.getsource(tool.handler)
    }
    assert checking == WRITE_TOOLS


@pytest.mark.parametrize("name", sorted(WRITE_TOOLS))
async def test_read_only_refuses_every_write_before_telegram(
    base_env: None, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    from app.tools import REGISTRY

    holder = await _holder(monkeypatch, TELETHON_READ_ONLY="true")
    with pytest.raises(PermissionError, match="read-only"):
        await REGISTRY[name].handler(holder, _params(name))
    assert _wrote(holder._client) == []


@pytest.mark.parametrize("name", sorted(WRITE_TOOLS))
async def test_dry_run_answers_and_sends_nothing(
    base_env: None, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    from app.tools import REGISTRY

    holder = await _holder(monkeypatch, TELETHON_DRY_RUN="true")
    _prepare_reads(holder._client, name)
    result = await REGISTRY[name].handler(holder, _params(name))
    # forward_messages answers with a list, like its real result.
    answer = result[0] if isinstance(result, list) else result
    assert answer["dry_run"] is True
    assert _wrote(holder._client) == []


def _poll_message() -> Any:
    answer_a, answer_b = MagicMock(option=b"\x10"), MagicMock(option=b"\x20")
    message = MagicMock()
    message.poll.poll.answers = [answer_a, answer_b]
    message.poll.poll.question.text = "q?"
    message.poll.results.results = []
    message.poll.results.total_voters = 0
    return message


def _prepare_reads(client: Any, name: str) -> None:
    """Return values for the reads some tools make before writing."""
    if name in ("vote_poll", "get_poll_results"):
        # get_messages(ids=[...]) returns a list.
        client.get_messages = AsyncMock(return_value=[_poll_message()])


@pytest.mark.parametrize(
    "name",
    sorted(
        set(__import__("app.tools", fromlist=["REGISTRY"]).REGISTRY)
        - {"send_file", "download_media", "get_participants", "get_messages", "get_dialogs"}
    ),
)
async def test_every_tool_runs_against_a_mocked_client(
    base_env: None, monkeypatch: pytest.MonkeyPatch, name: str, make_message: Any
) -> None:
    """send_file, download_media, get_participants, get_messages and
    get_dialogs need real transfer or paging fakes and have their own test
    files; this covers every other tool's normal path."""
    from app.tools import REGISTRY

    holder = await _holder(monkeypatch)
    client = holder._client
    _prepare_reads(client, name)
    if name not in ("vote_poll", "get_poll_results"):
        client.get_messages = AsyncMock(return_value=[make_message(7, text="hi")])
    message = make_message(8, text="hello")
    for method in ("send_message", "edit_message", "send_file"):
        setattr(client, method, AsyncMock(return_value=message))
    client.forward_messages = AsyncMock(return_value=[message])
    client.get_me = AsyncMock(return_value=_user())
    result = await REGISTRY[name].handler(holder, _params(name))
    assert result is not None
    if name in WRITE_TOOLS:
        assert _wrote(client), f"{name} sent nothing to Telegram"
