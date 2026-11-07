# HTTP API

Every route, its parameters and its response. The [README](../README.md) has the configuration, the [tool list](../README.md#tools) and the MCP endpoint.

Standard REST API. JSON in, JSON out. 2xx returns the resource directly; errors return `{"detail": ...}`.

If `TELETHON_AUTH_KEY` is set, every request (except `/healthz`) needs:

```http
Authorization: Bearer your-secret-key
```

## GET /api/me

Who am I right now.

```http
GET /api/me
```

```json
{
    "id": 123456789,
    "type": "User",
    "username": "psyb0t",
    "first_name": "Ciprian",
    "phone": "+40..."
  }
```

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

## GET /api/dialogs

```http
GET /api/dialogs?limit=10&archived=false
```

| Param | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 20 | How many dialogs (1 to 200) |
| `archived` | bool | false | Include archived chats |

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

| Param | Type | Default | Description |
|---|---|---|---|
| `chat` | string | required | Chat to read from |
| `limit` | int | 20 | How many messages (1 to 200) |
| `offset_id` | int | 0 | Start from this message ID (pagination) |
| `search` | string | none | Full-text search filter |

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
  "chat": "@psyb0t",
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
  "chat": "@psyb0t",
  "file_url": "https://example.com/photo.jpg",
  "text": "look at this shit",
  "silent": true
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `chat` | string | required | Target chat |
| `text` | string | required if no `file_url` | Message text or caption (1 to 4096 chars) |
| `file_url` | string | null | HTTPS URL to fetch and send as media. Telegram auto-picks media type from extension/MIME; override with `force_document`. |
| `parse_mode` | string | null | `md` / `markdown` / `html` / null |
| `reply_to` | int | null | Message ID to reply to |
| `silent` | bool | false | Send without notification |
| `link_preview` | bool | true | Show link previews (text only) |
| `schedule` | string | null | ISO datetime in the future to schedule the send |
| `force_document` | bool | false | When sending a file: send as generic doc instead of letting Telegram pick |
| `max_bytes` | int | 52428800 | When sending a file: reject larger than this (max 2 GB) |

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

| Field | Type | Default | Description |
|---|---|---|---|
| `chat` | string | required | Chat containing the message |
| `text` | string | required | New text (1 to 4096 chars) |
| `parse_mode` | string | null | `md` / `html` / null |
| `link_preview` | bool | true | Show link previews |

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
{ "deleted": 2, "requested": 2 }
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

| Field | Type | Default | Description |
|---|---|---|---|
| `from_chat` | string | required | Source chat |
| `to_chat` | string | required | Destination chat |
| `message_ids` | list[int] | required | IDs to forward (max 100) |
| `silent` | bool | false | Forward without notification |

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

{ "chat": "@psyb0t", "max_id": 0 }
```

| Field | Type | Default | Description |
|---|---|---|---|
| `chat` | string | required | Chat to mark as read |
| `max_id` | int | 0 | Mark up to this message ID. `0` = mark all. |

```json
{ "ok": true }
```

## GET /api/participants

List members of a group or channel.

```http
GET /api/participants?chat=-1001234567890&limit=50&search=john
```

| Param | Type | Default | Description |
|---|---|---|---|
| `chat` | string | required | Group or channel |
| `limit` | int | 100 | Max members to return (1 to 1000) |
| `search` | string | none | Filter by name |

```json
[
  { "id": 123456789, "type": "User", "username": "johndoe", "first_name": "John" }
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

| Field | Type | Default | Description |
|---|---|---|---|
| `title` | string | required | Group name (1 to 255 chars) |
| `megagroup` | bool | true | `true` = supergroup, `false` = broadcast channel |

```json
{ "id": 1234567890, "type": "Channel", "title": "my-group" }
```

## DELETE /api/chats

Delete a supergroup or channel you own. **Irreversible.**

```http
DELETE /api/chats
Content-Type: application/json

{ "chat": "-1001234567890" }
```

```json
{ "ok": true }
```

## POST /api/chats/join

Join a public channel or supergroup.

```http
POST /api/chats/join
Content-Type: application/json

{ "chat": "@somegroup" }
```

```json
{ "ok": true }
```

## POST /api/chats/leave

Leave a channel or supergroup.

```http
POST /api/chats/leave
Content-Type: application/json

{ "chat": "@somegroup" }
```

```json
{ "ok": true }
```

## Errors

| Status | When |
|---|---|
| `400` | Bad JSON, validation failure, or Telethon said the input is nonsense. Body has details. |
| `401` | Missing or wrong Bearer token (only when `TELETHON_AUTH_KEY` is set). |
| `404` | Unknown endpoint. |
| `502` | Telegram threw an RPC error (`FloodWaitError`, `ChatWriteForbiddenError`, etc.). Body has the error class and message. |

## Health

```http
GET /healthz
```

```json
{ "status": "ok", "authorized": true }
```

`authorized: false` means the container started but the session is fucked: bad string, revoked, or Telegram unreachable. Always public, no auth required.
