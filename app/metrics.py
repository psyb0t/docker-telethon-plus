"""Prometheus-format metrics exposition (no extra dependencies).

We emit OpenMetrics-style text directly, sourcing values live from the
Throttler, EntityCache, and a tiny in-process counter set. Kept zero-dep
to avoid pulling prometheus_client into the runtime image just for this.
"""

from __future__ import annotations

import time
from collections import Counter
from threading import RLock
from typing import Any, Dict, Iterable, List, Tuple


class MetricsRegistry:
    """Counts what we care about across the request lifecycle."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._tool_calls: Counter[str] = Counter()
        self._tool_errors: Counter[str] = Counter()
        self._flood_events: Counter[str] = Counter()
        self._cache_hits = 0
        self._cache_misses = 0
        self._started_at = time.time()

    def record_tool_call(self, name: str) -> None:
        with self._lock:
            self._tool_calls[name] += 1

    def record_tool_error(self, name: str) -> None:
        with self._lock:
            self._tool_errors[name] += 1

    def record_flood(self, bucket: str) -> None:
        with self._lock:
            self._flood_events[bucket] += 1

    def record_cache(self, hit: bool) -> None:
        with self._lock:
            if hit:
                self._cache_hits += 1
            else:
                self._cache_misses += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "tool_calls": dict(self._tool_calls),
                "tool_errors": dict(self._tool_errors),
                "flood_events": dict(self._flood_events),
                "cache": {"hits": self._cache_hits, "misses": self._cache_misses},
                "uptime_seconds": int(time.time() - self._started_at),
            }


def _fmt(name: str, help_text: str, mtype: str, samples: Iterable[Tuple[str, float]]) -> str:
    out: List[str] = [f"# HELP {name} {help_text}", f"# TYPE {name} {mtype}"]
    for labels, value in samples:
        out.append(f"{name}{labels} {value}")
    return "\n".join(out) + "\n"


def render(
    registry: MetricsRegistry,
    throttle_state: Dict[str, Any],
    cache_state: Dict[str, int],
) -> str:
    snap = registry.snapshot()
    blocks: List[str] = []

    blocks.append(_fmt(
        "telethon_plus_uptime_seconds",
        "Process uptime in seconds.",
        "counter",
        [("", snap["uptime_seconds"])],
    ))

    blocks.append(_fmt(
        "telethon_plus_tool_calls_total",
        "Total tool invocations (successful or otherwise).",
        "counter",
        [(_lbl(tool=t), v) for t, v in snap["tool_calls"].items()],
    ))

    blocks.append(_fmt(
        "telethon_plus_tool_errors_total",
        "Total tool invocations that raised an exception.",
        "counter",
        [(_lbl(tool=t), v) for t, v in snap["tool_errors"].items()],
    ))

    blocks.append(_fmt(
        "telethon_plus_flood_events_total",
        "FLOOD_WAIT events observed, by throttle bucket.",
        "counter",
        [(_lbl(bucket=b), v) for b, v in snap["flood_events"].items()],
    ))

    blocks.append(_fmt(
        "telethon_plus_cache_hits_total",
        "Entity cache hits.",
        "counter",
        [("", snap["cache"]["hits"])],
    ))

    blocks.append(_fmt(
        "telethon_plus_cache_misses_total",
        "Entity cache misses.",
        "counter",
        [("", snap["cache"]["misses"])],
    ))

    blocks.append(_fmt(
        "telethon_plus_cache_entries",
        "Entries currently in entity cache.",
        "gauge",
        [("", cache_state.get("entries", 0))],
    ))

    blocks.append(_fmt(
        "telethon_plus_throttle_multiplier",
        "Current adaptive backoff multiplier.",
        "gauge",
        [("", float(throttle_state.get("multiplier", 1.0)))],
    ))

    buckets = throttle_state.get("buckets", {}) or {}
    samples_used = []
    samples_limit = []
    for name, info in buckets.items():
        samples_used.append((_lbl(bucket=name), info.get("used", 0)))
        samples_limit.append((_lbl(bucket=name), info.get("limit", 0)))
    if samples_used:
        blocks.append(_fmt(
            "telethon_plus_bucket_used",
            "Current bucket usage in its window.",
            "gauge",
            samples_used,
        ))
        blocks.append(_fmt(
            "telethon_plus_bucket_limit",
            "Bucket capacity per window.",
            "gauge",
            samples_limit,
        ))

    return "\n".join(blocks)


def _lbl(**kwargs: str) -> str:
    if not kwargs:
        return ""
    inner = ",".join(f'{k}="{_escape(str(v))}"' for k, v in kwargs.items())
    return "{" + inner + "}"


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
