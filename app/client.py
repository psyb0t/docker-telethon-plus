"""Telethon client lifecycle, shared lock, throttling, entity cache."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon.tl.types import InputPeerChannel, InputPeerUser, PeerChat

from app.cache import ACCESS_HASH_FIELD, EntityCache
from app.config import Config, telethon_log_level
from app.metrics import MetricsRegistry
from app.throttle import BucketSpec, ThrottleConfig, Throttler
from app.updates import UpdateBroker

log = logging.getLogger(__name__)

# Telethon logs the flood waits it sleeps through itself (those at or under
# flood_sleep_threshold) from this logger, as an INFO record built by
# telethon.client.users._fmt_flood: msg template plus the args
# (" early" or "", seconds, timedelta, request class name).
TELETHON_USERS_LOGGER = "telethon.client.users"
_TELETHON_FLOOD_SLEEP_MSG = "Sleeping%s for %ds (%s) on %s flood wait"
_TELETHON_FLOOD_SECONDS_ARG = 1
_TELETHON_FLOOD_ARG_COUNT = 4
# Throttle bucket and metric label for waits Telethon slept through, which
# belong to no bucket of ours.
TELETHON_SLEEP_BUCKET = "telethon_sleep"

_CHANNEL_TYPES = ("Channel", "ChannelFull")
_CHAT_TYPES = ("Chat", "ChatFull")
_USER_TYPES = ("User", "UserFull")


def _peer_from_cached(cached: Dict[str, Any]) -> Optional[Any]:
    """Peer Telethon can resolve from a cache record, or None when the record
    cannot name one.

    Users and channels need their access_hash: the StringSession keeps no
    entities across a restart, so a bare id would fail with ValueError. Basic
    groups are addressed by id alone. Records without a usable hash (stored
    before hashes were cached, or entities Telegram gave none for) count as a
    miss and are resolved fresh.
    """
    eid = cached.get("id")
    if not isinstance(eid, int):
        return None
    etype = cached.get("type") or ""
    if etype in _CHAT_TYPES:
        return PeerChat(eid)
    access_hash = cached.get(ACCESS_HASH_FIELD)
    if not isinstance(access_hash, int):
        return None
    if etype in _CHANNEL_TYPES:
        return InputPeerChannel(eid, access_hash)
    if etype in _USER_TYPES:
        return InputPeerUser(eid, access_hash)
    return None


class _TelethonFloodObserver(logging.Filter):
    """Feeds the flood waits Telethon sleeps through into the throttler and
    the metrics.

    Telethon sleeps through any FLOOD_WAIT up to flood_sleep_threshold without
    raising, so guard() never sees it and neither the adaptive backoff nor the
    flood counters would. Its only trace is an INFO log record, which this
    filter reads. It runs on a logger lowered to INFO for that reason, then
    drops what the "telethon" logger would have hidden, so the extra INFO
    records never reach a handler.
    """

    def __init__(
        self,
        throttle: Throttler,
        metrics: MetricsRegistry,
        visible_level: int,
    ) -> None:
        super().__init__()
        self._throttle = throttle
        self._metrics = metrics
        self._visible_level = visible_level

    @staticmethod
    def _flood_seconds(record: logging.LogRecord) -> Optional[float]:
        if record.levelno != logging.INFO or record.msg != _TELETHON_FLOOD_SLEEP_MSG:
            return None
        args = record.args
        if not isinstance(args, tuple) or len(args) != _TELETHON_FLOOD_ARG_COUNT:
            return None
        seconds = args[_TELETHON_FLOOD_SECONDS_ARG]
        if not isinstance(seconds, (int, float)):
            return None
        return float(seconds)

    def filter(self, record: logging.LogRecord) -> bool:
        seconds = self._flood_seconds(record)
        if seconds is not None:
            self._throttle.notify_flood(seconds, TELETHON_SLEEP_BUCKET)
            self._metrics.record_flood(TELETHON_SLEEP_BUCKET)
        return record.levelno >= self._visible_level


def _build_throttle_config(cfg: Config) -> ThrottleConfig:
    return ThrottleConfig(
        enabled=cfg.throttle_enabled,
        global_interval_ms=cfg.throttle_global_interval_ms,
        jitter_ms=cfg.throttle_jitter_ms,
        per_chat_interval_ms=cfg.throttle_per_chat_interval_ms,
        per_chat_read_interval_ms=cfg.throttle_per_chat_read_interval_ms,
        adaptive=cfg.throttle_adaptive,
        buckets={
            "resolve_username": BucketSpec(cfg.bucket_resolve_per_min, 60),
            "get_full": BucketSpec(cfg.bucket_get_full_per_min, 60),
            "join": BucketSpec(cfg.bucket_join_per_hour, 3600),
            "create": BucketSpec(cfg.bucket_create_per_hour, 3600),
            "send": BucketSpec(cfg.bucket_send_per_min, 60),
            "read": BucketSpec(cfg.bucket_read_per_min, 60),
            "other": BucketSpec(60, 60),
        },
    )


class TelethonHolder:
    """Single shared TelegramClient with throttling, caching, and a serialization lock.

    Telethon's TelegramClient is async-safe for most operations, but serializing
    API calls behind a lock makes flood/error semantics predictable across the
    REST + MCP surfaces sharing one client. On top of that, every request goes
    through a throttler (per-method, per-chat, global + jitter) and a persistent
    entity cache.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._client: Optional[TelegramClient] = None
        self._lock = asyncio.Lock()
        self._throttle = Throttler(_build_throttle_config(cfg))
        self._cache = EntityCache(
            path=cfg.cache_path,
            ttl_seconds=cfg.cache_ttl_seconds,
            enabled=cfg.cache_enabled,
        )
        self._metrics = MetricsRegistry()
        self._updates = UpdateBroker(
            enabled=cfg.updates_enabled,
            post_to_url=cfg.post_to_url,
            post_to_timeout=cfg.post_to_timeout,
            buffer_size=cfg.updates_buffer_size,
        )
        self._flood_observer: Optional[_TelethonFloodObserver] = None
        self._users_logger_level = logging.NOTSET

    @property
    def client(self) -> TelegramClient:
        if self._client is None:
            raise RuntimeError("Telethon client not started")
        return self._client

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    @property
    def cache(self) -> EntityCache:
        return self._cache

    @property
    def throttle(self) -> Throttler:
        return self._throttle

    @property
    def metrics(self) -> MetricsRegistry:
        return self._metrics

    @property
    def updates(self) -> UpdateBroker:
        return self._updates

    @property
    def cfg(self) -> Config:
        return self._cfg

    def assert_writeable(self) -> None:
        """Raise PermissionError if read-only mode is active."""
        if self._cfg.read_only:
            raise PermissionError("read-only mode: write operations are disabled")

    @property
    def dry_run(self) -> bool:
        return self._cfg.dry_run

    @asynccontextmanager
    async def guard(
        self,
        bucket: str,
        chat_key: Optional[str] = None,
        chat_kind: str = "send",
    ) -> AsyncIterator[TelegramClient]:
        """Acquire throttle slot + holder lock for a single request.

        `bucket` selects the rate-limit category (resolve_username, get_full,
        join, create, send, read, other). `chat_key` enforces the per-chat
        min interval; `chat_kind` ("send" or "read") picks which timeline.

        The bucket and per-chat slots are taken before the holder lock, so a
        long wait for one chat does not block calls for other chats. The
        global gap, and a per-chat check against the real send times, run
        inside the lock: requests queued on it would otherwise fire back to
        back once a long call released it.

        On FloodWaitError, we record the event for adaptive backoff before
        re-raising. Other RPCErrors pass through.
        """
        await self._throttle.reserve(bucket, chat_key, chat_kind=chat_kind)
        async with self._lock:
            await self._throttle.pace(chat_key, chat_kind=chat_kind)
            try:
                yield self.client
            except FloodWaitError as exc:
                self._throttle.notify_flood(float(exc.seconds), bucket)
                self._metrics.record_flood(bucket)
                raise

    async def resolve_entity(self, chat: str) -> Any:
        """Cache-aware entity resolution.

        - Numeric chat refs use Telethon directly (no resolveUsername needed
          once the entity is in the session DB).
        - Username refs hit the cache; on miss we fall through to Telethon
          under the resolve_username bucket and store the result.
        """
        stripped = chat.strip()
        is_numeric = stripped.lstrip("-").isdigit()

        if is_numeric:
            async with self.guard("other"):
                entity = await self.client.get_entity(int(stripped))
            self._cache.put(stripped, entity)
            return entity

        entity = await self._resolve_from_cache(stripped)
        if entity is not None:
            return entity

        # True miss — burns a resolve_username slot.
        self._metrics.record_cache(hit=False)
        async with self.guard("resolve_username"):
            entity = await self.client.get_entity(stripped)
        self._cache.put(stripped, entity)
        return entity

    async def _resolve_from_cache(self, key: str) -> Optional[Any]:
        """Entity for a cached handle, or None when the caller must resolve it
        fresh.

        The lookup is one getUsers/getChannels/getChats call by id and access
        hash, not a resolveUsername. Only Telethon's ValueError ("Could not
        find the input entity") falls back to a fresh resolve; a flood wait or
        a network error says nothing about the entry and propagates.
        """
        cached = self._cache.get(key)
        if not cached:
            return None
        peer = _peer_from_cached(cached)
        if peer is None:
            log.debug("cached entry has no usable peer, resolving fresh: %s", key)
            return None
        try:
            async with self.guard("other"):
                entity = await self.client.get_entity(peer)
        except ValueError as exc:
            log.debug("cache hit miss-resolve, refreshing: %s", exc)
            self._cache.invalidate(key)
            return None
        self._metrics.record_cache(hit=True)
        # Refresh entry so TTL slides forward.
        self._cache.put(key, entity)
        return entity

    async def start(self) -> None:
        if self._client is not None:
            return

        client = TelegramClient(
            StringSession(self._cfg.session),
            self._cfg.api_id,
            self._cfg.api_hash,
            device_model=self._cfg.device_model,
            system_version=self._cfg.system_version,
            app_version=self._cfg.app_version,
            request_retries=5,
            connection_retries=5,
            flood_sleep_threshold=self._cfg.flood_sleep_threshold,
            timeout=self._cfg.request_timeout,
            proxy=self._cfg.proxy,
        )

        log.info("connecting to Telegram")
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError(
                "TELETHON_SESSION is not authorized; generate a fresh one with the "
                "login command: https://psyb0t.github.io/docker-telethon-plus/getting-started/"
            )

        self._client = client
        self._observe_telethon_flood_sleeps()
        self._updates.attach(client)
        me = await client.get_me()
        log.info("authorized as id=%s username=%s", me.id, me.username)
        # Access hashes are per account, so the cache file belongs to one.
        self._cache.bind_account(me.id)
        log.info(
            "throttle=%s adaptive=%s cache=%s (%d entries) updates=%s read_only=%s dry_run=%s",
            self._cfg.throttle_enabled,
            self._cfg.throttle_adaptive,
            self._cfg.cache_enabled,
            self._cache.stats()["entries"],
            self._cfg.updates_enabled,
            self._cfg.read_only,
            self._cfg.dry_run,
        )

    def _observe_telethon_flood_sleeps(self) -> None:
        users_logger = logging.getLogger(TELETHON_USERS_LOGGER)
        self._users_logger_level = users_logger.level
        self._flood_observer = _TelethonFloodObserver(
            self._throttle,
            self._metrics,
            visible_level=telethon_log_level(self._cfg.log_level),
        )
        users_logger.addFilter(self._flood_observer)
        # The flood record is INFO and "telethon" runs at WARNING or above, so
        # without this the record would never be created.
        users_logger.setLevel(logging.INFO)

    def _stop_observing_telethon_flood_sleeps(self) -> None:
        if self._flood_observer is None:
            return
        users_logger = logging.getLogger(TELETHON_USERS_LOGGER)
        users_logger.removeFilter(self._flood_observer)
        users_logger.setLevel(self._users_logger_level)
        self._flood_observer = None

    async def stop(self) -> None:
        if self._client is None:
            return
        log.info("disconnecting Telethon client")
        self._stop_observing_telethon_flood_sleeps()
        await self._updates.stop()
        await self._client.disconnect()
        self._client = None
