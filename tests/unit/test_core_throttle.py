"""Throttler races: bucket slots are booked before the sleep, and the pacing
runs inside the holder lock so queued requests cannot fire back to back."""

from __future__ import annotations

import asyncio
import time
from typing import Any, List

from app.throttle import BucketSpec, ThrottleConfig, Throttler, _Bucket

# Short windows and gaps keep the timing tests fast; the tolerance absorbs
# scheduler jitter without letting a missing wait pass.
BUCKET_LIMIT = 5
BUCKET_WINDOW = 0.4
GAP = 0.15
TOLERANCE = 0.9
# Far longer than any read-side wait in a test, so a read that wrongly waits
# on the send timeline is unmistakable.
LONG_SEND_GAP_MS = 5000
# Jitter makes every acquire() sleep between computing its wait and finishing,
# which is the window the old code let concurrent callers slip through.
JITTER_MS = 20


def _cfg(**overrides: Any) -> ThrottleConfig:
    base: dict[str, Any] = dict(
        enabled=True,
        global_interval_ms=0,
        jitter_ms=0,
        per_chat_interval_ms=0,
        per_chat_read_interval_ms=0,
        adaptive=False,
        buckets={
            "resolve_username": BucketSpec(BUCKET_LIMIT, BUCKET_WINDOW),
            "send": BucketSpec(100, 60),
            "read": BucketSpec(100, 60),
            "other": BucketSpec(100, 60),
        },
    )
    base.update(overrides)
    return ThrottleConfig(**base)


async def _times_of(count: int, call: Any) -> List[float]:
    """Seconds after the start at which each of `count` concurrent calls returned."""
    start = time.monotonic()
    done: List[float] = []

    async def one() -> None:
        await call()
        done.append(time.monotonic() - start)

    await asyncio.gather(*(one() for _ in range(count)))
    return sorted(done)


# ---------- bucket slots ---------------------------------------------------


async def test_concurrent_reserves_do_not_all_pass_one_bucket_window() -> None:
    """Regression: the slot was recorded after the sleep, so every concurrent
    caller saw the same empty window and passed together."""
    t = Throttler(_cfg())
    done = await _times_of(10, lambda: t.reserve("resolve_username"))
    at_once = [d for d in done if d < BUCKET_WINDOW / 2]
    assert len(at_once) == BUCKET_LIMIT, done
    assert all(d >= BUCKET_WINDOW * TOLERANCE for d in done[BUCKET_LIMIT:]), done


async def test_concurrent_acquires_do_not_all_pass_one_bucket_window() -> None:
    t = Throttler(_cfg(jitter_ms=JITTER_MS))
    done = await _times_of(10, lambda: t.acquire("resolve_username"))
    at_once = [d for d in done if d < BUCKET_WINDOW / 2]
    assert len(at_once) == BUCKET_LIMIT, done
    assert all(d >= BUCKET_WINDOW * TOLERANCE for d in done[BUCKET_LIMIT:]), done


async def test_callers_past_two_windows_wait_for_the_slot_a_window_back() -> None:
    """12 callers on a 5-per-window bucket pass as 5, 5 and 2: the third group
    waits for the second group's slots, not for the oldest hit."""
    t = Throttler(_cfg())
    done = await _times_of(12, lambda: t.reserve("resolve_username"))
    assert done[BUCKET_LIMIT * 2] >= BUCKET_WINDOW * 2 * TOLERANCE, done


def test_bucket_wait_counts_from_the_limit_th_newest_hit() -> None:
    bucket = _Bucket(BucketSpec(limit=5, window_seconds=10))
    for at in (0.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0):
        bucket.record(at)
    # Seven hits, limit five: the fifth newest is at 5.0, so the window has to
    # lapse from there. hits[0] (0.0) would answer 9.
    assert bucket.wait_seconds(now=1.0) == 14.0


def test_a_hit_recorded_behind_a_booked_slot_keeps_the_hits_ordered() -> None:
    bucket = _Bucket(BucketSpec(limit=2, window_seconds=10))
    bucket.record(30.0)
    bucket.record(1.0)
    assert list(bucket._hits) == [1.0, 30.0]


def test_record_extra_behind_a_booked_slot_keeps_the_bucket_ordered() -> None:
    t = Throttler(_cfg(buckets={"read": BucketSpec(1, 5), "other": BucketSpec(9, 60)}))
    booked_for = time.monotonic() + 30
    t._buckets["read"].record(booked_for)
    t.record_extra("read", 2)
    hits = list(t._buckets["read"]._hits)
    assert hits == sorted(hits)
    assert hits[-1] == booked_for


# ---------- pacing ---------------------------------------------------------


async def test_concurrent_acquires_for_one_chat_are_spaced_by_the_chat_gap() -> None:
    """Regression: the per-chat timestamp was recorded after the sleep, so
    concurrent callers all computed their wait from the same old timestamp."""
    t = Throttler(_cfg(per_chat_interval_ms=int(GAP * 1000)))
    done = await _times_of(3, lambda: t.acquire("send", "chat", chat_kind="send"))
    gaps = [b - a for a, b in zip(done, done[1:])]
    assert all(g >= GAP * TOLERANCE for g in gaps), done


async def test_concurrent_paces_are_spaced_by_the_global_gap() -> None:
    t = Throttler(_cfg(global_interval_ms=int(GAP * 1000)))
    done = await _times_of(3, lambda: t.pace())
    gaps = [b - a for a, b in zip(done, done[1:])]
    assert all(g >= GAP * TOLERANCE for g in gaps), done


async def test_concurrent_read_paces_use_the_read_gap_not_the_send_gap() -> None:
    t = Throttler(
        _cfg(
            per_chat_interval_ms=LONG_SEND_GAP_MS,
            per_chat_read_interval_ms=int(GAP * 1000),
        )
    )
    done = await _times_of(3, lambda: t.pace("chat", chat_kind="read"))
    assert done[-1] < LONG_SEND_GAP_MS / 1000 / 2, done
    gaps = [b - a for a, b in zip(done, done[1:])]
    assert all(g >= GAP * TOLERANCE for g in gaps), done


async def test_the_adaptive_multiplier_widens_concurrent_pacing() -> None:
    t = Throttler(_cfg(per_chat_interval_ms=int(GAP * 1000), adaptive=True))
    t.notify_flood(30, "send")  # 2x
    done = await _times_of(2, lambda: t.pace("chat", chat_kind="send"))
    assert done[1] - done[0] >= GAP * 2 * TOLERANCE, done


async def test_disabled_throttler_reserve_and_pace_are_noops() -> None:
    t = Throttler(
        _cfg(
            enabled=False,
            global_interval_ms=10000,
            per_chat_interval_ms=10000,
            buckets={"read": BucketSpec(1, 60), "other": BucketSpec(1, 60)},
        )
    )
    start = time.monotonic()
    for _ in range(3):
        await t.reserve("read")
        await t.pace("chat")
    assert time.monotonic() - start < 0.05


# ---------- guard: pacing happens inside the holder lock ---------------------


def _holder_throttler() -> Throttler:
    return Throttler(_cfg(per_chat_interval_ms=int(GAP * 1000)))


async def test_guards_queued_behind_a_lock_holder_are_spaced_by_the_chat_gap(holder: Any) -> None:
    """Regression: guard() paced before taking the holder lock, so requests
    that queued on the lock had all passed their pacing and fired back to
    back once it opened."""
    holder._throttle = _holder_throttler()
    sent_at: List[float] = []

    async def send() -> None:
        async with holder.guard("send", chat_key="chat", chat_kind="send"):
            sent_at.append(time.monotonic())

    await holder.lock.acquire()  # a request already in flight
    tasks = [asyncio.create_task(send()) for _ in range(2)]
    # Longer than the gap: pacing done before the lock would be over by now.
    await asyncio.sleep(GAP * 2)
    holder.lock.release()
    await asyncio.gather(*tasks)

    assert sent_at[1] - sent_at[0] >= GAP * TOLERANCE, sent_at


async def test_a_long_bucket_wait_does_not_hold_the_lock_for_other_calls(holder: Any) -> None:
    holder._throttle = Throttler(
        _cfg(
            buckets={
                "join": BucketSpec(1, BUCKET_WINDOW),
                "other": BucketSpec(100, 60),
            }
        )
    )

    async def join() -> None:
        async with holder.guard("join"):
            pass

    await join()
    waiting = asyncio.create_task(join())
    await asyncio.sleep(0.05)  # now sleeping on the bucket

    start = time.monotonic()
    async with holder.guard("other"):
        pass
    assert time.monotonic() - start < BUCKET_WINDOW / 2

    await waiting


async def test_a_chat_waiting_for_its_gap_does_not_block_other_chats() -> None:
    """A send waiting out its per-chat gap must not hold the client lock:
    the gap is booked in reserve(), before the lock is taken."""
    import time as _time

    throttle = Throttler(
        ThrottleConfig(
            enabled=True,
            global_interval_ms=0,
            jitter_ms=0,
            per_chat_interval_ms=500,
            per_chat_read_interval_ms=0,
            adaptive=False,
        )
    )
    lock = asyncio.Lock()

    async def call(chat: str) -> float:
        await throttle.reserve("send", chat, chat_kind="send")
        async with lock:
            await throttle.pace(chat, chat_kind="send")
            return _time.monotonic()

    start = _time.monotonic()
    await call("chat-a")
    second_a, other = await asyncio.gather(call("chat-a"), call("chat-b"))
    assert other - start < 0.2, "chat-b waited behind chat-a's per-chat gap"
    assert second_a - start >= 0.45
