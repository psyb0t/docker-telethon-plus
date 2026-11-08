"""send_file and download_media on the real account. The files come from the
suite's local server, go to TEST_CHAT, are downloaded back and compared, and
are deleted afterwards."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict

import httpx
import pytest
from conftest import FIXTURES, LocalServer, container_logs, marker, ok

Cleanup = Callable[[str, int], None]


@pytest.fixture(autouse=True)
def _serve(local: LocalServer, media: Dict[str, bytes]) -> None:
    local.files.update(media)
    local.files["photo.png"] = (FIXTURES / "photo-64x48.png").read_bytes()


def _send_file(http: httpx.Client, cleanup: Cleanup, chat: str, url: str, **fields: object) -> dict:
    sent = ok(
        http.post("/api/messages", json={"chat": chat, "file_url": url, "silent": True, **fields})
    )
    cleanup(chat, sent["id"])
    return sent


def _download(http: httpx.Client, chat: str, message_id: int, **params: object) -> httpx.Response:
    return http.get(f"/api/messages/{message_id}/media", params={"chat": chat, **params})


def test_a_png_goes_out_as_a_photo(
    http: httpx.Client, env: dict[str, str], local: LocalServer, cleanup: Cleanup
) -> None:
    chat = env["TEST_CHAT"]
    caption = marker("photo")
    sent = _send_file(http, cleanup, chat, local.file_url("photo.png"), text=caption)
    assert sent["media_type"] == "MessageMediaPhoto"
    assert sent["text"] == caption

    downloaded = _download(http, chat, sent["id"])
    assert downloaded.status_code == 200
    # Telegram re-encodes photos, so only the type is compared.
    assert downloaded.headers["content-type"].startswith("image/")
    assert len(downloaded.content) > 0


@pytest.mark.parametrize(
    ("name", "mime_type"),
    [("clip.mp4", "video/mp4"), ("song.mp3", "audio/mpeg"), ("notes.txt", "text/plain")],
)
def test_a_document_comes_back_byte_for_byte_with_its_name_and_type(
    http: httpx.Client,
    env: dict[str, str],
    local: LocalServer,
    media: Dict[str, bytes],
    cleanup: Cleanup,
    name: str,
    mime_type: str,
) -> None:
    chat = env["TEST_CHAT"]
    sent = _send_file(http, cleanup, chat, local.file_url(name))
    assert sent["media_type"] == "MessageMediaDocument"

    downloaded = _download(http, chat, sent["id"])
    assert downloaded.status_code == 200
    assert downloaded.content == media[name]
    assert downloaded.headers["content-type"].startswith(mime_type)
    assert name in downloaded.headers["content-disposition"]


def test_force_document_sends_a_png_as_a_file(
    http: httpx.Client, env: dict[str, str], local: LocalServer, cleanup: Cleanup
) -> None:
    chat = env["TEST_CHAT"]
    sent = _send_file(http, cleanup, chat, local.file_url("photo.png"), force_document=True)
    assert sent["media_type"] == "MessageMediaDocument"

    downloaded = _download(http, chat, sent["id"])
    assert downloaded.content == (FIXTURES / "photo-64x48.png").read_bytes()
    assert "photo.png" in downloaded.headers["content-disposition"]


def test_a_failed_download_sends_nothing(
    http: httpx.Client, env: dict[str, str], local: LocalServer
) -> None:
    chat = env["TEST_CHAT"]
    newest_before = ok(http.get("/api/messages", params={"chat": chat, "limit": 1}))

    resp = http.post(
        "/api/messages", json={"chat": chat, "file_url": local.file_url("missing.bin")}
    )
    assert resp.status_code == 400
    assert "file_url fetch failed" in resp.text
    assert ok(http.get("/api/messages", params={"chat": chat, "limit": 1})) == newest_before


def test_max_bytes_refuses_a_bigger_download(
    http: httpx.Client, env: dict[str, str], local: LocalServer, cleanup: Cleanup
) -> None:
    chat = env["TEST_CHAT"]
    sent = _send_file(http, cleanup, chat, local.file_url("clip.mp4"))

    refused = _download(http, chat, sent["id"], max_bytes=100)
    assert refused.status_code == 400
    assert "too large" in refused.text


def test_a_text_message_has_no_media_to_download(
    http: httpx.Client, env: dict[str, str], cleanup: Cleanup
) -> None:
    chat = env["TEST_CHAT"]
    sent = ok(
        http.post("/api/messages", json={"chat": chat, "text": marker("no-media"), "silent": True})
    )
    cleanup(chat, sent["id"])

    resp = _download(http, chat, sent["id"])
    assert resp.status_code == 400
    assert "no media" in resp.text


def test_the_logs_stay_one_json_object_per_line(
    http: httpx.Client, env: dict[str, str], local: LocalServer, cleanup: Cleanup
) -> None:
    """Regression: hachoir printed its parser warnings to stderr itself, so a
    TELETHON_LOG_JSON stream got plain-text lines whenever a file was sent."""
    for name in ("clip.mp4", "song.mp3"):
        _send_file(http, cleanup, env["TEST_CHAT"], local.file_url(name))

    lines = [line for line in container_logs().splitlines() if line.strip()]
    assert lines
    for line in lines:
        json.loads(line)


def test_the_fixtures_are_real_media() -> None:
    """The byte comparisons above only mean something if the fixtures are what
    their names say."""
    assert (FIXTURES / "photo-64x48.png").read_bytes().startswith(b"\x89PNG")
    assert b"ftyp" in Path(FIXTURES / "video-64x48-2s.mp4").read_bytes()[:16]
