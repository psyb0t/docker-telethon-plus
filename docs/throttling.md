---
title: "Throttling and cache"
description: "How telethon-plus keeps a Telegram account under the flood limits: per-method buckets, per-chat gaps, jitter, adaptive backoff after FLOOD_WAIT, and the entity cache."
nav_order: 8
---

# Throttling and cache

Telegram limits accounts that hammer it. Past a soft limit a call fails with `FLOOD_WAIT_<seconds>`, and an account that keeps resolving new usernames can be locked out of that call for about a day. telethon-plus spaces every request out before Telegram has to, with defaults meant to stay under the limits without tuning. The settings are on the [Configuration](configuration.md#throttling-and-cache) page; the live state is at [`/api/throttle/status`](observability.md#get-apithrottlestatus).

## How the layers stack

Before each Telegram request, the throttle works out how long every layer below wants it to wait, sleeps for the longest of them plus a random jitter, then sends.

1. **Entity cache.** A chat or user resolved once by username is kept on disk, so the next lookup of the same `@name` skips Telegram's `resolveUsername` entirely. See [Entity cache](#entity-cache).
2. **Per-method buckets.** Each kind of call has its own budget per window, such as 5 username resolutions a minute. The sixth in a minute waits until the oldest falls out of the window.
3. **Per-chat gaps.** Two sends to the same chat are at least 1100 ms apart, under Telegram's roughly one message a second per chat. Two reads from the same chat are at least 250 ms apart, so scraping one channel cannot take the whole read budget.
4. **Global gap and jitter.** Any two requests are at least 50 ms apart, plus up to 200 ms of random delay so the traffic does not tick like a clock.
5. **Adaptive backoff.** Every `FLOOD_WAIT` Telegram sends doubles the global and per-chat gaps: one in the last hour means 2x, two mean 4x, three mean 8x, up to 64x. Each one stops counting an hour after it happened.

`TELETHON_THROTTLE_ENABLED=false` turns all of it off.

## Buckets

| Bucket | Default | Counts |
| --- | --- | --- |
| `resolve_username` | 5 a minute | Looking up a username that is not in the cache. This is the call that gets accounts locked out for a day. |
| `get_full` | 10 a minute | Full channel and user info, and member lists. |
| `join` | 5 an hour | Joining channels and groups, including by invite link. |
| `create` | 5 an hour | Creating channels and supergroups. |
| `send` | 20 a minute | Sends across all chats. |
| `read` | 600 a minute | Reads, counted per Telegram request: Telegram returns at most 100 messages per history request, so reading 300 messages costs 3, and listing 500 dialogs costs 5. |
| `other` | 60 a minute | Everything else, such as fetching a cached entity by ID. Not configurable. |

The configurable ones are `TELETHON_BUCKET_<NAME>_PER_MIN` or `_PER_HOUR`; see [Configuration](configuration.md#throttling-and-cache).

## Bulk work

The cache plus the 5-a-minute resolve bucket is what keeps bulk scraping safe. Resolving 200 new channels takes about 40 minutes instead of risking the lockout, and every later run over the same channels is cache hits. Use [`bulk_resolve`](tools.md#bulk_resolve) to resolve a list once up front: it honours the bucket and reports each failure separately.

## Entity cache

Resolved chats and users are written to `TELETHON_CACHE_PATH` (`/cache/entities.json`), keyed by the username or link you passed and by their ID, with the access hash Telegram needs to address them. A cache hit goes straight to Telegram with that hash, with no username lookup, and keeps working after a restart. An entry is valid for `TELETHON_CACHE_TTL_SECONDS` (7 days) and every hit pushes that forward. Mount `/cache` as a volume, or the cache starts empty on every new container.

Access hashes belong to one account, so the file records which account wrote it. When the session belongs to another account, the cache is cleared at startup.

A numeric ID only resolves for a chat Telethon has already seen in this session: from a username lookup, the dialog list, or an incoming message. Telegram needs an access hash with the ID, and a bare number does not carry one. Resolve a bot or user by `@username` once before using its ID.

## Telethon's own flood sleep

Telethon sleeps through a `FLOOD_WAIT` of up to `TELETHON_FLOOD_SLEEP_THRESHOLD` (60 seconds) and retries the call, holding the client while it waits, because Telegram wants the account to pause. A longer one fails the call with a [429](http-api.md#errors) (`flood_wait` over MCP). Both kinds feed the adaptive backoff above and the flood counter in [`/metrics`](observability.md#get-metrics): a failed call under its bucket's name, a wait Telethon slept through under `telethon_sleep`.
