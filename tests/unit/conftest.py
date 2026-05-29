"""Unit-test fixtures — no Docker, no Telegram.

The TelethonHolder normally connects to Telegram on .start(); these tests
bypass that by constructing a holder manually with a stub TelegramClient.

`mock_client` is an AsyncMock with the surface we care about. Each test
arranges return values per call and asserts on .mock_calls / .await_args_list.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Tests live in tests/unit/, app code is in app/ — add repo root to sys.path
# so `from app.foo import bar` works without packaging.
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Set env vars Config requires so tests can build a Config object."""
    monkeypatch.setenv("TELETHON_API_ID", "1")
    monkeypatch.setenv("TELETHON_API_HASH", "stub")
    monkeypatch.setenv("TELETHON_SESSION", "stub")
    monkeypatch.setenv("TELETHON_CACHE_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setenv("TELETHON_UPDATES_ENABLED", "false")
    monkeypatch.setenv("TELETHON_LOG_LEVEL", "ERROR")


@pytest.fixture
def cfg(base_env: None):
    from app.config import Config
    return Config.from_env()


def _make_entity(eid: int, *, username: str | None = None, title: str | None = None,
                 is_channel: bool = False) -> MagicMock:
    """Construct a minimal stand-in for a Telethon entity."""
    e = MagicMock()
    e.id = eid
    e.username = username
    e.title = title
    e.first_name = None
    e.last_name = None
    e.phone = None
    e.bot = False
    e.__class__.__name__ = "Channel" if is_channel else "User"
    return e


def _make_message(mid: int, *, text: str = "", chat_id: int | None = None) -> MagicMock:
    m = MagicMock()
    m.id = mid
    m.message = text
    m.date = None
    m.out = False
    m.reply_to = None
    m.media = None
    m.sender_id = 1
    m.peer_id = MagicMock()
    m.peer_id.user_id = chat_id
    m.peer_id.channel_id = None
    m.peer_id.chat_id = None
    m.fwd_from = None
    m.views = None
    m.forwards = None
    return m


@pytest.fixture
def make_entity():
    return _make_entity


@pytest.fixture
def make_message():
    return _make_message


@pytest.fixture
async def holder(cfg) -> Any:
    """A TelethonHolder with an AsyncMock standing in for TelegramClient.

    `_client` is set directly to bypass .start()'s actual network connect.
    """
    from app.client import TelethonHolder

    h = TelethonHolder(cfg)
    h._client = AsyncMock()
    # default get_entity behaviour: not mocked — tests will set it.
    return h
