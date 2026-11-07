---
title: "Configuration"
description: "Every telethon-plus environment variable with its default: Telegram credentials, listen address, auth key, proxy, throttling, cache, updates and safety switches."
nav_order: 3
---

# Configuration

Everything is an environment variable, read once at startup. Change one and restart the container. A value that cannot be parsed, such as a word where a number belongs, stops the container with `config error: ...` and exit code 2; [Troubleshooting](troubleshooting.md#the-container-exits-with-config-error) lists the messages. [`.env.example`](https://github.com/psyb0t/docker-telethon-plus/blob/main/.env.example) has the full list with comments.

Booleans take `1`, `true`, `yes`, `on` or `0`, `false`, `no`, `off`, in any case. Numbers must be plain numbers without a unit. Bucket limits and `TELETHON_UPDATES_BUFFER_SIZE` must be at least 1, timeouts above 0, and intervals, jitter, the cache TTL and the flood sleep threshold at least 0.

## Telegram and server

| Variable | Default | Meaning |
| --- | --- | --- |
| `TELETHON_API_ID` | required | App ID from <https://my.telegram.org/apps>. |
| `TELETHON_API_HASH` | required | App hash from the same page. |
| `TELETHON_SESSION` | required for the server | Session string from the [login](getting-started.md#2-log-in-once). Full access to the account. |
| `TELETHON_HTTP_LISTEN_ADDRESS` | `0.0.0.0:8080` | `host:port` the server binds. An empty host means all interfaces. The image healthcheck follows the port set here. |
| `TELETHON_AUTH_KEY` | empty | When set, every route except `/healthz` and `/metrics` requires `Authorization: Bearer <key>`, and `/ws/updates` requires `?token=<key>`. Empty means no auth at all. See [Security](security.md). |
| `TELETHON_REQUEST_TIMEOUT` | `60` | Seconds Telethon waits for one Telegram request. |
| `TELETHON_FLOOD_SLEEP_THRESHOLD` | `60` | Telethon sleeps through a `FLOOD_WAIT` up to this many seconds and retries; a longer one fails the call with a [429](http-api.md#errors). A very high value (such as `86400`) never fails, but then one long `FLOOD_WAIT` blocks the whole client for that long. |
| `TELETHON_PROXY` | empty | Connect to Telegram through a proxy: `socks5://`, `socks4://` or `http://`, written `scheme://[user:password@]host:port`. Percent-encode special characters in the user and password. The proxy resolves Telegram's hostnames. An invalid value stops startup. MTProto proxies are not supported. |
| `TELETHON_DEVICE_MODEL` | `docker-telethon-plus` | Device name Telegram shows in the account's active sessions. |
| `TELETHON_SYSTEM_VERSION` | `1.0` | OS version shown there. |
| `TELETHON_APP_VERSION` | `1.0` | App version shown there. |
| `TELETHON_DOWNLOAD_DIR` | `/tmp/telethon-plus` | Where `send_file` stages a download before sending it. Each upload gets a private temp file there, deleted after the send whether it worked or not. |
| `TELETHON_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`, in any case; anything else stops startup. Telethon's own logger never goes below `WARNING`. Access logs are on at `INFO` and below. |
| `TELETHON_LOG_JSON` | `false` | Log one JSON object per line instead of plain text. |

## Safety switches

| Variable | Default | Meaning |
| --- | --- | --- |
| `TELETHON_READ_ONLY` | `false` | Refuse every tool that changes something with `403`. Reads, status and metrics still work. |
| `TELETHON_DRY_RUN` | `false` | Validate writes and answer `{"dry_run": true, "would_...": {...}}` without calling Telegram. |

[Security](security.md#safety-switches) says when to use each.

## Throttling and cache

The defaults are meant to keep an account under Telegram's limits without thinking about it. [Throttling and cache](throttling.md) explains how the layers stack and what each one prevents.

| Variable | Default | Meaning |
| --- | --- | --- |
| `TELETHON_THROTTLE_ENABLED` | `true` | Master switch for every limit below. |
| `TELETHON_THROTTLE_GLOBAL_INTERVAL_MS` | `50` | Minimum gap between any two requests. |
| `TELETHON_THROTTLE_JITTER_MS` | `200` | Random extra delay up to this long, so traffic does not tick like a metronome. |
| `TELETHON_THROTTLE_PER_CHAT_INTERVAL_MS` | `1100` | Minimum gap between two sends to the same chat (Telegram allows about one per second). |
| `TELETHON_THROTTLE_PER_CHAT_READ_INTERVAL_MS` | `250` | Minimum gap between two reads from the same chat. |
| `TELETHON_THROTTLE_ADAPTIVE` | `true` | Double every wait for an hour after each `FLOOD_WAIT`. |
| `TELETHON_BUCKET_RESOLVE_PER_MIN` | `5` | Username resolutions per minute, the call that gets accounts banned for a day. |
| `TELETHON_BUCKET_GET_FULL_PER_MIN` | `10` | Full channel and user lookups, and member lists, per minute. |
| `TELETHON_BUCKET_JOIN_PER_HOUR` | `5` | Channel and group joins per hour. |
| `TELETHON_BUCKET_CREATE_PER_HOUR` | `5` | Channel and group creations per hour. |
| `TELETHON_BUCKET_SEND_PER_MIN` | `20` | Sends per minute across all chats. |
| `TELETHON_BUCKET_READ_PER_MIN` | `600` | Read calls per minute, counted per Telegram request: reading 300 messages costs 3. |
| `TELETHON_CACHE_ENABLED` | `true` | Keep resolved chats and users on disk. |
| `TELETHON_CACHE_PATH` | `/cache/entities.json` | Cache file. Mount `/cache` as a volume to keep it across container rebuilds. |
| `TELETHON_CACHE_TTL_SECONDS` | `604800` | How long an entry stays valid: 7 days. `0` keeps entries forever. |

## Updates and observability

| Variable | Default | Meaning |
| --- | --- | --- |
| `TELETHON_UPDATES_ENABLED` | `true` | Listen for incoming events and serve `/ws/updates`. See [Updates and webhooks](updates.md). |
| `TELETHON_UPDATES_BUFFER_SIZE` | `256` | Events queued per websocket client, and for the webhook. A client that falls this far behind is disconnected. |
| `TELETHON_POST_TO_URL` | empty | POST every incoming event to this URL as JSON. Every message the account receives goes there. |
| `TELETHON_POST_TO_TIMEOUT` | `10` | Seconds per webhook POST. |
| `TELETHON_METRICS_ENABLED` | `true` | Serve Prometheus metrics at `/metrics`. See [Observability](observability.md). |

## Login only

| Variable | Default | Meaning |
| --- | --- | --- |
| `TELETHON_SESSION_OUTPUT_FILE` | empty | When set, `login` also writes the session string to this file. `make login` uses it to fill `.env`. |

`login` needs `TELETHON_API_ID` and `TELETHON_API_HASH`, and uses `TELETHON_PROXY` and the device settings above. A `TELETHON_SESSION` already set is reused instead of starting over.
