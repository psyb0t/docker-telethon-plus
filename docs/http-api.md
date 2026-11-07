---
title: "HTTP API"
description: "telethon-plus REST API: bearer auth, JSON response and error shapes, status codes, and request and response examples for every message and chat route."
nav_order: 5
---

# HTTP API

A plain REST API. JSON in, JSON out. Every route runs one of the [tools](tools.md), so the MCP endpoint and REST do the same thing with the same parameters. The [Tools](tools.md) page has each tool's full parameter list; this page covers how to call the REST side, with examples.

## Authentication

When `TELETHON_AUTH_KEY` is set, every route except `/healthz` and `/metrics` needs the key:

```http
Authorization: Bearer your-secret-key
```

A missing or wrong key answers `401` with `{"detail":"unauthorized"}`. `/ws/updates` takes the key as `?token=` instead, because browsers cannot set headers on a websocket. Without `TELETHON_AUTH_KEY` there is no auth at all; see [Security](security.md).

## Responses

A 2xx answer is the resource itself: an object for one thing, an array for a list. There is no `{"result": ...}` wrapper. Errors are `{"detail": ...}`, FastAPI's shape; the status codes are listed under [Errors](#errors).

Every `/api/...` response also carries throttle headers (`X-Throttle-Multiplier`, `X-Throttle-Flood-Events-1h`, `X-RateLimit-Remaining-<bucket>`), described in [Observability](observability.md#throttle-response-headers).

## Quick reference

| Endpoint | Tool | Required | What it does |
| --- | --- | --- | --- |
| `GET /api/me` | [`get_me`](tools.md#get_me) | none | Account profile. |
| `GET /api/entities` | [`get_entity`](tools.md#get_entity) | `chat` | Resolve a username/ID/link to a profile. |
| `POST /api/entities/bulk` | [`bulk_resolve`](tools.md#bulk_resolve) | `chats` | Bulk resolve, honoring the resolve-username bucket. |
| `GET /api/dialogs` | [`get_dialogs`](tools.md#get_dialogs) | none | List dialogs. Optional: `limit`, `archived`, `search` (substring on title/@). |
| `GET /api/messages` | [`get_messages`](tools.md#get_messages) | `chat` | Read recent messages. Optional: `limit`, `offset_id`, `search`. |
| `GET /api/messages/{id}` | [`get_message`](tools.md#get_message) | `chat` | Fetch a single message. |
| `GET /api/messages/{id}/media` | [`download_media`](tools.md#download_media) | `chat` | Download the attachment as raw bytes, with its type in `Content-Type` (or `application/octet-stream` when the sender's type is not a plain `type/subtype`) and its name in `Content-Disposition`. Optional: `max_bytes`. Over MCP, `download_media` returns base64 instead. |
| `POST /api/messages` | [`send_message`](tools.md#send_message), [`send_file`](tools.md#send_file) | `chat`, **`text` or `file_url`** | Send a message. With `file_url`, the server downloads it and sends it as media with `text` as the caption (`send_file`); otherwise it sends `text` (`send_message`). Optional with text: `parse_mode`, `reply_to`, `silent`, `link_preview`, `schedule`. Optional with a file: `parse_mode`, `silent`, `force_document`, `max_bytes`. |
| `POST /api/messages/forward` | [`forward_messages`](tools.md#forward_messages) | `from_chat`, `to_chat`, `message_ids` | Forward messages. |
| `POST /api/messages/read` | [`mark_read`](tools.md#mark_read) | `chat` | Mark as read. Optional `max_id`. |
| `PATCH /api/messages/{id}` | [`edit_message`](tools.md#edit_message) | `chat`, `text` | Edit. |
| `DELETE /api/messages` | [`delete_messages`](tools.md#delete_messages) | `chat`, `message_ids` | Bulk delete (body has the list). |
| `POST /api/messages/{id}/pin` | [`pin_message`](tools.md#pin_message) | `chat` | Pin. Optional `silent`, `pm_oneside`. |
| `POST /api/messages/{id}/unpin` | [`unpin_message`](tools.md#unpin_message) | `chat` | Unpin. |
| `POST /api/messages/{id}/reactions` | [`set_reaction`](tools.md#set_reaction) | `chat`, `emoji` | React. Optional `big`. |
| `DELETE /api/messages/{id}/reactions` | [`remove_reaction`](tools.md#remove_reaction) | `chat` | Remove your reaction. |
| `GET /api/participants` | [`get_participants`](tools.md#get_participants) | `chat` | List members. Optional: `limit`, `search`. |
| `POST /api/chats` | [`create_group`](tools.md#create_group) | `title` | Create supergroup or channel. |
| `DELETE /api/chats` | [`delete_chat`](tools.md#delete_chat) | `chat` | Delete supergroup/channel you own (body). |
| `POST /api/chats/join` | [`join_chat`](tools.md#join_chat) | `chat` | Join public channel/group. |
| `POST /api/chats/invite` | [`join_via_invite`](tools.md#join_via_invite) | `invite` | Join via private `t.me/+hash`. |
| `POST /api/chats/leave` | [`leave_chat`](tools.md#leave_chat) | `chat` | Leave. |
| `GET /api/chats/{chat}/linked` | [`get_linked_chat`](tools.md#get_linked_chat) | none | Resolve a channel's linked discussion group. |
| `POST /api/chats/{chat}/admin/ban` | [`ban_user`](tools.md#ban_user) | `user` | Ban a user. Optional `until_seconds`. |
| `POST /api/chats/{chat}/admin/unban` | [`unban_user`](tools.md#unban_user) | `user` | Lift a ban. |
| `POST /api/chats/{chat}/admin/kick` | [`kick_user`](tools.md#kick_user) | `user` | Kick (ban + immediate unban). |
| `POST /api/chats/{chat}/admin/promote` | [`promote_user`](tools.md#promote_user) | `user` | Grant the admin rights set to `true` (at least one), with an optional `title`. |
| `POST /api/chats/{chat}/admin/demote` | [`demote_user`](tools.md#demote_user) | `user` | Strip admin rights. |
| `POST /api/polls` | [`create_poll`](tools.md#create_poll) | `chat`, `question`, `options` | Create a poll. Optional `multiple_choice`, or `quiz` with `correct_option` and `solution`. |
| `POST /api/polls/{id}/vote` | [`vote_poll`](tools.md#vote_poll) | `chat`, `options` | Vote (0-based indices). |
| `GET /api/polls/{id}/results` | [`get_poll_results`](tools.md#get_poll_results) | `chat` | Current vote counts. |
| `GET /api/throttle/status` | [`throttle_status`](tools.md#throttle_status) | none | Live rate-limit + cache state. |
| `GET /api/account/health` | [`account_health`](tools.md#account_health) | none | Flood-risk tier. |
| `GET /metrics` | none | none | Prometheus exposition (no auth). |
| `WS /ws/updates` | none | `?token=...` | Stream incoming Telegram events. |

Path parameters take the same [chat references](tools.md#chat-references) as the body: `@username`, a numeric ID or `me`. A phone number (`+...`) or a `t.me/...` link in the path needs URL-encoding (`%2B` and so on).

## GET /api/me

Who am I right now.

```http
GET /api/me
```

```json
{
  "id": 123456789,
  "type": "User",
  "username": "someone",
  "first_name": "Some",
  "phone": "+15550000000"
}
```

Parameters: [`get_me`](tools.md#get_me).

## GET /api/entities

Resolve any chat reference to a full profile.

```http
GET /api/entities?chat=@telegram
```

```json
{
  "id": 1234567,
  "type": "Channel",
  "username": "telegram",
  "title": "Telegram"
}
```

Parameters: [`get_entity`](tools.md#get_entity).

## GET /api/dialogs

```http
GET /api/dialogs?limit=10&archived=false
```

Parameters: [`get_dialogs`](tools.md#get_dialogs).

```json
[
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
```

## GET /api/messages

```http
GET /api/messages?chat=me&limit=5&search=hello
```

Parameters: [`get_messages`](tools.md#get_messages).

```json
[
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
```

Newest first. Returns `[]` if nothing matches.

## POST /api/messages

Send a message. The same endpoint handles **text** and **files**. If the body contains `file_url`, the URL is fetched and sent as media (with `text` becoming the caption); otherwise `text` is sent as a plain message.

```http
POST /api/messages
Content-Type: application/json

{
  "chat": "@someone",
  "text": "**hello** from a container",
  "parse_mode": "md",
  "silent": true,
  "reply_to": 4241
}
```

```http
POST /api/messages
Content-Type: application/json

{
  "chat": "@someone",
  "file_url": "https://example.com/photo.jpg",
  "text": "look at this shit",
  "silent": true
}
```

Parameters: [`send_message`](tools.md#send_message) for text, [`send_file`](tools.md#send_file) with `file_url`.

```json
{
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
```

## PATCH /api/messages/{id}

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

Parameters: [`edit_message`](tools.md#edit_message).

```json
{
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
```

## DELETE /api/messages

Delete messages by ID.

```http
DELETE /api/messages
Content-Type: application/json

{
  "chat": "@someone",
  "message_ids": [4242, 4243],
  "revoke": true
}
```

Parameters: [`delete_messages`](tools.md#delete_messages).

```json
{
  "deleted": 2,
  "requested": 2
}
```

## POST /api/messages/forward

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

Parameters: [`forward_messages`](tools.md#forward_messages).

```json
[
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
```

## POST /api/messages/read

Mark messages as read.

```http
POST /api/messages/read
Content-Type: application/json

{ "chat": "@someone", "max_id": 0 }
```

Parameters: [`mark_read`](tools.md#mark_read).

```json
{
  "ok": true
}
```

## GET /api/participants

List members of a group or channel.

```http
GET /api/participants?chat=-1001234567890&limit=50&search=john
```

Parameters: [`get_participants`](tools.md#get_participants).

```json
[
  {
    "id": 123456789,
    "type": "User",
    "username": "johndoe",
    "first_name": "John"
  }
]
```

Large public channels may return a limited set or require admin rights.

## POST /api/chats

Create a supergroup or broadcast channel.

```http
POST /api/chats
Content-Type: application/json

{ "title": "my-group", "megagroup": true }
```

Parameters: [`create_group`](tools.md#create_group).

```json
{
  "id": 1234567890,
  "type": "Channel",
  "title": "my-group"
}
```

## DELETE /api/chats

Delete a supergroup or channel you own. **Irreversible.**

```http
DELETE /api/chats
Content-Type: application/json

{ "chat": "-1001234567890" }
```

```json
{
  "ok": true
}
```

Parameters: [`delete_chat`](tools.md#delete_chat).

## POST /api/chats/join

Join a public channel or supergroup.

```http
POST /api/chats/join
Content-Type: application/json

{ "chat": "@somegroup" }
```

```json
{
  "ok": true
}
```

Parameters: [`join_chat`](tools.md#join_chat).

## POST /api/chats/leave

Leave a channel, supergroup or basic group.

```http
POST /api/chats/leave
Content-Type: application/json

{ "chat": "@somegroup" }
```

```json
{
  "ok": true
}
```

Parameters: [`leave_chat`](tools.md#leave_chat).

## Errors

| Status | When |
|---|---|
| `400` | Bad JSON, a body that is not an object, a validation failure, or a value the tool refuses (such as a file over `max_bytes`). `detail` says what was wrong; for a validation failure it is pydantic's list of errors. |
| `401` | Missing or wrong Bearer token (only when `TELETHON_AUTH_KEY` is set). |
| `403` | A write while `TELETHON_READ_ONLY` is on: `read-only mode: write operations are disabled`. |
| `404` | Unknown endpoint, or an unknown admin action in `/api/chats/{chat}/admin/{action}`. |
| `429` | Telegram sent a `FLOOD_WAIT` longer than `TELETHON_FLOOD_SLEEP_THRESHOLD`. `Retry-After` holds the seconds to wait, and `detail` is `{"telegram_error": "FloodWaitError", "message": ..., "seconds": ...}`. |
| `502` | Telegram refused the call for another reason (`ChatWriteForbiddenError`, `MessageIdInvalidError` and so on). `detail` is `{"telegram_error": <Telethon error class>, "message": ...}`. |
| `500` | A bug. The server log has the traceback; please [open an issue](https://github.com/psyb0t/docker-telethon-plus/issues). |

## Health

```http
GET /healthz
```

```json
{ "status": "ok", "authorized": true }
```

Always public, no auth required. The server only starts answering once the session is authorized, so a running server says `"authorized": true`. A bad or revoked session stops the container at startup instead; see [Troubleshooting](troubleshooting.md#telethon_session-is-not-authorized).
