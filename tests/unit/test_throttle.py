"""Throttler unit tests — verifies all four layers + adaptive backoff."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.throttle import BucketSpec, Throttler, ThrottleConfig


def _cfg(**overrides) -> ThrottleConfig:
    base = dict(
        enabled=True,
        global_interval_ms=0,
        jitter_ms=0,
        per_chat_interval_ms=0,
        per_chat_read_interval_ms=0,
        adaptive=False,
        buckets={
            "send": BucketSpec(100, 60),
            "read": BucketSpec(100, 60),
            "resolve_username": BucketSpec(100, 60),
            "other": BucketSpec(100, 60),
        },
    )
    base.update(overrides)
    return ThrottleConfig(**base)


# ---------- Bucket caps -----------------------------------------------------


async def test_bucket_blocks_at_limit() -> None:
    t = Throttler(_cfg(buckets={"read": BucketSpec(3, 1), "other": BucketSpec(99, 60)}))
    start = time.monotonic()
    for _ in range(3):
        await t.acquire("read")
    elapsed_3 = time.monotonic() - start
    assert elapsed_3 < 0.05, f"first 3 acquires should be instant, took {elapsed_3*1000:.0f}ms"

    # 4th must wait until the window slides forward.
    await t.acquire("read")
    elapsed_4 = time.monotonic() - start
    assert 0.95 < elapsed_4 < 1.2, f"4th acquire should wait ~1s, took {elapsed_4*1000:.0f}ms"


async def test_unknown_bucket_falls_back_to_other() -> None:
    t = Throttler(_cfg(buckets={"other": BucketSpec(2, 1)}))
    await t.acquire("totally_unknown_bucket_name")
    await t.acquire("totally_unknown_bucket_name")
    # 3rd would block — verify by checking timing on a short blocking call.
    start = time.monotonic()
    await asyncio.wait_for(t.acquire("totally_unknown_bucket_name"), timeout=2.0)
    assert (time.monotonic() - start) > 0.9


# ---------- Per-chat send vs read intervals ---------------------------------


async def test_per_chat_send_and_read_timelines_are_independent() -> None:
    t = Throttler(_cfg(per_chat_interval_ms=500, per_chat_read_interval_ms=100))
    # send → read on same chat must NOT inherit the send's interval
    start = time.monotonic()
    await t.acquire("send", "chatA", chat_kind="send")
    await t.acquire("read", "chatA", chat_kind="read")
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"different timelines should not block each other; got {elapsed*1000:.0f}ms"


async def test_read_per_chat_interval_enforced() -> None:
    t = Throttler(_cfg(per_chat_read_interval_ms=200))
    start = time.monotonic()
    await t.acquire("read", "chatB", chat_kind="read")
    await t.acquire("read", "chatB", chat_kind="read")
    elapsed = time.monotonic() - start
    assert 0.18 < elapsed < 0.30, f"two same-chat reads should wait ~200ms, got {elapsed*1000:.0f}ms"


async def test_send_per_chat_interval_enforced() -> None:
    t = Throttler(_cfg(per_chat_interval_ms=300))
    start = time.monotonic()
    await t.acquire("send", "chatC", chat_kind="send")
    await t.acquire("send", "chatC", chat_kind="send")
    elapsed = time.monotonic() - start
    assert 0.28 < elapsed < 0.40, f"two same-chat sends should wait ~300ms, got {elapsed*1000:.0f}ms"


async def test_per_chat_does_not_affect_other_chats() -> None:
    t = Throttler(_cfg(per_chat_read_interval_ms=500))
    start = time.monotonic()
    await t.acquire("read", "chatX", chat_kind="read")
    await t.acquire("read", "chatY", chat_kind="read")
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, "different chats should not block each other"


# ---------- Global interval -------------------------------------------------


async def test_global_interval_enforced() -> None:
    t = Throttler(_cfg(global_interval_ms=100))
    start = time.monotonic()
    await t.acquire("read")
    await t.acquire("read")
    elapsed = time.monotonic() - start
    assert 0.08 < elapsed < 0.16, f"global gap should produce ~100ms, got {elapsed*1000:.0f}ms"


# ---------- Adaptive backoff ------------------------------------------------


async def test_adaptive_backoff_multiplier_doubles_on_flood() -> None:
    t = Throttler(_cfg(global_interval_ms=50, adaptive=True))
    await t.acquire("read")
    t.notify_flood(30, "read")
    snap = t.snapshot()
    assert snap["multiplier"] == 2.0

    t.notify_flood(30, "read")
    snap = t.snapshot()
    assert snap["multiplier"] == 4.0

    t.notify_flood(30, "read")
    assert t.snapshot()["multiplier"] == 8.0


async def test_adaptive_backoff_disabled_when_flag_off() -> None:
    t = Throttler(_cfg(adaptive=False))
    t.notify_flood(30, "read")
    assert t.snapshot()["multiplier"] == 1.0


async def test_adaptive_multiplier_applied_to_global_gap() -> None:
    t = Throttler(_cfg(global_interval_ms=100, adaptive=True))
    await t.acquire("read")
    t.notify_flood(30, "read")  # mult becomes 2

    start = time.monotonic()
    await t.acquire("read")
    elapsed = time.monotonic() - start
    assert 0.18 < elapsed < 0.28, f"global gap should double to ~200ms, got {elapsed*1000:.0f}ms"


# ---------- record_extra ----------------------------------------------------


async def test_record_extra_bumps_bucket_without_sleeping() -> None:
    t = Throttler(_cfg(buckets={"read": BucketSpec(10, 60), "other": BucketSpec(99, 60)}))
    assert t.snapshot()["buckets"]["read"]["used"] == 0

    start = time.monotonic()
    t.record_extra("read", 5)
    elapsed = time.monotonic() - start
    assert elapsed < 0.01, "record_extra must not sleep"
    assert t.snapshot()["buckets"]["read"]["used"] == 5


async def test_record_extra_respects_zero_or_negative() -> None:
    t = Throttler(_cfg(buckets={"read": BucketSpec(10, 60), "other": BucketSpec(99, 60)}))
    t.record_extra("read", 0)
    t.record_extra("read", -3)
    assert t.snapshot()["buckets"]["read"]["used"] == 0


# ---------- Disabled mode skips everything ----------------------------------


async def test_disabled_throttler_is_a_noop() -> None:
    t = Throttler(_cfg(
        enabled=False,
        per_chat_interval_ms=10000,
        global_interval_ms=10000,
        buckets={"read": BucketSpec(1, 60), "other": BucketSpec(1, 60)},
    ))
    start = time.monotonic()
    for _ in range(5):
        await t.acquire("read", "any_chat", chat_kind="send")
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"disabled throttler should not sleep, got {elapsed*1000:.0f}ms"


# ---------- snapshot --------------------------------------------------------


async def test_snapshot_shape() -> None:
    t = Throttler(_cfg())
    await t.acquire("read", "abc", chat_kind="read")
    await t.acquire("send", "xyz", chat_kind="send")
    snap = t.snapshot()
    assert snap["enabled"] is True
    assert "multiplier" in snap
    assert "buckets" in snap
    assert snap["buckets"]["read"]["used"] == 1
    assert snap["buckets"]["send"]["used"] == 1
    assert snap["tracked_chats_read"] == 1
    assert snap["tracked_chats_send"] == 1
