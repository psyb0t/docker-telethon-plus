"""What send_file hands to Telegram, and how it fails.

The client is a real TelegramClient on a fake wire (see
test_tools_support.make_wire), so Telethon itself decides whether the staged
file becomes a photo or a document and which attributes it carries. Only the
upload of the file's bytes is stubbed. Cleanup of the staged file is covered
by test_send_file.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from unittest.mock import AsyncMock

import httpx
import pytest
from telethon.tl.types import (
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeVideo,
    InputFile,
    InputMediaUploadedDocument,
    InputMediaUploadedPhoto,
)

from .test_tools_support import Wire, make_user, make_wire, sent_message_updates

PAYLOAD = b"x" * 1000
BASE = "https://files.example.invalid"
# A 64x48, two-second H.264 clip.
VIDEO = (Path(__file__).parent / "fixtures" / "video-64x48-2s.mp4").read_bytes()
# Two seconds of silence tagged title "Test Title", artist "Test Artist".
AUDIO = (Path(__file__).parent / "fixtures" / "audio-2s.mp3").read_bytes()


@dataclass
class Staged:
    holder: Any
    wire: Wire
    downloads: Path

    @property
    def staged_names(self) -> list[str]:
        """Names of the files uploaded to Telegram, as the upload saw them."""
        return [os.path.basename(c.args[0]) for c in self.wire.client.upload_file.await_args_list]

    @property
    def left(self) -> list[str]:
        """Files left in the download directory, which may never be created."""
        return os.listdir(self.downloads) if self.downloads.exists() else []

    @property
    def media(self) -> Any:
        return self.wire.sent[0].media


@pytest.fixture
async def staged(base_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Staged:
    from app.client import TelethonHolder
    from app.config import Config

    monkeypatch.setenv("TELETHON_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    holder = TelethonHolder(Config.from_env())
    wire = make_wire(sent_message_updates)
    wire.client.get_entity = AsyncMock(return_value=make_user())
    wire.client.upload_file = AsyncMock(
        return_value=InputFile(id=1, parts=1, name="staged", md5_checksum="")
    )
    holder._client = wire.client
    return Staged(holder=holder, wire=wire, downloads=tmp_path / "downloads")


def _serve(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    import app.tools as tools

    real = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(tools.httpx, "AsyncClient", client)


def _serve_file(
    monkeypatch: pytest.MonkeyPatch, content: bytes = PAYLOAD, content_type: str | None = None
) -> None:
    headers = {"content-type": content_type} if content_type else {}
    _serve(monkeypatch, lambda request: httpx.Response(200, content=content, headers=headers))


async def _send(staged: Staged, url: str, **fields: Any) -> Any:
    from app.tools import REGISTRY

    tool = REGISTRY["send_file"]
    params = tool.params_model.model_validate(
        {"chat": "@test", "file_url": url, "max_bytes": 10_000_000, **fields}
    )
    return await tool.handler(staged.holder, params)


def _filename(media: Any) -> str:
    names = [a.file_name for a in media.attributes if isinstance(a, DocumentAttributeFilename)]
    assert len(names) == 1
    return names[0]


# ---------- file type and name ----------------------------------------------


@pytest.mark.parametrize("name", ["pic.jpg", "pic.jpeg", "pic.png"])
async def test_a_picture_url_is_sent_as_a_photo(staged, monkeypatch, name) -> None:
    """Regression: the staged file had no extension, so Telethon never
    recognised a photo and sent every image as a generic document."""
    _serve_file(monkeypatch)

    await _send(staged, f"{BASE}/{name}")

    assert isinstance(staged.media, InputMediaUploadedPhoto)


async def test_a_picture_sent_as_a_document_keeps_its_name_and_type(staged, monkeypatch) -> None:
    _serve_file(monkeypatch)

    await _send(staged, f"{BASE}/pic.jpg", force_document=True)

    assert isinstance(staged.media, InputMediaUploadedDocument)
    assert staged.media.mime_type == "image/jpeg"
    assert _filename(staged.media) == "pic.jpg"


@pytest.mark.parametrize(
    "path,name,mime_type",
    [
        ("/dir/Quarterly%20report.pdf?token=1#top", "Quarterly report.pdf", "application/pdf"),
        ("/clip.mp4", "clip.mp4", "video/mp4"),
        ("/notes.txt", "notes.txt", "text/plain"),
    ],
)
async def test_a_document_keeps_the_file_name_and_type_from_the_url(
    staged, monkeypatch, path, name, mime_type
) -> None:
    """Regression: recipients saw the staged name, "upload-xxxx"."""
    _serve_file(monkeypatch)

    await _send(staged, f"{BASE}{path}")

    assert isinstance(staged.media, InputMediaUploadedDocument)
    assert _filename(staged.media) == name
    assert staged.media.mime_type == mime_type


def _video(media: Any) -> DocumentAttributeVideo | None:
    found = [a for a in media.attributes if isinstance(a, DocumentAttributeVideo)]
    assert len(found) <= 1
    return found[0] if found else None


async def test_a_video_carries_its_real_dimensions_and_duration(staged, monkeypatch) -> None:
    """Regression: without hachoir Telethon cannot read a video, so it went out
    as 0:00 at 1x1 and played as a broken preview."""
    _serve_file(monkeypatch, VIDEO)

    await _send(staged, f"{BASE}/clip.mp4")

    video = _video(staged.media)
    assert video is not None
    assert (video.w, video.h) == (64, 48)
    assert round(video.duration) == 2
    assert _filename(staged.media) == "clip.mp4"


async def test_a_video_sent_as_a_document_has_no_video_attribute(staged, monkeypatch) -> None:
    _serve_file(monkeypatch, VIDEO)

    await _send(staged, f"{BASE}/clip.mp4", force_document=True)

    assert _video(staged.media) is None
    assert staged.media.mime_type == "video/mp4"


async def test_an_audio_file_carries_its_duration_title_and_artist(staged, monkeypatch) -> None:
    _serve_file(monkeypatch, AUDIO)

    await _send(staged, f"{BASE}/song.mp3")

    (audio,) = [a for a in staged.media.attributes if isinstance(a, DocumentAttributeAudio)]
    assert round(audio.duration) == 2
    assert (audio.title, audio.performer) == ("Test Title", "Test Artist")
    assert not audio.voice


async def test_the_staged_file_has_the_url_extension(staged, monkeypatch) -> None:
    _serve_file(monkeypatch)

    await _send(staged, f"{BASE}/archive.tar.gz")

    assert [n.endswith(".gz") for n in staged.staged_names] == [True]


async def test_the_extension_comes_from_the_content_type_when_the_url_has_none(
    staged, monkeypatch
) -> None:
    _serve_file(monkeypatch, content_type="image/png; charset=binary")

    await _send(staged, f"{BASE}/download")

    assert [n.endswith(".png") for n in staged.staged_names] == [True]
    assert isinstance(staged.media, InputMediaUploadedPhoto)


async def test_a_url_extension_wins_over_the_content_type(staged, monkeypatch) -> None:
    _serve_file(monkeypatch, content_type="application/octet-stream")

    await _send(staged, f"{BASE}/pic.jpg")

    assert [n.endswith(".jpg") for n in staged.staged_names] == [True]
    assert isinstance(staged.media, InputMediaUploadedPhoto)


async def test_an_unrecognisable_extension_is_not_used_for_the_staged_name(
    staged, monkeypatch
) -> None:
    _serve_file(monkeypatch)

    await _send(staged, f"{BASE}/file.{'x' * 40}")

    assert [os.path.splitext(n)[1] for n in staged.staged_names] == [""]


@pytest.mark.parametrize(
    "path,name",
    [
        ("/a/b%2Fc.txt", "c.txt"),
        ("/..%5Cwin%5Cevil.txt", "evil.txt"),
        ("/ev%00il%0A.txt", "evil.txt"),
        (f"/{'n' * 300}.txt", "n" * 255),
    ],
    ids=["encoded_slash", "backslashes", "control_characters", "overlong"],
)
async def test_the_file_name_from_the_url_is_cleaned(staged, monkeypatch, path, name) -> None:
    _serve_file(monkeypatch)

    await _send(staged, f"{BASE}{path}")

    assert _filename(staged.media) == name


@pytest.mark.parametrize("path", ["/", "/dir/..", "/dir/."])
async def test_a_url_without_a_usable_name_does_not_send_a_dot_name(
    staged, monkeypatch, path
) -> None:
    _serve_file(monkeypatch, content_type="application/pdf")

    await _send(staged, f"{BASE}{path}")

    assert _filename(staged.media) not in {"", ".", ".."}
    assert staged.left == []


# ---------- download failures -----------------------------------------------


def _refuse_connection(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.mark.parametrize(
    "handler",
    [lambda request: httpx.Response(404), _refuse_connection],
    ids=["http_404", "connect_error"],
)
async def test_a_download_failure_is_a_value_error_not_a_500(staged, monkeypatch, handler) -> None:
    """Regression: httpx errors escaped as-is and the server mapped them to 500."""
    _serve(monkeypatch, handler)

    with pytest.raises(ValueError, match="file_url fetch failed"):
        await _send(staged, f"{BASE}/a.bin")

    assert staged.left == []
    assert staged.wire.sent == []


@pytest.mark.parametrize(
    "url,message",
    [
        ("https://files.example.invalid:abc/a.bin", "file_url fetch failed"),
        ("http://[not-a-host/a.bin", "Invalid IPv6 URL"),
    ],
    ids=["httpx_rejects", "urllib_rejects"],
)
async def test_a_malformed_url_is_a_value_error(staged, monkeypatch, url, message) -> None:
    _serve_file(monkeypatch)

    with pytest.raises(ValueError, match=message):
        await _send(staged, url)

    assert staged.left == []
    assert staged.wire.sent == []


async def test_a_redirect_to_another_scheme_is_refused(staged, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"location": "ftp://files.example.invalid/a.bin"})
        return httpx.Response(200, content=PAYLOAD)

    _serve(monkeypatch, handler)

    with pytest.raises(ValueError, match="non-http"):
        await _send(staged, f"{BASE}/a.bin")

    assert staged.left == []
    assert staged.wire.sent == []


async def test_a_redirect_between_http_urls_is_followed(staged, monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/moved":
            return httpx.Response(302, headers={"location": f"{BASE}/pic.jpg"})
        return httpx.Response(200, content=PAYLOAD)

    _serve(monkeypatch, handler)

    await _send(staged, f"{BASE}/moved")

    assert len(staged.wire.sent) == 1
    assert staged.left == []


# ---------- throttle accounting ---------------------------------------------


def _send_slots_used(staged: Staged) -> int:
    return staged.holder.throttle.snapshot()["buckets"]["send"]["used"]


@pytest.mark.parametrize(
    "size,requests_sent",
    [
        # Telethon uploads files up to 100 MB in 128 KB parts, then posts them
        # with one SendMediaRequest: parts + 1 requests.
        (1_000, 1 + 1),
        (131_072, 1 + 1),
        (131_073, 2 + 1),
        (300_000, 3 + 1),
    ],
)
async def test_an_upload_is_charged_one_slot_per_request_it_sends(
    staged, monkeypatch, size, requests_sent
) -> None:
    """Regression: the single guard charged one slot however many parts."""
    _serve_file(monkeypatch, content=b"x" * size)
    used_before = _send_slots_used(staged)

    await _send(staged, f"{BASE}/big.bin")

    assert _send_slots_used(staged) - used_before == requests_sent


async def test_a_failed_download_is_not_charged_as_an_upload(staged, monkeypatch) -> None:
    _serve(monkeypatch, lambda request: httpx.Response(404))
    used_before = _send_slots_used(staged)

    with pytest.raises(ValueError):
        await _send(staged, f"{BASE}/a.bin")

    assert _send_slots_used(staged) == used_before
