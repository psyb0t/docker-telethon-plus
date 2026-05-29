"""Tools tests with a mocked TelegramClient.

Covers the contracts that matter most for safety:
- get_messages pages correctly (≤100 per server call, advances offset_id,
  stops on short page)
- read_only mode blocks writes with PermissionError
- dry_run echoes the request without calling Telegram
- bulk_resolve isolates per-handle errors
- throttle_status / account_health surface state
- get_dialogs records extra bucket slots for pagination
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------- get_messages pagination ----------------------------------------


async def test_get_messages_pages_below_threshold_makes_one_call(holder, make_entity, make_message) -> None:
    from app.tools import REGISTRY

    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(
        return_value=[make_message(i) for i in range(10, 0, -1)]
    )

    params = REGISTRY["get_messages"].params_model.model_validate(
        {"chat": "@known", "limit": 10}
    )
    # populate cache so resolve_entity skips resolveUsername bucket
    holder.cache.put("@known", make_entity(1, username="known"))

    result = await REGISTRY["get_messages"].handler(holder, params)
    assert len(result) == 10
    assert holder._client.get_messages.await_count == 1


async def test_get_messages_pages_above_threshold(holder, make_entity, make_message) -> None:
    """limit=200 → 2 server calls (100 + 100)."""
    from app.tools import REGISTRY

    holder._client.get_entity = AsyncMock(return_value=make_entity(1))

    page_1 = [make_message(i) for i in range(1000, 900, -1)]
    page_2 = [make_message(i) for i in range(900, 800, -1)]
    holder._client.get_messages = AsyncMock(side_effect=[page_1, page_2])
    holder.cache.put("@bigchan", make_entity(1, username="bigchan"))

    params = REGISTRY["get_messages"].params_model.model_validate(
        {"chat": "@bigchan", "limit": 200}
    )
    result = await REGISTRY["get_messages"].handler(holder, params)

    assert len(result) == 200
    assert holder._client.get_messages.await_count == 2

    # Verify offset_id advances between pages.
    calls = holder._client.get_messages.await_args_list
    assert calls[0].kwargs["offset_id"] == 0
    assert calls[1].kwargs["offset_id"] == 901


async def test_get_messages_stops_on_short_page(holder, make_entity, make_message) -> None:
    """Server says we asked for 100 and got 30 — that means EOF, no more calls."""
    from app.tools import REGISTRY

    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder._client.get_messages = AsyncMock(
        return_value=[make_message(i) for i in range(30, 0, -1)]
    )
    holder.cache.put("@tinychan", make_entity(1, username="tinychan"))

    params = REGISTRY["get_messages"].params_model.model_validate(
        {"chat": "@tinychan", "limit": 200}
    )
    result = await REGISTRY["get_messages"].handler(holder, params)

    assert len(result) == 30
    assert holder._client.get_messages.await_count == 1


async def test_get_messages_consumes_one_bucket_slot_per_page(holder, make_entity, make_message) -> None:
    from app.tools import REGISTRY

    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    page_1 = [make_message(i) for i in range(1000, 900, -1)]
    page_2 = [make_message(i) for i in range(900, 850, -1)]
    holder._client.get_messages = AsyncMock(side_effect=[page_1, page_2])
    holder.cache.put("@chan", make_entity(1, username="chan"))

    used_before = holder.throttle.snapshot()["buckets"]["read"]["used"]

    params = REGISTRY["get_messages"].params_model.model_validate(
        {"chat": "@chan", "limit": 150}
    )
    await REGISTRY["get_messages"].handler(holder, params)

    used_after = holder.throttle.snapshot()["buckets"]["read"]["used"]
    assert used_after - used_before == 2, (
        f"two server calls should charge two slots; delta={used_after - used_before}"
    )


# ---------- read-only -------------------------------------------------------


async def test_read_only_blocks_send_with_permission_error(cfg, monkeypatch, make_entity) -> None:
    monkeypatch.setenv("TELETHON_READ_ONLY", "true")
    from app.client import TelethonHolder
    from app.config import Config
    from app.tools import REGISTRY

    holder = TelethonHolder(Config.from_env())
    holder._client = AsyncMock()
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder.cache.put("@x", make_entity(1, username="x"))

    params = REGISTRY["send_message"].params_model.model_validate(
        {"chat": "@x", "text": "hi"}
    )
    with pytest.raises(PermissionError):
        await REGISTRY["send_message"].handler(holder, params)
    # Telegram must not have been touched.
    holder._client.send_message.assert_not_awaited()


async def test_read_only_does_not_block_reads(holder, make_entity, monkeypatch) -> None:
    monkeypatch.setenv("TELETHON_READ_ONLY", "true")
    from app.client import TelethonHolder
    from app.config import Config
    from app.tools import REGISTRY

    h = TelethonHolder(Config.from_env())
    h._client = AsyncMock()
    me = make_entity(123, username="me")
    h._client.get_me = AsyncMock(return_value=me)

    params = REGISTRY["get_me"].params_model.model_validate({})
    result = await REGISTRY["get_me"].handler(h, params)
    assert result["id"] == 123


# ---------- dry-run ---------------------------------------------------------


async def test_dry_run_short_circuits_send(holder, make_entity, monkeypatch) -> None:
    monkeypatch.setenv("TELETHON_DRY_RUN", "true")
    from app.client import TelethonHolder
    from app.config import Config
    from app.tools import REGISTRY

    h = TelethonHolder(Config.from_env())
    h._client = AsyncMock()
    h._client.get_entity = AsyncMock(return_value=make_entity(1))
    h.cache.put("@chan", make_entity(1, username="chan"))

    params = REGISTRY["send_message"].params_model.model_validate(
        {"chat": "@chan", "text": "would not actually send"}
    )
    result = await REGISTRY["send_message"].handler(h, params)
    assert result["dry_run"] is True
    assert result["would_send"]["text"] == "would not actually send"
    h._client.send_message.assert_not_awaited()


# ---------- bulk_resolve ----------------------------------------------------


async def test_bulk_resolve_isolates_per_handle_errors(holder, make_entity) -> None:
    from app.tools import REGISTRY

    # cache 2 hits, force a real lookup that raises for the 3rd
    holder.cache.put("@a", make_entity(1, username="a"))
    holder.cache.put("@b", make_entity(2, username="b"))

    async def get_entity_side_effect(arg):
        if isinstance(arg, str) and "bad" in arg:
            raise ValueError("nope")
        if isinstance(arg, int):  # cached resolve via id
            return make_entity(arg, username=str(arg))
        return make_entity(99, username=arg)

    holder._client.get_entity = AsyncMock(side_effect=get_entity_side_effect)

    params = REGISTRY["bulk_resolve"].params_model.model_validate(
        {"chats": ["@a", "@b", "@bad_one"]}
    )
    result = await REGISTRY["bulk_resolve"].handler(holder, params)
    assert result["count"] == 2
    assert result["failed"] == 1
    assert result["errors"][0]["chat"] == "@bad_one"


# ---------- throttle_status / account_health -------------------------------


async def test_throttle_status_returns_live_snapshot(holder) -> None:
    from app.tools import REGISTRY
    params = REGISTRY["throttle_status"].params_model.model_validate({})
    result = await REGISTRY["throttle_status"].handler(holder, params)
    assert "throttle" in result
    assert "cache" in result
    assert "read_only" in result
    assert result["throttle"]["enabled"] is True


async def test_account_health_risk_tiers(holder) -> None:
    from app.tools import REGISTRY
    params = REGISTRY["account_health"].params_model.model_validate({})

    # baseline = ok
    assert (await REGISTRY["account_health"].handler(holder, params))["risk"] == "ok"

    # one flood → 2x → warning
    holder.throttle.notify_flood(30, "read")
    assert (await REGISTRY["account_health"].handler(holder, params))["risk"] == "warning"

    # three more → 16x → high
    for _ in range(3):
        holder.throttle.notify_flood(30, "read")
    assert (await REGISTRY["account_health"].handler(holder, params))["risk"] == "high"


# ---------- get_dialogs charges extra bucket slots --------------------------


async def test_get_dialogs_records_extra_pages(holder, make_entity) -> None:
    """When iter_dialogs internally returns >100 dialogs, charge extras."""
    from app.tools import REGISTRY

    # Telethon may internally page even if user asked for less than 100, but
    # we account based on what we actually got back. Simulate 200 returned.
    dialogs = []
    for i in range(200):
        d = MagicMock()
        d.entity = make_entity(i)
        d.unread_count = 0
        d.pinned = False
        d.message = None
        d.name = f"dialog{i}"
        dialogs.append(d)
    holder._client.get_dialogs = AsyncMock(return_value=dialogs)

    used_before = holder.throttle.snapshot()["buckets"]["read"]["used"]

    params = REGISTRY["get_dialogs"].params_model.model_validate({"limit": 200})
    out = await REGISTRY["get_dialogs"].handler(holder, params)
    assert len(out) == 200

    used_after = holder.throttle.snapshot()["buckets"]["read"]["used"]
    # 1 for the initial acquire() + 1 record_extra for the 2nd page server hit
    assert used_after - used_before == 2, (
        f"expected 2 read slots (1 acquire + 1 extra), delta={used_after - used_before}"
    )


# ---------- adaptive backoff fires on FloodWaitError -----------------------


async def test_flood_wait_error_notifies_throttler(holder, make_entity) -> None:
    """A FloodWaitError raised from inside guard() must update the multiplier."""
    from telethon.errors import FloodWaitError
    from app.tools import REGISTRY

    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    holder.cache.put("@chan", make_entity(1, username="chan"))

    # send_message will hit the AsyncMock with a side effect that raises
    holder._client.send_message = AsyncMock(
        side_effect=FloodWaitError(request=None, capture=20)
    )

    params = REGISTRY["send_message"].params_model.model_validate(
        {"chat": "@chan", "text": "spam"}
    )
    with pytest.raises(FloodWaitError):
        await REGISTRY["send_message"].handler(holder, params)

    # Multiplier should have bumped to 2x.
    snap = holder.throttle.snapshot()
    assert snap["multiplier"] == 2.0
    assert snap["flood_events_1h"] == 1
