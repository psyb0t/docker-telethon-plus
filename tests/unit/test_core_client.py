"""TelethonHolder: cache-aware entity resolution, account binding at start,
and Telethon's own flood sleeps reaching the throttler and the metrics."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any, AsyncIterator, List
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.client.users import _fmt_flood
from telethon.errors import ChatAdminRequiredError, FloodWaitError
from telethon.tl.types import InputPeerChannel, InputPeerUser, PeerChat

import app.client as client_mod
from app.cache import ACCESS_HASH_FIELD, ACCOUNT_ID_KEY
from app.client import TELETHON_SLEEP_BUCKET, TELETHON_USERS_LOGGER, TelethonHolder
from app.config import Config

ACCESS_HASH = 123456789012345
ACCOUNT = 99
FLOOD_SECONDS = 12


def _entity(eid: int, kind: str, username: str, access_hash: Any = ACCESS_HASH) -> MagicMock:
    e = MagicMock()
    e.id = eid
    e.username = username
    e.title = None
    e.first_name = None
    e.last_name = None
    e.phone = None
    e.bot = False
    e.access_hash = access_hash
    e.__class__.__name__ = kind
    return e


def _cached(holder: Any, kind: str = "Channel", **kwargs: Any) -> MagicMock:
    entity = _entity(55, kind, "somechan", **kwargs)
    holder.cache.put("@somechan", entity)
    return entity


# ---------- cache hits: what is passed to Telethon ---------------------------


@pytest.mark.parametrize(
    ("kind", "peer_type", "id_attr"),
    [
        ("Channel", InputPeerChannel, "channel_id"),
        ("User", InputPeerUser, "user_id"),
    ],
)
async def test_a_cache_hit_resolves_by_id_and_access_hash(
    holder: Any, kind: str, peer_type: type, id_attr: str
) -> None:
    entity = _cached(holder, kind)
    holder._client.get_entity = AsyncMock(return_value=entity)

    assert await holder.resolve_entity("@somechan") is entity

    (peer,) = holder._client.get_entity.await_args.args
    assert isinstance(peer, peer_type)
    assert getattr(peer, id_attr) == 55
    assert peer.access_hash == ACCESS_HASH
    assert holder.metrics.snapshot()["cache"] == {"hits": 1, "misses": 0}


async def test_a_cached_basic_group_needs_no_access_hash(holder: Any) -> None:
    entity = _cached(holder, "Chat", access_hash=None)
    holder._client.get_entity = AsyncMock(return_value=entity)

    await holder.resolve_entity("@somechan")

    (peer,) = holder._client.get_entity.await_args.args
    assert isinstance(peer, PeerChat)
    assert peer.chat_id == 55


async def test_a_cached_entity_resolves_after_a_restart(holder: Any) -> None:
    """Regression: the cache held the id only, and a fresh StringSession does
    not know it, so get_entity(PeerChannel(id)) raised ValueError."""
    entity = _cached(holder)

    async def get_entity(ref: Any) -> Any:
        if isinstance(ref, (InputPeerChannel, InputPeerUser)):
            return entity
        raise ValueError("Could not find the input entity")

    holder._client.get_entity = AsyncMock(side_effect=get_entity)
    assert await holder.resolve_entity("@somechan") is entity
    assert holder._client.get_entity.await_count == 1


async def test_a_record_without_an_access_hash_is_resolved_fresh(holder: Any) -> None:
    _cached(holder, access_hash=None)
    fresh = _entity(55, "Channel", "somechan")
    holder._client.get_entity = AsyncMock(return_value=fresh)

    assert await holder.resolve_entity("@somechan") is fresh

    holder._client.get_entity.assert_awaited_once_with("@somechan")
    assert holder.metrics.snapshot()["cache"] == {"hits": 0, "misses": 1}
    # The fresh resolve stores the hash the old record lacked.
    assert holder.cache.get("somechan")[ACCESS_HASH_FIELD] == ACCESS_HASH


# ---------- cache hits: which errors fall back -------------------------------


async def test_telethons_value_error_falls_back_to_a_fresh_resolve(holder: Any) -> None:
    _cached(holder)
    fresh = _entity(55, "Channel", "somechan")
    holder._client.get_entity = AsyncMock(
        side_effect=[ValueError("Could not find the input entity"), fresh]
    )

    assert await holder.resolve_entity("@somechan") is fresh

    assert holder._client.get_entity.await_args_list[1].args == ("@somechan",)
    # The failed lookup is not a hit.
    assert holder.metrics.snapshot()["cache"] == {"hits": 0, "misses": 1}


@pytest.mark.parametrize(
    "error",
    [
        FloodWaitError(request=None, capture=17),
        ChatAdminRequiredError(request=None),
        ConnectionError("network down"),
    ],
    ids=["flood_wait", "rpc_error", "connection_error"],
)
async def test_other_errors_on_the_cached_lookup_are_not_swallowed(
    holder: Any, error: Exception
) -> None:
    """Regression: `except Exception` hid a FloodWaitError or a network error
    and followed it with a ResolveUsername, the call the cache exists to avoid."""
    _cached(holder)
    holder._client.get_entity = AsyncMock(side_effect=error)

    with pytest.raises(type(error)):
        await holder.resolve_entity("@somechan")

    assert holder._client.get_entity.await_count == 1
    assert holder.metrics.snapshot()["cache"] == {"hits": 0, "misses": 0}
    assert holder.cache.get("somechan") is not None


# ---------- start(): account binding and Telethon's flood sleeps ---------------


class _FakeTelegramClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.disconnected = False

    async def connect(self) -> None:
        return None

    async def is_user_authorized(self) -> bool:
        return True

    async def get_me(self) -> Any:
        return SimpleNamespace(id=ACCOUNT, username="me")

    async def disconnect(self) -> None:
        self.disconnected = True


@pytest.fixture
def telethon_stubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod, "TelegramClient", _FakeTelegramClient)
    # The fixture's session "stub" is not a real session string.
    monkeypatch.setattr(client_mod, "StringSession", lambda session: None)


@pytest.fixture
async def started(
    base_env: None, telethon_stubbed: None, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[TelethonHolder]:
    monkeypatch.setenv("TELETHON_LOG_LEVEL", "INFO")
    h = TelethonHolder(Config.from_env())
    await h.start()
    yield h
    await h.stop()


async def test_start_binds_the_cache_to_the_account(started: TelethonHolder) -> None:
    started.cache.put("@somechan", _entity(55, "Channel", "somechan"))
    assert started.cache._account_id == ACCOUNT


async def test_start_drops_a_cache_that_belongs_to_another_account(
    base_env: None,
    telethon_stubbed: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    other_account = ACCOUNT + 1
    (tmp_path / "cache.json").write_text(
        json.dumps(
            {
                ACCOUNT_ID_KEY: other_account,
                "somechan": {
                    "id": 55,
                    "type": "Channel",
                    ACCESS_HASH_FIELD: ACCESS_HASH,
                    "cached_at": 1,
                },
            }
        )
    )
    h = TelethonHolder(Config.from_env())
    assert h.cache.stats() == {"entries": 1}
    await h.start()
    try:
        assert h.cache.stats() == {"entries": 0}
    finally:
        await h.stop()


def _telethon_logger_records(caplog: pytest.LogCaptureFixture) -> List[logging.LogRecord]:
    return [r for r in caplog.records if r.name == TELETHON_USERS_LOGGER]


def _log_flood_sleep(seconds: int = FLOOD_SECONDS, early: bool = False) -> None:
    """The record Telethon emits when it sleeps through a flood wait."""
    request = SimpleNamespace()
    logging.getLogger(TELETHON_USERS_LOGGER).info(*_fmt_flood(seconds, request, early=early))


@pytest.mark.parametrize("early", [False, True])
async def test_a_flood_sleep_telethon_takes_reaches_the_throttler_and_metrics(
    started: TelethonHolder, early: bool
) -> None:
    """Regression: Telethon sleeps through waits up to flood_sleep_threshold
    itself and never raises, so neither the adaptive backoff nor the flood
    counters ever saw them."""
    _log_flood_sleep(early=early)

    assert started.throttle.snapshot()["flood_events_1h"] == 1
    assert started.throttle.snapshot()["multiplier"] == 2.0
    assert started.metrics.snapshot()["flood_events"] == {TELETHON_SLEEP_BUCKET: 1}


async def test_the_flood_record_is_still_hidden_from_the_logs(
    started: TelethonHolder, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        _log_flood_sleep()
        logging.getLogger(TELETHON_USERS_LOGGER).info("Phone migrated to %d", 4)
    assert _telethon_logger_records(caplog) == []


async def test_other_telethon_info_records_are_not_counted_as_floods(
    started: TelethonHolder,
) -> None:
    logging.getLogger(TELETHON_USERS_LOGGER).info("Phone migrated to %d", 4)
    assert started.throttle.snapshot()["flood_events_1h"] == 0
    assert started.metrics.snapshot()["flood_events"] == {}


async def test_warnings_from_the_logger_still_show(
    started: TelethonHolder, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        logging.getLogger(TELETHON_USERS_LOGGER).warning(
            "Telegram is having internal issues %s: %s", "ServerError", "x"
        )
    assert [r.levelno for r in _telethon_logger_records(caplog)] == [logging.WARNING]


async def test_records_below_the_configured_level_stay_hidden(
    base_env: None,
    telethon_stubbed: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("TELETHON_LOG_LEVEL", "ERROR")
    h = TelethonHolder(Config.from_env())
    await h.start()
    try:
        with caplog.at_level(logging.DEBUG):
            logging.getLogger(TELETHON_USERS_LOGGER).warning("quiet at ERROR")
            logging.getLogger(TELETHON_USERS_LOGGER).error("loud at ERROR")
        assert [r.levelno for r in _telethon_logger_records(caplog)] == [logging.ERROR]
    finally:
        await h.stop()


async def test_stop_stops_observing_and_restores_the_logger_level(
    base_env: None, telethon_stubbed: None
) -> None:
    users_logger = logging.getLogger(TELETHON_USERS_LOGGER)
    level_before = users_logger.level
    filters_before = list(users_logger.filters)

    h = TelethonHolder(Config.from_env())
    await h.start()
    assert users_logger.level == logging.INFO
    await h.stop()

    assert users_logger.level == level_before
    assert users_logger.filters == filters_before
    _log_flood_sleep()
    assert h.throttle.snapshot()["flood_events_1h"] == 0
