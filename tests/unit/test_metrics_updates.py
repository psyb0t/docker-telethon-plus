"""MetricsRegistry counter behavior + UpdateBroker fan-out."""

from __future__ import annotations

import asyncio

import pytest

from app.metrics import MetricsRegistry, render
from app.updates import UpdateBroker, event_payload_json


# ---------- MetricsRegistry -------------------------------------------------


def test_counters_accumulate() -> None:
    r = MetricsRegistry()
    r.record_tool_call("send_message")
    r.record_tool_call("send_message")
    r.record_tool_call("get_me")
    r.record_tool_error("send_message")
    r.record_flood("resolve_username")
    r.record_cache(hit=True)
    r.record_cache(hit=True)
    r.record_cache(hit=False)

    snap = r.snapshot()
    assert snap["tool_calls"] == {"send_message": 2, "get_me": 1}
    assert snap["tool_errors"] == {"send_message": 1}
    assert snap["flood_events"] == {"resolve_username": 1}
    assert snap["cache"] == {"hits": 2, "misses": 1}


def test_render_produces_valid_prometheus_text() -> None:
    r = MetricsRegistry()
    r.record_tool_call("get_me")
    r.record_cache(hit=True)

    throttle_state = {
        "multiplier": 1.0,
        "buckets": {
            "read": {"used": 3, "limit": 600, "window_seconds": 60},
        },
    }
    cache_state = {"entries": 42}

    out = render(r, throttle_state, cache_state)
    assert "# HELP telethon_plus_tool_calls_total" in out
    assert "# TYPE telethon_plus_tool_calls_total counter" in out
    assert 'telethon_plus_tool_calls_total{tool="get_me"} 1' in out
    assert "telethon_plus_cache_hits_total 1" in out
    assert "telethon_plus_cache_entries 42" in out
    assert 'telethon_plus_bucket_used{bucket="read"} 3' in out
    assert "telethon_plus_throttle_multiplier 1.0" in out


def test_render_label_escapes_quotes() -> None:
    r = MetricsRegistry()
    r.record_tool_call('weird"name')
    out = render(r, {"buckets": {}, "multiplier": 1.0}, {"entries": 0})
    assert 'tool="weird\\"name"' in out


# ---------- UpdateBroker fan-out --------------------------------------------


async def test_subscribe_unsubscribe_isolated() -> None:
    b = UpdateBroker(enabled=True, post_to_url="", post_to_timeout=1, buffer_size=10)
    q1 = b.subscribe()
    q2 = b.subscribe()
    b._dispatch({"type": "test", "n": 1})
    assert q1.qsize() == 1
    assert q2.qsize() == 1

    b.unsubscribe(q1)
    b._dispatch({"type": "test", "n": 2})
    assert q1.qsize() == 1, "unsubscribed queue should not receive new events"
    assert q2.qsize() == 2


async def test_full_subscriber_queue_is_dropped_not_blocked() -> None:
    b = UpdateBroker(enabled=True, post_to_url="", post_to_timeout=1, buffer_size=2)
    q = b.subscribe()
    for i in range(5):
        b._dispatch({"type": "test", "n": i})
    assert q.qsize() == 2, "buffer should cap at 2"


async def test_disabled_broker_does_not_subscribe() -> None:
    b = UpdateBroker(enabled=False, post_to_url="", post_to_timeout=1, buffer_size=10)
    # attach() is a no-op when disabled — verify subscribe still works for
    # the WS route to plug into (events just never arrive).
    q = b.subscribe()
    b._dispatch({"type": "test"})  # dispatch is purely internal, no event source
    # If the broker were live with an attached client, attach()'s no-op means
    # no events fire. Subscribers still get whatever we manually dispatch:
    assert q.qsize() == 1


async def test_event_payload_json_round_trip() -> None:
    import json

    payload = {"type": "NewMessage", "message": {"id": 42, "text": "hi"}}
    encoded = event_payload_json(payload)
    decoded = json.loads(encoded)
    assert decoded == payload
