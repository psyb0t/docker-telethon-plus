# docker-telethon-plus

[![Docker Hub](https://img.shields.io/docker/pulls/psyb0t/telethon-plus?style=flat-square)](https://hub.docker.com/r/psyb0t/telethon-plus)
[![License: WTFPL](https://img.shields.io/badge/License-WTFPL-brightgreen.svg?style=flat-square)](http://www.wtfpl.net/)

Your Telegram account, but it takes HTTP requests. Wraps [Telethon](https://codeberg.org/Lonami/Telethon) — the real MTProto userbot client, not that neutered Bot API garbage — behind a JSON HTTP API and a Model Context Protocol endpoint.

Same tools, two doors. POST some JSON, or point your AI agent at `/mcp` and let it go nuts. Either way it's talking to Telegram as *you*, with full account access.

One login. One session string. Never type a code again.

## Table of Contents

- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [First-time login](#first-time-login)
- [Configuration](#configuration)
- [Tools](#tools)
- [HTTP API](#http-api)
- [MCP](#mcp)
- [Development](#development)
- [Tests](#tests)
- [License](#license)

## How it works

```
+-------------------+        +-----------------------+
|  Any HTTP client  | -----> |  REST  /api/...       | --+
+-------------------+        +-----------------------+   |
                                                         |   +-----------+        Telegram
+-------------------+        +-----------------------+   +-> |  Telethon | <----> Servers
|  MCP-aware agent  | -----> |  /mcp  (Streamable    | --+   +-----------+       (MTProto)
|  (Claude, etc.)   |        |   HTTP transport)     |
+-------------------+        +-----------------------+
```

One Telethon client. One async lock. Both surfaces share the same tool registry — no duplication, no weird state, no bullshit.

## Quick start

```yaml
services:
  telethon-plus:
    image: psyb0t/telethon-plus
    ports:
      - "8080:8080"
    environment:
      TELETHON_API_ID: "123456"
      TELETHON_API_HASH: "your-api-hash"
      TELETHON_SESSION: "1Aa...long-string-from-login-helper..."
    restart: unless-stopped
```

Get `API_ID` / `API_HASH` from <https://my.telegram.org/apps>. Get the session string from the [login helper](#first-time-login) below.

## First-time login

Telegram makes you prove you're a human once — phone number, SMS code, optionally 2FA. Do it once, never again.

```bash
cp .env.example .env
$EDITOR .env  # put in TELETHON_API_ID and TELETHON_API_HASH

make login
```

`make login` builds the image, runs the interactive flow, and shoves `TELETHON_SESSION` straight into your `.env`. That's it. Run `make run` and you're live.

No repo? No problem:

```bash
docker run --rm -it \
  -e TELETHON_API_ID=123456 \
  -e TELETHON_API_HASH=your-api-hash \
  psyb0t/telethon-plus login
```

Copy the session string it spits out, set it as `TELETHON_SESSION`, done.

> **The session string is full account access.** Whoever has it is you. Don't commit it, don't paste it in Slack, don't tattoo it anywhere.

## Configuration

All config via environment variables. Copy `.env.example` to get the full list with comments.

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELETHON_API_ID` | yes | — | API ID from my.telegram.org |
| `TELETHON_API_HASH` | yes | — | API hash from my.telegram.org |
| `TELETHON_SESSION` | yes | — | StringSession from the login helper |
| `TELETHON_HTTP_LISTEN_ADDRESS` | no | `0.0.0.0:8080` | `host:port` to bind |
| `TELETHON_LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `TELETHON_REQUEST_TIMEOUT` | no | `60` | Per-request timeout in seconds |
| `TELETHON_FLOOD_SLEEP_THRESHOLD` | no | `60` | Auto-sleep through `FLOOD_WAIT` errors below this many seconds. Telegram will rate-limit you — this is the safety valve. |
| `TELETHON_DEVICE_MODEL` | no | `docker-telethon-plus` | What Telegram thinks your device is |
| `TELETHON_SYSTEM_VERSION` | no | `1.0` | Ditto for OS |
| `TELETHON_APP_VERSION` | no | `1.0` | Ditto for app |
| `TELETHON_DOWNLOAD_DIR` | no | `/tmp/telethon-plus` | Scratch space for `send_file` uploads |
| `TELETHON_AUTH_KEY` | no | `""` | When set, all endpoints require `Authorization: Bearer <key>`. `/healthz` stays public. Empty = no auth. |

### Throttling & cache (anti-flood)

Telegram bans accounts that hammer it. Defaults here are conservative — meant to keep you under the server-side limits without you having to think about it. Tune only if you know what you're doing.

| Variable | Default | What it does |
|---|---|---|
| `TELETHON_THROTTLE_ENABLED` | `true` | Master switch for all rate-limiting below |
| `TELETHON_THROTTLE_GLOBAL_INTERVAL_MS` | `50` | Min gap between any two outgoing requests |
| `TELETHON_THROTTLE_JITTER_MS` | `200` | Random ±jitter added on top (kills metronome traffic patterns) |
| `TELETHON_THROTTLE_PER_CHAT_INTERVAL_MS` | `1100` | Min gap between sends to the same chat (Telegram's "1/sec/chat" ceiling, with margin) |
| `TELETHON_THROTTLE_PER_CHAT_READ_INTERVAL_MS` | `250` | Min gap between reads from the same chat. Stops single-channel scraping from monopolizing the read bucket. |
| `TELETHON_THROTTLE_ADAPTIVE` | `true` | On each `FLOOD_WAIT`, multiply all waits ×2 for an hour. Resets after a quiet hour. |
| `TELETHON_BUCKET_RESOLVE_PER_MIN` | `5` | Cap on `resolveUsername` — **the main thing that gets accounts 22-hour-banned** |
| `TELETHON_BUCKET_GET_FULL_PER_MIN` | `10` | Cap on `getFullChannel` / `getFullUser` / `get_participants` |
| `TELETHON_BUCKET_JOIN_PER_HOUR` | `5` | Cap on channel/group joins |
| `TELETHON_BUCKET_CREATE_PER_HOUR` | `5` | Cap on channel/group creation |
| `TELETHON_BUCKET_SEND_PER_MIN` | `20` | Cap on sends across all chats |
| `TELETHON_BUCKET_READ_PER_MIN` | `600` | Cap on read ops. **Counted per server-side API call**, not per tool call: `get_messages(limit=300)` charges 3 slots (Telegram caps GetHistory at 100/page); `get_dialogs(limit=500)` similarly charges 5. |
| `TELETHON_CACHE_ENABLED` | `true` | Persist resolved entities to disk |
| `TELETHON_CACHE_PATH` | `/cache/entities.json` | Mount `/cache` as a host volume to keep this across rebuilds |
| `TELETHON_CACHE_TTL_SECONDS` | `604800` | 7 days. Set `0` for no expiry. |
| `TELETHON_FLOOD_SLEEP_THRESHOLD` | `60` | Telethon's reactive auto-sleep: if a `FLOOD_WAIT` is shorter than this many seconds, sleep through it. Above this, raise. Set very high (e.g. `86400`) to never raise — but then a hostile FLOOD_WAIT will block your process for the full duration. |

**How the layers stack:**

1. Entity cache short-circuits resolveUsername — first lookup of `@somechannel` calls Telegram; every subsequent lookup is free. Survives container restarts via the `/cache` volume.
2. Per-method token buckets cap the dangerous methods. If you try to resolve 6 new usernames in one minute, the 6th sleeps until the oldest expires.
3. Per-chat send interval throttles sends to each chat to ≤1/sec (with margin).
4. Global gap + jitter humanizes the overall traffic shape.
5. Adaptive backoff: a single FLOOD_WAIT halves your effective rate for an hour. Three in a row → ÷8. Auto-recovers after an idle hour.

**For bulk scraping work** (the scenario that caused 22-hour bans before): the cache + `bucket_resolve_per_min=5` combination is what saves you. Resolving 200 new channels takes ~40 minutes instead of getting you banned in 5.

## Tools

JSON in, JSON out. All inputs are pydantic-validated — send garbage, get a `400` back with exactly what's wrong.

Chat references (`chat`, `from_chat`, `to_chat`) accept whatever Telethon accepts:

| Format | Example |
|---|---|
| Username | `@psyb0t` |
| Phone number | `+1234567890` |
| t.me link | `https://t.me/psyb0t` |
| Numeric ID | `123456789` |
| Supergroup/channel ID | `-1001234567890` |
| Your own Saved Messages | `me` |

> **Numeric IDs only resolve for entities Telethon has already seen** — i.e. cached in your session via a prior `@username` / `t.me` lookup, dialog list, or incoming message. MTProto needs an `access_hash`, not just an ID, and bare numbers don't carry one. Especially relevant for bots: pass `@botusername` first (or call `GET /api/dialogs` / `GET /api/entities?chat=@bot` once) before referring to it by numeric ID. If you only have the bot's token and no username, hit Telegram's Bot API `getMe` to fetch the username, then use that.

### Quick reference

| Endpoint | Required params | What it does |
|---|---|---|
| `GET /api/me` | — | Who the fuck am I — returns your account profile. |
| `GET /api/entities` | `chat` | Resolve a username/ID/link to a full profile. |
| `POST /api/entities/bulk` | `chats` | Bulk resolve many handles. Honors the resolve-username bucket. Returns per-handle success/error. |
| `POST /api/messages` | `chat`, `text` | Send a text message. Supports `parse_mode`, `reply_to`, `silent`, `link_preview`, `schedule` (ISO datetime). |
| `GET /api/messages` | `chat` | Read recent messages. Optional: `limit`, `offset_id`, `search`. |
| `GET /api/messages/{id}` | `chat` | Fetch a single message by ID. |
| `GET /api/messages/{id}/media` | `chat` | Download a message's media as base64. Optional: `max_bytes`. |
| `GET /api/dialogs` | — | List your chats, groups, channels. Optional: `limit`, `archived`. |
| `GET /api/dialogs/search` | `query` | Find dialogs by title or @username substring. |
| `POST /api/messages/forward` | `from_chat`, `to_chat`, `message_ids` | Forward messages between chats. |
| `DELETE /api/messages` | `chat`, `message_ids` | Nuke messages by ID. |
| `PATCH /api/messages/{id}` | `chat`, `text` | Edit your message. |
| `POST /api/messages/read` | `chat` | Mark as read. Optional `max_id`. |
| `POST /api/messages/{id}/pin` | `chat` | Pin a message. Optional `silent`, `pm_oneside`. |
| `POST /api/messages/{id}/unpin` | `chat` | Unpin. |
| `POST /api/messages/{id}/reactions` | `chat`, `emoji` | React with an emoji. Optional `big`. |
| `DELETE /api/messages/{id}/reactions` | `chat` | Remove your reaction. |
| `POST /api/files` | `chat`, `file_url` | Fetch from URL and send. |
| `GET /api/participants` | `chat` | List members. |
| `POST /api/chats` | `title` | Create a supergroup or channel. |
| `DELETE /api/chats` | `chat` | Delete a supergroup/channel you own. |
| `POST /api/chats/join` | `chat` | Join a public channel/group. |
| `POST /api/chats/invite` | `invite` | Join via private `t.me/+hash` invite link. |
| `POST /api/chats/leave` | `chat` | Leave. |
| `GET /api/channels/linked` | `chat` | Get a channel's linked discussion group, if any. |
| `POST /api/admin/ban` | `chat`, `user` | Ban a user. Optional `until_seconds`. |
| `POST /api/admin/unban` | `chat`, `user` | Lift a ban. |
| `POST /api/admin/kick` | `chat`, `user` | Kick (ban+immediate unban). |
| `POST /api/admin/promote` | `chat`, `user` | Grant admin rights. Per-permission booleans + optional `title`. |
| `POST /api/admin/demote` | `chat`, `user` | Strip admin rights. |
| `POST /api/polls` | `chat`, `question`, `options` | Create a poll. Optional `quiz`, `correct_option`, `solution`. |
| `POST /api/polls/{id}/vote` | `chat`, `options` | Vote (0-based indices). |
| `GET /api/polls/{id}/results` | `chat` | Current results / vote counts. |
| `GET /api/throttle/status` | — | Live rate-limit + cache state. |
| `GET /api/account/health` | — | Flood-risk tier based on adaptive multiplier. |
| `GET /metrics` | — | Prometheus exposition (no auth). |
| `WS /ws/updates` | `?token=...` | Stream incoming Telegram events as JSON. |

## HTTP API

Standard REST API. JSON in, JSON out. Every response is `{"result": ...}` on success.

If `TELETHON_AUTH_KEY` is set, every request (except `/healthz`) needs:

```http
Authorization: Bearer your-secret-key
```

### GET /api/me

Who am I right now.

```http
GET /api/me
```

```json
{
  "result": {
    "id": 123456789,
    "type": "User",
    "username": "psyb0t",
    "first_name": "Ciprian",
    "phone": "+40..."
  }
}
```

### GET /api/entities

Resolve any chat reference to a full profile.

```http
GET /api/entities?chat=@telegram
```

```json
{
  "result": {
    "id": 1234567,
    "type": "Channel",
    "username": "telegram",
    "title": "Telegram"
  }
}
```

### GET /api/dialogs

```http
GET /api/dialogs?limit=10&archived=false
```

| Param | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 20 | How many dialogs (1–200) |
| `archived` | bool | false | Include archived chats |

```json
{
  "result": [
    {
      "id": 123456789,
      "type": "User",
      "username": "someone",
      "first_name": "Some",
      "last_name": "One",
      "unread_count": 3,
      "pinned": true,
      "last_message": {
        "id": 999,
        "date": "2026-04-29T11:00:00+00:00",
        "chat_id": 123456789,
        "sender_id": 123456789,
        "text": "hey",
        "out": false,
        "reply_to_msg_id": null,
        "media": false,
        "media_type": null
      }
    }
  ]
}
```

### GET /api/messages

```http
GET /api/messages?chat=me&limit=5&search=hello
```

| Param | Type | Default | Description |
|---|---|---|---|
| `chat` | string | required | Chat to read from |
| `limit` | int | 20 | How many messages (1–200) |
| `offset_id` | int | 0 | Start from this message ID (pagination) |
| `search` | string | — | Full-text search filter |

```json
{
  "result": [
    {
      "id": 4242,
      "date": "2026-04-29T12:00:00+00:00",
      "chat_id": 12345,
      "sender_id": 67890,
      "text": "hello",
      "out": false,
      "reply_to_msg_id": null,
      "media": false,
      "media_type": null
    }
  ]
}
```

Newest first. Returns `[]` if nothing matches.

### POST /api/messages

Send a message.

```http
POST /api/messages
Content-Type: application/json

{
  "chat": "@psyb0t",
  "text": "**hello** from a container",
  "parse_mode": "md",
  "silent": true,
  "reply_to": 4241
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `chat` | string | required | Target chat |
| `text` | string | required | Message text (1–4096 chars) |
| `parse_mode` | string | null | `md` / `markdown` / `html` / null |
| `reply_to` | int | null | Message ID to reply to |
| `silent` | bool | false | Send without notification |
| `link_preview` | bool | true | Show link previews |

```json
{
  "result": {
    "id": 4242,
    "date": "2026-04-29T12:00:00+00:00",
    "chat_id": 12345,
    "sender_id": 67890,
    "text": "hello from a container",
    "out": true,
    "reply_to_msg_id": 4241,
    "media": false,
    "media_type": null
  }
}
```

### PATCH /api/messages/{id}

Edit a message. Message ID goes in the URL, everything else in the body.

```http
PATCH /api/messages/4242
Content-Type: application/json

{
  "chat": "me",
  "text": "fixed version",
  "parse_mode": "md"
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `chat` | string | required | Chat containing the message |
| `text` | string | required | New text (1–4096 chars) |
| `parse_mode` | string | null | `md` / `html` / null |
| `link_preview` | bool | true | Show link previews |

```json
{
  "result": {
    "id": 4242,
    "date": "2026-04-29T12:00:00+00:00",
    "chat_id": 99999,
    "sender_id": 123456789,
    "text": "fixed version",
    "out": true,
    "reply_to_msg_id": null,
    "media": false,
    "media_type": null
  }
}
```

### DELETE /api/messages

Delete messages by ID.

```http
DELETE /api/messages
Content-Type: application/json

{
  "chat": "@psyb0t",
  "message_ids": [4242, 4243],
  "revoke": true
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `chat` | string | required | Chat containing the messages |
| `message_ids` | list[int] | required | IDs to delete (max 100) |
| `revoke` | bool | true | Delete for everyone, not just yourself |

```json
{ "result": { "deleted": 2, "requested": 2 } }
```

### POST /api/messages/forward

```http
POST /api/messages/forward
Content-Type: application/json

{
  "from_chat": "@sourcechannel",
  "to_chat": "me",
  "message_ids": [101, 102, 103],
  "silent": true
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `from_chat` | string | required | Source chat |
| `to_chat` | string | required | Destination chat |
| `message_ids` | list[int] | required | IDs to forward (max 100) |
| `silent` | bool | false | Forward without notification |

```json
{
  "result": [
    {
      "id": 5001,
      "date": "2026-04-29T12:01:00+00:00",
      "chat_id": 99999,
      "sender_id": 123456789,
      "text": "forwarded content here",
      "out": true,
      "reply_to_msg_id": null,
      "media": false,
      "media_type": null
    }
  ]
}
```

### POST /api/messages/read

Mark messages as read.

```http
POST /api/messages/read
Content-Type: application/json

{ "chat": "@psyb0t", "max_id": 0 }
```

| Field | Type | Default | Description |
|---|---|---|---|
| `chat` | string | required | Chat to mark as read |
| `max_id` | int | 0 | Mark up to this message ID. `0` = mark all. |

```json
{ "result": { "ok": true } }
```

### POST /api/files

Download a file from an HTTPS URL and send it to a chat. Never touches your disk — goes through the container's scratch dir (`TELETHON_DOWNLOAD_DIR`) and gets cleaned up immediately.

```http
POST /api/files
Content-Type: application/json

{
  "chat": "@psyb0t",
  "file_url": "https://example.com/photo.jpg",
  "caption": "look at this shit",
  "silent": true
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `chat` | string | required | Target chat |
| `file_url` | string | required | HTTPS URL of the file to fetch and send |
| `caption` | string | null | Caption text |
| `parse_mode` | string | null | `md` / `html` / null |
| `silent` | bool | false | Send without notification |
| `force_document` | bool | false | Send as a generic file instead of letting Telegram pick media type |
| `max_bytes` | int | 52428800 | Reject files larger than this (default 50 MB, max 2 GB) |

Telegram auto-detects media type from the file extension and MIME type. A `.jpg` becomes a photo, `.mp4` becomes a video, `.mp3` becomes audio. Use `force_document: true` to override.

```json
{
  "result": {
    "id": 4243,
    "date": "2026-04-29T12:02:00+00:00",
    "chat_id": 12345,
    "sender_id": 67890,
    "text": "look at this shit",
    "out": true,
    "reply_to_msg_id": null,
    "media": true,
    "media_type": "MessageMediaPhoto"
  }
}
```

### GET /api/participants

List members of a group or channel.

```http
GET /api/participants?chat=-1001234567890&limit=50&search=john
```

| Param | Type | Default | Description |
|---|---|---|---|
| `chat` | string | required | Group or channel |
| `limit` | int | 100 | Max members to return (1–1000) |
| `search` | string | — | Filter by name |

```json
{
  "result": [
    { "id": 123456789, "type": "User", "username": "johndoe", "first_name": "John" }
  ]
}
```

Large public channels may return a limited set or require admin rights.

### POST /api/chats

Create a supergroup or broadcast channel.

```http
POST /api/chats
Content-Type: application/json

{ "title": "my-group", "megagroup": true }
```

| Field | Type | Default | Description |
|---|---|---|---|
| `title` | string | required | Group name (1–255 chars) |
| `megagroup` | bool | true | `true` = supergroup, `false` = broadcast channel |

```json
{
  "result": { "id": 1234567890, "type": "Channel", "title": "my-group" }
}
```

### DELETE /api/chats

Delete a supergroup or channel you own. **Irreversible.**

```http
DELETE /api/chats
Content-Type: application/json

{ "chat": "-1001234567890" }
```

```json
{ "result": { "ok": true } }
```

### POST /api/chats/join

Join a public channel or supergroup.

```http
POST /api/chats/join
Content-Type: application/json

{ "chat": "@somegroup" }
```

```json
{ "result": { "ok": true } }
```

### POST /api/chats/leave

Leave a channel or supergroup.

```http
POST /api/chats/leave
Content-Type: application/json

{ "chat": "@somegroup" }
```

```json
{ "result": { "ok": true } }
```

### Errors

| Status | When |
|---|---|
| `400` | Bad JSON, validation failure, or Telethon said the input is nonsense. Body has details. |
| `401` | Missing or wrong Bearer token (only when `TELETHON_AUTH_KEY` is set). |
| `404` | Unknown endpoint. |
| `502` | Telegram threw an RPC error (`FloodWaitError`, `ChatWriteForbiddenError`, etc.). Body has the error class and message. |

### Health

```http
GET /healthz
```

```json
{ "status": "ok", "authorized": true }
```

`authorized: false` means the container started but the session is fucked — bad string, revoked, or Telegram unreachable. Always public, no auth required.

## Observability

### `GET /metrics`

Prometheus exposition. Scrape it. No auth (publicly readable inside your cluster — if you need it locked down, put it behind your usual scrape-network policy).

Series exported:
- `telethon_plus_tool_calls_total{tool=...}`
- `telethon_plus_tool_errors_total{tool=...}`
- `telethon_plus_flood_events_total{bucket=...}`
- `telethon_plus_cache_hits_total` / `_misses_total` / `_entries`
- `telethon_plus_throttle_multiplier`
- `telethon_plus_bucket_used{bucket=...}` / `_bucket_limit{bucket=...}`
- `telethon_plus_uptime_seconds`

### `GET /api/throttle/status`

Live state of every bucket, multiplier, recent flood events, cache stats:

```json
{
  "result": {
    "throttle": {
      "enabled": true,
      "adaptive": true,
      "multiplier": 1.0,
      "flood_events_1h": 0,
      "buckets": {
        "resolve_username": {"used": 0, "limit": 5, "window_seconds": 60},
        "send": {"used": 0, "limit": 20, "window_seconds": 60}
      },
      "tracked_chats": 12,
      "global_interval_ms": 50,
      "per_chat_interval_ms": 1100,
      "jitter_ms": 200
    },
    "cache": {"entries": 247},
    "read_only": false,
    "dry_run": false
  }
}
```

### `GET /api/account/health`

```json
{
  "result": {
    "authorized": true,
    "risk": "ok",
    "multiplier": 1.0,
    "flood_events_1h": 0,
    "read_only": false,
    "dry_run": false
  }
}
```

`risk` is `ok` (multiplier 1×), `warning` (≥2×), or `high` (≥8×).

### Throttle response headers

Every `/api/...` response carries:
- `X-Throttle-Multiplier`
- `X-Throttle-Flood-Events-1h`
- `X-RateLimit-Remaining-<bucket>` for each bucket

Lets clients self-throttle without polling `/api/throttle/status`.

## Updates / webhooks

Two ways to receive incoming Telegram events (new messages, edits, deletes, chat actions):

### WebSocket — `/ws/updates`

```javascript
const ws = new WebSocket('ws://your-host:8080/ws/updates?token=YOUR_AUTH_KEY');
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

If `TELETHON_AUTH_KEY` is set, pass it as `?token=`. Browser WS clients can't set Authorization headers, hence query-string.

Multiple subscribers are supported — each gets its own queue. Slow consumers drop events at `TELETHON_UPDATES_BUFFER_SIZE`.

### Outbound webhook — `TELETHON_POST_TO_URL`

Set the env var to your endpoint and every event gets `POST`ed there as JSON. Fire-and-forget — a slow or failing webhook never blocks Telethon's loop. Use for distributed workers, separate processes, anything that can't hold a WS open.

### Event payload shape

```json
{
  "type": "NewMessage",
  "message": {
    "id": 4242,
    "date": "2026-04-29T12:00:00+00:00",
    "text": "hi",
    "out": false,
    "sender_id": 12345,
    "chat_id": -1001234567890,
    "reply_to_msg_id": null,
    "media": false,
    "media_type": null
  },
  "chat_id": -1001234567890
}
```

For `MessageEdited`, `MessageDeleted`, `ChatAction` — same shape, fewer fields.

## Safety switches

### `TELETHON_READ_ONLY=true`

Every write endpoint returns `403`. Reads, status, metrics still work. Killswitch for panic mode or for keeping a test deployment harmless.

### `TELETHON_DRY_RUN=true`

Write endpoints accept the request, validate it, **don't** call Telegram, return `{"dry_run": true, "would_...": {...}}`. Useful for verifying scripts before pointing them at production.

## MCP

Mounted at `/mcp/` using the [streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http). Every tool from the table above shows up automatically as an MCP tool with the same name and schema.

Point your agent at:

```
http://your-host:8080/mcp/
```

Stateless — every request is independent, no session juggling. Drop it into Claude Desktop, a custom agent, anything that speaks MCP over HTTP. Works out of the box.

## Development

```bash
make build        # build psyb0t/telethon-plus:latest
make build-test   # build psyb0t/telethon-plus:latest-test
make run          # run locally on :8080 (reads .env)
make login        # interactive login — writes TELETHON_SESSION to .env automatically
make lint         # flake8 + pyright
make format       # isort + black
make test         # run integration tests in Docker
make clean        # remove built images
```

## Tests

Real tests. Real Telegram. No mocking bullshit.

`tests/` spins up the container and hammers both REST and MCP with your actual account. Messages get sent and deleted. If anything breaks, you'll know.

Setup:

```bash
cp .env.example .env
$EDITOR .env  # needs TELETHON_API_ID, TELETHON_API_HASH, TELETHON_SESSION, TEST_CHAT

make test
```

`TEST_CHAT` is where test messages land. Use `me` for Saved Messages — private, yours, no one else sees it. All chat reference formats from the [Tools](#tools) section work here.

`make test` builds both images and runs pytest inside Docker with the socket mounted. No setup beyond `.env`. If credentials are missing, the suite skips cleanly.

| Test file | What it beats on |
|---|---|
| `test_health.py` | Container boots, auth succeeds, OpenAPI spec has all the routes. |
| `test_rest.py` | Validation errors, extra fields rejected, send → edit → fetch → delete roundtrip, dialogs, entity resolution, public channel read, group create/delete, participants. |
| `test_mcp.py` | MCP streamable HTTP: tool discovery, `get_me`, send + delete roundtrip, validation errors come back as `isError`. |
| `test_auth.py` | Auth middleware: 401 on missing/wrong token, 200 on correct token, `/healthz` always public, MCP endpoint protected too. |

## License

[WTFPL](LICENSE) — do whatever the fuck you want.
