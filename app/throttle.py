"""Adaptive request throttling.

Four layers of defense against Telegram FLOOD_WAITs and account bans:

1. Per-method token buckets — conservative caps for the dangerous methods
   (resolveUsername, getFullChannel, joinChannel, createChannel, etc.).
   Telegram enforces per-method rate limits server-side; we want to stay
   well under those soft caps so we never even hit them.

2. Per-chat *send* interval — minimum gap between two sends to the same
   chat. Default 1100 ms keeps us under Telegram's "1 msg/sec/chat"
   ceiling with margin.

3. Per-chat *read* interval — separate, smaller gap between two reads
   from the same chat. Prevents single-channel scraping from monopolizing
   the read bucket. Default 250 ms (4 reads/sec/chat).

4. Global gap + jitter — small mandatory delay between any two outgoing
   requests, with random jitter so traffic doesn't look like a metronome.

On top of those, adaptive backoff: every FLOOD_WAIT observed bumps a
global multiplier that decays over hours. Hit one FLOOD_WAIT → all waits
×2 for an hour. Hit three in a window → ×8, etc. Auto-recovers.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BucketSpec:
    """Token bucket: at most `limit` requests per `window_seconds`."""

    limit: int
    window_seconds: float


# Conservative defaults based on community-observed Telegram thresholds.
# These are NOT the actual server limits — they're safe margins below them.
DEFAULT_BUCKETS: Dict[str, BucketSpec] = {
    # New-entity resolution is the dangerous one — burns the daily quota.
    "resolve_username": BucketSpec(limit=5, window_seconds=60),
    # Full info fetches are expensive and tracked.
    "get_full": BucketSpec(limit=10, window_seconds=60),
    # Joining/leaving/creating are heavily limited.
    "join": BucketSpec(limit=5, window_seconds=3600),
    "create": BucketSpec(limit=5, window_seconds=3600),
    # Sends are per-chat-rate-limited, this is the global ceiling.
    "send": BucketSpec(limit=20, window_seconds=60),
    # Reads can paginate at 100/page on the server side — every page is one
    # server hit. Bucket is sized assuming honest page-aware accounting,
    # so 600/min ≈ 10 hits/sec which is well under Telegram's GetHistory
    # ceiling and matches what bulk-scrape jobs realistically need.
    "read": BucketSpec(limit=600, window_seconds=60),
    # Catch-all for anything we don't classify.
    "other": BucketSpec(limit=60, window_seconds=60),
}


@dataclass
class ThrottleConfig:
    enabled: bool = True
    global_interval_ms: int = 50
    jitter_ms: int = 200
    per_chat_interval_ms: int = 1100        # sends to the same chat
    per_chat_read_interval_ms: int = 250    # reads from the same chat
    adaptive: bool = True
    # Buckets — name -> (limit, window_seconds).
    buckets: Dict[str, BucketSpec] = field(default_factory=lambda: dict(DEFAULT_BUCKETS))


class _Bucket:
    """Token bucket implemented as a sliding-window timestamp deque."""

    __slots__ = ("spec", "_hits")

    def __init__(self, spec: BucketSpec) -> None:
        self.spec = spec
        self._hits: Deque[float] = deque()

    def _evict(self, now: float) -> None:
        cutoff = now - self.spec.window_seconds
        while self._hits and self._hits[0] < cutoff:
            self._hits.popleft()

    def wait_seconds(self, now: float) -> float:
        self._evict(now)
        if len(self._hits) < self.spec.limit:
            return 0.0
        oldest = self._hits[0]
        return max(0.0, (oldest + self.spec.window_seconds) - now)

    def record(self, now: float) -> None:
        self._hits.append(now)


class Throttler:
    """Coordinates all throttling layers + adaptive backoff."""

    # chat_kind values for acquire()
    SEND = "send"
    READ = "read"

    def __init__(self, cfg: ThrottleConfig) -> None:
        self._cfg = cfg
        self._buckets: Dict[str, _Bucket] = {
            name: _Bucket(spec) for name, spec in cfg.buckets.items()
        }
        self._last_request_at: float = 0.0
        # Per-chat last-action timestamps, split by kind so a heavy send
        # doesn't make subsequent reads on the same chat wait 1100ms.
        self._chat_last_sent_at: Dict[str, float] = defaultdict(float)
        self._chat_last_read_at: Dict[str, float] = defaultdict(float)
        # Adaptive backoff state.
        self._flood_events: Deque[float] = deque()
        self._lock = asyncio.Lock()

    def _multiplier(self, now: float) -> float:
        """Decay flood events older than 1h; return 2^(remaining count), capped."""
        if not self._cfg.adaptive:
            return 1.0
        cutoff = now - 3600
        while self._flood_events and self._flood_events[0] < cutoff:
            self._flood_events.popleft()
        count = len(self._flood_events)
        if count == 0:
            return 1.0
        return float(min(2 ** count, 64))

    def _jitter_seconds(self) -> float:
        if self._cfg.jitter_ms <= 0:
            return 0.0
        return random.uniform(0, self._cfg.jitter_ms) / 1000.0

    async def acquire(
        self,
        method_bucket: str,
        chat_key: Optional[str] = None,
        chat_kind: str = SEND,
    ) -> None:
        """Block until it's safe to issue one request of the given class.

        `chat_kind` selects which per-chat timeline applies: sends and reads
        use different intervals and different last-touched dicts.
        """
        if not self._cfg.enabled:
            return

        async with self._lock:
            now = time.monotonic()
            mult = self._multiplier(now)
            waits = []

            bucket = self._buckets.get(method_bucket) or self._buckets["other"]
            bucket_wait = bucket.wait_seconds(now)
            if bucket_wait > 0:
                waits.append(("bucket:" + method_bucket, bucket_wait))

            global_gap = (self._cfg.global_interval_ms / 1000.0) * mult
            global_wait = max(0.0, (self._last_request_at + global_gap) - now)
            if global_wait > 0:
                waits.append(("global", global_wait))

            if chat_key:
                if chat_kind == self.READ:
                    chat_gap = (self._cfg.per_chat_read_interval_ms / 1000.0) * mult
                    last = self._chat_last_read_at.get(chat_key, 0.0)
                else:
                    chat_gap = (self._cfg.per_chat_interval_ms / 1000.0) * mult
                    last = self._chat_last_sent_at.get(chat_key, 0.0)
                chat_wait = max(0.0, (last + chat_gap) - now)
                if chat_wait > 0:
                    waits.append((f"chat[{chat_kind}]:{chat_key}", chat_wait))

            total = max((w for _, w in waits), default=0.0) + self._jitter_seconds()

            if total > 0 and waits:
                reasons = ", ".join(f"{n}={w * 1000:.0f}ms" for n, w in waits)
                log.debug("throttle sleep %.0fms (mult=%.1fx, %s)", total * 1000, mult, reasons)

        if total > 0:
            await asyncio.sleep(total)

        async with self._lock:
            now = time.monotonic()
            bucket.record(now)
            self._last_request_at = now
            if chat_key:
                if chat_kind == self.READ:
                    self._chat_last_read_at[chat_key] = now
                else:
                    self._chat_last_sent_at[chat_key] = now

    def record_extra(self, method_bucket: str, count: int) -> None:
        """Charge the bucket for N additional server hits that we've already
        completed (e.g. Telethon's internal pagination inside an iter_* call
        that we couldn't intercept at the page boundary).

        Doesn't sleep. Just counts.
        """
        if not self._cfg.enabled or count <= 0:
            return
        bucket = self._buckets.get(method_bucket) or self._buckets["other"]
        now = time.monotonic()
        for _ in range(count):
            bucket.record(now)

    def notify_flood(self, seconds: float, method_bucket: str) -> None:
        """Record a server-side FLOOD_WAIT — feeds the adaptive multiplier."""
        if not self._cfg.adaptive:
            return
        self._flood_events.append(time.monotonic())
        log.warning(
            "FLOOD_WAIT observed: %.0fs on bucket=%s — adaptive backoff bumped to %.1fx",
            seconds,
            method_bucket,
            self._multiplier(time.monotonic()),
        )

    def snapshot(self) -> Dict[str, object]:
        """Live state for /api/throttle/status and /metrics."""
        now = time.monotonic()
        mult = self._multiplier(now)
        buckets: Dict[str, Dict[str, int]] = {}
        for name, bucket in self._buckets.items():
            bucket._evict(now)
            buckets[name] = {
                "used": len(bucket._hits),
                "limit": bucket.spec.limit,
                "window_seconds": int(bucket.spec.window_seconds),
            }
        return {
            "enabled": self._cfg.enabled,
            "adaptive": self._cfg.adaptive,
            "multiplier": mult,
            "flood_events_1h": len(self._flood_events),
            "global_interval_ms": self._cfg.global_interval_ms,
            "per_chat_interval_ms": self._cfg.per_chat_interval_ms,
            "per_chat_read_interval_ms": self._cfg.per_chat_read_interval_ms,
            "jitter_ms": self._cfg.jitter_ms,
            "tracked_chats_send": len(self._chat_last_sent_at),
            "tracked_chats_read": len(self._chat_last_read_at),
            "buckets": buckets,
        }
