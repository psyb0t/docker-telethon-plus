"""download_media: the size cap, media with no file, and the throttle charge."""

from __future__ import annotations

import base64
from typing import Any, Callable
from unittest.mock import AsyncMock

import pytest
from telethon.tl.types import (
    Document,
    DocumentAttributeFilename,
    GeoPointEmpty,
    Message,
    MessageMediaDice,
    MessageMediaDocument,
    MessageMediaGeo,
    MessageMediaPhoto,
    MessageMediaPoll,
    PeerUser,
    Photo,
    Poll,
    PollResults,
    TextWithEntities,
)

from .test_tools_support import NOW

CHAT = "1"
MESSAGE_ID = 7


def _message(media: Any) -> Message:
    return Message(id=MESSAGE_ID, peer_id=PeerUser(1), media=media)


def _document(size: int, *, mime_type: str = "video/mp4", name: str = "clip.mp4") -> Message:
    document = Document(
        id=1,
        access_hash=1,
        file_reference=b"",
        date=NOW,
        mime_type=mime_type,
        size=size,
        dc_id=1,
        attributes=[DocumentAttributeFilename(file_name=name)],
    )
    return _message(MessageMediaDocument(document=document))


def _photo_of_unknown_size() -> Message:
    photo = Photo(id=1, access_hash=1, file_reference=b"", date=NOW, sizes=[], dc_id=1)
    return _message(MessageMediaPhoto(photo=photo))


def _poll() -> Message:
    poll = Poll(
        id=1,
        hash=0,
        question=TextWithEntities(text="Q?", entities=[]),
        answers=[],
    )
    return _message(MessageMediaPoll(poll=poll, results=PollResults()))


def _download_of(data: bytes) -> Callable[..., Any]:
    async def download_media(message: Any, file: Any) -> Any:
        file.write(data)
        return file

    return download_media


def _arrange(holder: Any, make_entity: Any, message: Message, data: bytes) -> AsyncMock:
    """The chat holds `message`; downloading it yields `data`."""
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(return_value=[message])
    holder._client.download_media = AsyncMock(side_effect=_download_of(data))
    return holder._client.download_media


async def _download(holder: Any, max_bytes: int = 10_000_000) -> Any:
    from app.tools import REGISTRY

    tool = REGISTRY["download_media"]
    params = tool.params_model.model_validate(
        {"chat": CHAT, "message_id": MESSAGE_ID, "max_bytes": max_bytes}
    )
    return await tool.handler(holder, params)


def _read_slots_used(holder: Any) -> int:
    return holder.throttle.snapshot()["buckets"]["read"]["used"]


async def test_download_media_returns_the_file(holder, make_entity) -> None:
    _arrange(holder, make_entity, _document(5), b"hello")

    result = await _download(holder)

    assert result["size"] == 5
    assert result["mime_type"] == "video/mp4"
    assert result["filename"] == "clip.mp4"
    assert result["media_type"] == "MessageMediaDocument"
    assert base64.b64decode(result["data_base64"]) == b"hello"


async def test_media_over_the_limit_is_refused_before_it_is_downloaded(holder, make_entity) -> None:
    """Regression: the whole file was downloaded before max_bytes was checked."""
    download = _arrange(holder, make_entity, _document(10_000), b"x" * 10_000)

    with pytest.raises(ValueError, match="too large"):
        await _download(holder, max_bytes=1_000)

    download.assert_not_awaited()


async def test_media_of_unknown_size_is_capped_after_the_download(holder, make_entity) -> None:
    download = _arrange(holder, make_entity, _photo_of_unknown_size(), b"x" * 2_000)

    with pytest.raises(ValueError, match="too large"):
        await _download(holder, max_bytes=1_000)

    download.assert_awaited_once()


async def test_media_that_reports_less_than_it_delivers_is_capped_after_the_download(
    holder, make_entity
) -> None:
    _arrange(holder, make_entity, _document(10), b"x" * 2_000)

    with pytest.raises(ValueError, match="too large"):
        await _download(holder, max_bytes=1_000)


@pytest.mark.parametrize(
    "message",
    [
        _message(MessageMediaGeo(geo=GeoPointEmpty())),
        _message(MessageMediaDice(value=3, emoticon="x")),
        _poll(),
    ],
    ids=["geo", "dice", "poll"],
)
async def test_media_with_no_file_is_an_error_not_an_empty_success(
    holder, make_entity, message
) -> None:
    """Regression: a location, dice roll or poll came back as size 0, a
    success with nothing in it."""
    download = _arrange(holder, make_entity, message, b"")

    with pytest.raises(ValueError, match="no downloadable file"):
        await _download(holder)

    download.assert_not_awaited()


async def test_a_download_that_returns_nothing_is_an_error(holder, make_entity) -> None:
    _arrange(holder, make_entity, _document(10), b"")

    with pytest.raises(ValueError, match="no data"):
        await _download(holder)


async def test_a_message_without_media_is_an_error(holder, make_entity) -> None:
    _arrange(holder, make_entity, Message(id=MESSAGE_ID, peer_id=PeerUser(1), message="text"), b"")

    with pytest.raises(ValueError, match="no media"):
        await _download(holder)


@pytest.mark.parametrize(
    "size,requests_sent",
    [
        # Telethon downloads files up to 100 MB in 128 KB parts, one GetFile
        # request each, after the GetMessages request that found the message.
        (1_000, 1 + 1),
        (131_072, 1 + 1),
        (131_073, 1 + 2),
        (300_000, 1 + 3),
    ],
)
async def test_a_download_is_charged_one_slot_per_request_it_sends(
    holder, make_entity, size, requests_sent
) -> None:
    """Regression: two guards charged two slots however many parts."""
    _arrange(holder, make_entity, _document(size), b"x" * size)
    used_before = _read_slots_used(holder)

    await _download(holder)

    assert _read_slots_used(holder) - used_before == requests_sent
