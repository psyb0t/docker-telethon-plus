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
import bisect
import logging
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional

log = logging.getLogger(__name__)

_MS_PER_SECOND = 1000.0


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
    per_chat_interval_ms: int = 1100  # sends to the same chat
    per_chat_read_interval_ms: int = 250  # reads from the same chat
    adaptive: bool = True
    # Buckets — name -> (limit, window_seconds).
    buckets: Dict[str, BucketSpec] = field(default_factory=lambda: dict(DEFAULT_BUCKETS))


class _Bucket:
    """Token bucket implemented as a sliding-window timestamp deque.

    Hits are kept sorted. A hit may be stamped in the future: reserve() books
    the slot a caller will use while it is still sleeping toward it.
    """

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
        # Booked slots can push the deque past `limit` entries. A new hit is
        # only legal once the limit-th newest hit has left the window, and
        # that is not hits[0] when more than `limit` slots are booked.
        blocking = self._hits[-self.spec.limit]
        return max(0.0, (blocking + self.spec.window_seconds) - now)

    def record(self, at: float) -> None:
        # insort keeps the order when record_extra stamps "now" behind a
        # slot that reserve() booked in the future.
        bisect.insort(self._hits, at)


class Throttler:
    """Coordinates all throttling layers + adaptive backoff."""

    # chat_kind values for pace() and acquire()
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
        # Per-chat slots booked by reserve(), ahead of the holder lock, so
        # callers for one chat are spaced before they queue on it.
        self._chat_booked_send: Dict[str, float] = defaultdict(float)
        self._chat_booked_read: Dict[str, float] = defaultdict(float)
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
        return float(min(2**count, 64))

    def _chat_gap(self, chat_kind: str, mult: float) -> float:
        interval_ms = (
            self._cfg.per_chat_read_interval_ms
            if chat_kind == self.READ
            else self._cfg.per_chat_interval_ms
        )
        return (interval_ms / _MS_PER_SECOND) * mult

    def _jitter_seconds(self) -> float:
        if self._cfg.jitter_ms <= 0:
            return 0.0
        # Timing jitter to break up request patterns, not a secret.
        return random.uniform(0, self._cfg.jitter_ms) / _MS_PER_SECOND  # nosec B311

    async def reserve(
        self,
        method_bucket: str,
        chat_key: Optional[str] = None,
        chat_kind: str = SEND,
    ) -> None:
        """Take one slot in the method's token bucket, and in the chat's
        timeline when `chat_key` is given, sleeping until both open.

        Slots are booked at their future time under the lock, before the
        sleep. Booking after the sleep let every concurrent caller see the
        same empty window and pass together. This runs before the holder
        lock, so a long wait here never blocks calls for other chats.
        """
        if not self._cfg.enabled:
            return

        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(method_bucket) or self._buckets["other"]
            wait = bucket.wait_seconds(now)
            if chat_key:
                booked = (
                    self._chat_booked_read if chat_kind == self.READ else self._chat_booked_send
                )
                gap = self._chat_gap(chat_kind, self._multiplier(now))
                wait = max(wait, booked[chat_key] + gap - now)
                booked[chat_key] = now + wait
            bucket.record(now + wait)

        if wait <= 0:
            return
        log.debug("throttle bucket sleep %.0fms (bucket=%s)", wait * 1000, method_bucket)
        await asyncio.sleep(wait)

    async def pace(
        self,
        chat_key: Optional[str] = None,
        chat_kind: str = SEND,
    ) -> None:
        """Sleep until the global gap, the per-chat gap and the jitter allow
        the request to go out, then return so the caller sends it.

        `chat_kind` selects which per-chat timeline applies: sends and reads
        use different intervals and different last-touched dicts. The time the
        request will go out is stamped under the lock before sleeping, so the
        next caller measures its gap from this request and not from the
        moment this one woke up. reserve() has normally spaced a chat's
        callers already, so the per-chat wait here is zero unless a long
        call holding the holder lock bunched them up.
        """
        if not self._cfg.enabled:
            return

        async with self._lock:
            now = time.monotonic()
            mult = self._multiplier(now)
            waits = []

            global_gap = (self._cfg.global_interval_ms / _MS_PER_SECOND) * mult
            global_wait = max(0.0, (self._last_request_at + global_gap) - now)
            if global_wait > 0:
                waits.append(("global", global_wait))

            if chat_key:
                chat_gap = self._chat_gap(chat_kind, mult)
                if chat_kind == self.READ:
                    last = self._chat_last_read_at.get(chat_key, 0.0)
                else:
                    last = self._chat_last_sent_at.get(chat_key, 0.0)
                chat_wait = max(0.0, (last + chat_gap) - now)
                if chat_wait > 0:
                    waits.append((f"chat[{chat_kind}]:{chat_key}", chat_wait))

            total = max((w for _, w in waits), default=0.0) + self._jitter_seconds()
            goes_out_at = now + total
            self._last_request_at = goes_out_at
            if chat_key:
                if chat_kind == self.READ:
                    self._chat_last_read_at[chat_key] = goes_out_at
                else:
                    self._chat_last_sent_at[chat_key] = goes_out_at

            if total > 0 and waits:
                reasons = ", ".join(f"{n}={w * 1000:.0f}ms" for n, w in waits)
                log.debug("throttle sleep %.0fms (mult=%.1fx, %s)", total * 1000, mult, reasons)

        if total > 0:
            await asyncio.sleep(total)

    async def acquire(
        self,
        method_bucket: str,
        chat_key: Optional[str] = None,
        chat_kind: str = SEND,
    ) -> None:
        """Block until it's safe to issue one request of the given class:
        reserve() the bucket slot, then pace() the request.

        TelethonHolder.guard() calls the two halves separately so the pacing
        happens inside the holder lock.
        """
        await self.reserve(method_bucket, chat_key, chat_kind=chat_kind)
        await self.pace(chat_key, chat_kind=chat_kind)

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

    def snapshot(self) -> Dict[str, Any]:
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
