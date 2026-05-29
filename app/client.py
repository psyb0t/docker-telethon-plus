"""Telethon client lifecycle, shared lock, throttling, entity cache."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

from app.cache import EntityCache
from app.config import Config
from app.metrics import MetricsRegistry
from app.throttle import BucketSpec, ThrottleConfig, Throttler
from app.updates import UpdateBroker

log = logging.getLogger(__name__)


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

        On FloodWaitError, we record the event for adaptive backoff before
        re-raising. Other RPCErrors pass through.
        """
        await self._throttle.acquire(bucket, chat_key, chat_kind=chat_kind)
        async with self._lock:
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

        # Cache hit: ask Telethon by id (cheap — uses its in-memory DB).
        cached = self._cache.get(stripped)
        if cached:
            self._metrics.record_cache(hit=True)
            try:
                async with self.guard("other"):
                    entity = await self.client.get_entity(cached["id"])
                # Refresh entry so TTL slides forward.
                self._cache.put(stripped, entity)
                return entity
            except Exception as exc:  # noqa: BLE001 — fall back to fresh resolve
                log.debug("cache hit miss-resolve, refreshing: %s", exc)
                self._cache.invalidate(stripped)

        # True miss — burns a resolve_username slot.
        self._metrics.record_cache(hit=False)
        async with self.guard("resolve_username"):
            entity = await self.client.get_entity(stripped)
        self._cache.put(stripped, entity)
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
        )

        log.info("connecting to Telegram")
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError(
                "TELETHON_SESSION is not authorized — generate a fresh one with "
                "the login helper (see README)."
            )

        self._client = client
        self._updates.attach(client)
        me = await client.get_me()
        log.info("authorized as id=%s username=%s", me.id, me.username)
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

    async def stop(self) -> None:
        if self._client is None:
            return
        log.info("disconnecting Telethon client")
        await self._updates.stop()
        await self._client.disconnect()
        self._client = None
