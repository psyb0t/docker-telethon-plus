---
title: "Observability"
description: "telethon-plus monitoring: Prometheus metrics, live throttle and flood state, account flood-risk health, the healthcheck, throttle headers and logs."
nav_order: 9
---

# Observability

Four places to look: `/metrics` for Prometheus, `/api/throttle/status` and `/api/account/health` for the live flood state, the headers on every API answer, and the container log.


## `GET /metrics`

Prometheus text format, on while `TELETHON_METRICS_ENABLED` is true (the default). It needs no auth even when `TELETHON_AUTH_KEY` is set, so keep the port off networks you do not trust, or block `/metrics` at your proxy. The counters are per process and start at zero on every restart.

Series exported:
- `telethon_plus_tool_calls_total{tool=...}`
- `telethon_plus_tool_errors_total{tool=...}`
- `telethon_plus_flood_events_total{bucket=...}` (a `FLOOD_WAIT` that failed a call counts under that call's bucket; one Telethon slept through counts under `telethon_sleep`)
- `telethon_plus_cache_hits_total` / `_misses_total` / `_entries`
- `telethon_plus_throttle_multiplier`
- `telethon_plus_bucket_used{bucket=...}` / `_bucket_limit{bucket=...}`
- `telethon_plus_uptime_seconds`

## `GET /api/throttle/status`

Live state of every bucket, the [adaptive multiplier](throttling.md#how-the-layers-stack), recent flood events and cache stats. Needs the auth key when one is set.

```json
{
  "throttle": {
    "enabled": true,
    "adaptive": true,
    "multiplier": 1.0,
    "flood_events_1h": 0,
    "buckets": {
      "resolve_username": {"used": 0, "limit": 5, "window_seconds": 60},
      "send": {"used": 0, "limit": 20, "window_seconds": 60}
    },
    "tracked_chats_send": 12,
    "tracked_chats_read": 30,
    "global_interval_ms": 50,
    "per_chat_interval_ms": 1100,
    "per_chat_read_interval_ms": 250,
    "jitter_ms": 200
  },
  "cache": {"entries": 247},
  "read_only": false,
  "dry_run": false
}
```

## `GET /api/account/health`

```json
{
  "authorized": true,
  "risk": "ok",
  "multiplier": 1.0,
  "flood_events_1h": 0,
  "read_only": false,
  "dry_run": false
}
```

`risk` is `ok` while the multiplier is 1, `warning` from 2 (one `FLOOD_WAIT` in the last hour) and `high` from 8 (three or more). When it says `high`, stop bulk work for an hour.

## Throttle response headers

Every `/api/...` response carries:
- `X-Throttle-Multiplier`
- `X-Throttle-Flood-Events-1h`
- `X-RateLimit-Remaining-<bucket>` for each bucket

Lets clients self-throttle without polling `/api/throttle/status`.

## Health and logs

`GET /healthz` answers `{"status": "ok", "authorized": true}` without auth once the server is up. The image's Docker healthcheck calls it on the port set in `TELETHON_HTTP_LISTEN_ADDRESS`.

The server logs to stdout at `TELETHON_LOG_LEVEL` (default `INFO`), one JSON object per line with `TELETHON_LOG_JSON=true`. Throttle sleeps are logged at `DEBUG` with the reason for each wait, and every `FLOOD_WAIT` that fails a call is logged at `WARNING` with its length and bucket. Warnings about a sent file's format, from the `hachoir` parser Telethon reads media metadata with, go to the `hachoir` logger at `DEBUG`.
