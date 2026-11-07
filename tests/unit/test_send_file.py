"""send_file downloads into TELETHON_DOWNLOAD_DIR and never leaves the file
behind, whether the send works, the download fails or the file is too big."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

PAYLOAD = b"x" * 1000


@pytest.fixture
async def file_holder(
    base_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make_entity: Any
) -> Any:
    from app.client import TelethonHolder
    from app.config import Config

    monkeypatch.setenv("TELETHON_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    holder = TelethonHolder(Config.from_env())
    holder._client = AsyncMock()
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.send_file = AsyncMock(return_value=None)
    return holder


def _serve(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> None:
    import app.tools as tools

    real = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real(transport=httpx.MockTransport(lambda request: response), **kwargs)

    monkeypatch.setattr(tools.httpx, "AsyncClient", client)


async def _send(holder: Any, max_bytes: int = 10_000) -> Any:
    from app.tools import REGISTRY

    tool = REGISTRY["send_file"]
    params = tool.params_model.model_validate(
        {"chat": "@test", "file_url": "https://files.example.invalid/a.bin", "max_bytes": max_bytes}
    )
    return await tool.handler(holder, params)


def _left(tmp_path: Path) -> list[str]:
    return os.listdir(tmp_path / "downloads")


async def test_the_file_is_staged_in_the_download_dir_and_removed(
    file_holder: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import app.tools as tools

    _serve(monkeypatch, httpx.Response(200, content=PAYLOAD))
    staged: dict[str, Any] = {}

    async def send_file(**kwargs: Any) -> None:
        path = Path(kwargs["file"])
        staged["dir"] = path.parent
        staged["content"] = path.read_bytes()

    file_holder._client.send_file = send_file
    monkeypatch.setattr(tools, "_message_to_dict", lambda msg: {"sent": True})
    assert await _send(file_holder) == {"sent": True}
    assert staged == {"dir": tmp_path / "downloads", "content": PAYLOAD}
    assert _left(tmp_path) == []


async def test_a_failed_download_leaves_no_file(
    file_holder: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _serve(monkeypatch, httpx.Response(500))
    with pytest.raises(ValueError, match="file_url fetch failed"):
        await _send(file_holder)
    assert _left(tmp_path) == []
    file_holder._client.send_file.assert_not_awaited()


async def test_a_file_over_the_limit_leaves_no_file(
    file_holder: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def body() -> Any:
        yield PAYLOAD
        yield PAYLOAD

    # A streamed body has no Content-Length, so the cap is enforced while
    # the file is written.
    _serve(monkeypatch, httpx.Response(200, content=body()))
    with pytest.raises(ValueError, match="too large"):
        await _send(file_holder, max_bytes=1500)
    assert _left(tmp_path) == []
    file_holder._client.send_file.assert_not_awaited()


async def test_a_failed_send_leaves_no_file(
    file_holder: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _serve(monkeypatch, httpx.Response(200, content=PAYLOAD))
    file_holder._client.send_file = AsyncMock(side_effect=ConnectionError("telegram down"))
    with pytest.raises(ConnectionError):
        await _send(file_holder)
    assert _left(tmp_path) == []
