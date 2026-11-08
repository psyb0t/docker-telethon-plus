"""Shared by the test_tools_* modules: real Telethon objects, and a real
TelegramClient whose wire is a fake.

A MagicMock client accepts any request, so a request that Telegram would
reject, or that Telethon cannot even serialise, passes unnoticed. `make_wire`
keeps Telethon's own request pipeline (resolving entities, `send_file`, the
participants iterator) and replaces only the MTProto sender: every request is
serialised the way the sender does, then answered by a test-supplied handler.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    Channel,
    Chat,
    ChatPhotoEmpty,
    Message,
    PeerUser,
    UpdateMessageID,
    UpdateNewMessage,
    Updates,
    User,
)

NOW = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
SENT_MESSAGE_ID = 5
SENT_MESSAGE_CHAT_ID = 5


def make_user(eid: int = 5) -> User:
    return User(id=eid, access_hash=1)


def make_supergroup(eid: int = 100) -> Channel:
    return Channel(
        id=eid, title="group", photo=ChatPhotoEmpty(), date=NOW, megagroup=True, access_hash=1
    )


def make_basic_group(eid: int = 7) -> Chat:
    return Chat(
        id=eid, title="basic", photo=ChatPhotoEmpty(), participants_count=3, date=NOW, version=1
    )


def empty_updates() -> Updates:
    return Updates(updates=[], users=[], chats=[], date=NOW, seq=0)


def sent_message_updates(request: Any) -> Updates:
    """The answer to a send request: the new message, matched by random_id."""
    message = Message(
        id=SENT_MESSAGE_ID,
        peer_id=PeerUser(SENT_MESSAGE_CHAT_ID),
        date=NOW,
        message="",
        out=True,
    )
    return Updates(
        updates=[
            UpdateMessageID(id=SENT_MESSAGE_ID, random_id=request.random_id),
            UpdateNewMessage(message=message, pts=1, pts_count=1),
        ],
        users=[],
        chats=[],
        date=NOW,
        seq=0,
    )


class _FakeSender:
    """Stands in for MTProtoSender: serialises the request, then answers it."""

    def __init__(self, handler: Callable[[Any], Any], sent: List[Any]) -> None:
        self._handler = handler
        self._sent = sent

    def send(self, request: Any, ordered: bool = False) -> "asyncio.Future[Any]":
        request._bytes()  # a TL type error surfaces here, as on the real wire
        self._sent.append(request)
        future: "asyncio.Future[Any]" = asyncio.get_running_loop().create_future()
        future.set_result(self._handler(request))
        return future


@dataclass
class Wire:
    client: TelegramClient
    sent: List[Any] = field(default_factory=list)


def make_wire(handler: Callable[[Any], Any] | None = None) -> Wire:
    """A real TelegramClient that never opens a socket. `handler(request)`
    returns the server's answer; the default answers with an empty update."""
    wire = Wire(client=TelegramClient(StringSession(), 1, "stub"))
    wire.client._sender = _FakeSender(  # type: ignore[assignment]
        handler or (lambda request: empty_updates()), wire.sent
    )
    return wire


def entity_table(entities: Dict[str, Any]) -> Callable[[Any], Any]:
    """A `get_entity` side effect that answers by the chat reference, or, for
    the InputPeer a cache hit sends, by the entity id in it."""

    async def get_entity(reference: Any) -> Any:
        for id_attr in ("channel_id", "user_id", "chat_id"):
            peer_id = getattr(reference, id_attr, None)
            if peer_id is not None:
                return next(e for e in entities.values() if e.id == peer_id)
        return entities[str(reference)]

    return get_entity
