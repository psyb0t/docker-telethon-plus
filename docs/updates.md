---
title: "Updates and webhooks"
description: "Receive the account's incoming Telegram events live: the /ws/updates websocket, the outbound webhook, the event payload, and what happens when a client falls behind."
nav_order: 7
---

# Updates and webhooks

telethon-plus listens for four kinds of incoming events on the account and hands each one to every subscriber: `NewMessage`, `MessageEdited`, `MessageDeleted` and `ChatAction` (joins, leaves, title changes and the like). What telethon-plus does itself raises no event: a message it sends, edits or deletes is not echoed back, because Telethon applies the answers to its own requests without dispatching them. Messages from other people, and from your other Telegram apps (with `"out": true`), do arrive. There are two ways to receive them, and both can run at once: the websocket, on by default, and a webhook, on when `TELETHON_POST_TO_URL` is set. `TELETHON_UPDATES_ENABLED=false` turns event listening off, which removes `/ws/updates` and stops the webhook.

## WebSocket: /ws/updates

```javascript
const ws = new WebSocket('ws://your-host:8080/ws/updates?token=YOUR_AUTH_KEY');
ws.onmessage = (e) => console.log(JSON.parse(e.data));
```

Each frame is one event as JSON. When `TELETHON_AUTH_KEY` is set, pass it as `?token=`, because browser websocket clients cannot set an `Authorization` header. A wrong or missing token refuses the handshake with HTTP `403`, so the socket never opens.

Any number of clients can connect, and each gets its own queue of `TELETHON_UPDATES_BUFFER_SIZE` events (default 256). The server closes a socket in these cases:

| Close code | When | What the client should do |
| --- | --- | --- |
| `1013` | The client fell a whole buffer behind and events were dropped. | Reconnect, then read what it missed with [`GET /api/messages`](http-api.md#get-apimessages). |
| `1001` | The server is shutting down. | Reconnect once it is back. |

A websocket only delivers events that happen while it is connected. Nothing is replayed after a reconnect, so a client that must not miss anything reads the chats it cares about with `GET /api/messages` after every connect.

## Outbound webhook: TELETHON_POST_TO_URL

Set `TELETHON_POST_TO_URL` and every event is also POSTed there as JSON, with a `TELETHON_POST_TO_TIMEOUT` second timeout (default 10). Use it for workers that cannot hold a websocket open.

Delivery is best effort and never blocks Telethon. Events wait in a queue of `TELETHON_UPDATES_BUFFER_SIZE`, and one that arrives while the queue is full is dropped with a log line. A POST that fails to connect or gets a 5xx answer is logged and not retried; other answers, 4xx included, are not checked. The webhook runs only while `TELETHON_UPDATES_ENABLED` is on. Every message the account receives goes to that URL, so point it only at an endpoint you control, over HTTPS.

## Event payload

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

| Field | Meaning |
| --- | --- |
| `type` | `NewMessage`, `MessageEdited`, `MessageDeleted` or `ChatAction`. |
| `message.id` | Message ID within the chat. |
| `message.date` | When it was sent, ISO 8601 in UTC. |
| `message.text` | The text, or the caption of a media message. Empty for media without one. |
| `message.out` | `true` when the account itself sent it. |
| `message.media`, `message.media_type` | Whether it has an attachment, and Telethon's type name for it (such as `MessageMediaPhoto`). Fetch the bytes with [`download_media`](tools.md#download_media). |
| `chat_id` | The chat in Telethon's marked form: `-100<id>` for channels and supergroups, `-<id>` for basic groups, the plain ID for private chats. Message listings and entity lookups report the plain ID without the prefix. |

The payload is small on purpose. Use [`GET /api/messages`](http-api.md#get-apimessages) or [`GET /api/entities`](http-api.md#get-apientities) for the full message or the sender's profile. `MessageDeleted` carries `deleted_ids` instead of a message, and `ChatAction` carries the fields Telethon gives it, such as `user_id`.
