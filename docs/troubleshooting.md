---
title: "Troubleshooting"
description: "telethon-plus failures by their exact message: config errors, an unauthorized session, 401, 403, 429 FLOOD_WAIT, unknown numeric IDs and websocket closes."
nav_order: 11
---

# Troubleshooting

Each entry starts with the message you see, so search this page for it.

## The container exits with "config error"

```
config error: TELETHON_REQUEST_TIMEOUT must be a number, got '60s'
```

A setting could not be parsed, and the container stops with exit code 2 before connecting to Telegram. The message names the variable and the value it got. The forms you can meet:

| Message | Fix |
| --- | --- |
| `<NAME> environment variable is required` | Set it. `TELETHON_API_ID`, `TELETHON_API_HASH` and `TELETHON_SESSION` are required; `login` needs only the first two. |
| `<NAME> must be an integer, got '...'` | Use a whole number, with no unit: `60`, not `60s`. |
| `<NAME> must be a number, got '...'` or `must be a finite number` | Use a plain number such as `10` or `2.5`. |
| `<NAME> must be >= <n>, got <value>` or `must be > <n>` | The value is below the allowed minimum. Bucket limits and the update buffer must be at least 1, timeouts above 0, intervals and the cache TTL at least 0. |
| `TELETHON_LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL` | Use one of those names. |
| `<NAME> must be boolean-ish, got '...'` | Use `true` or `false` (or `1`, `0`, `yes`, `no`, `on`, `off`). |
| `TELETHON_HTTP_LISTEN_ADDRESS must be host:port` or `invalid port in '...'` | Write it as `0.0.0.0:8080`. |
| `TELETHON_PROXY must start with socks5://, socks4://, http://` | Only those proxy schemes work; MTProto proxies do not. |
| `TELETHON_PROXY must be scheme://[user:password@]host:port` or `TELETHON_PROXY has an invalid port` | Add the port, and percent-encode special characters in the user and password. |

[Configuration](configuration.md) has every variable and its valid values.

## TELETHON_SESSION is not authorized

```
RuntimeError: TELETHON_SESSION is not authorized; generate a fresh one with the login command: ...
```

Telegram does not accept the session string. Either it was mistyped (it is long; copy it whole), or the session was ended: from Telegram's Settings, Devices, by logging out, or by Telegram after a long time unused. The container stops at startup. [Log in again](getting-started.md#2-log-in-once) and replace `TELETHON_SESSION`.

## 401 {"detail":"unauthorized"}

`TELETHON_AUTH_KEY` is set and the request did not carry it. Send `Authorization: Bearer <key>` on `/api/...` and `/mcp/`, or `?token=<key>` on `/ws/updates`, where a wrong token shows up as an HTTP `403` on the websocket handshake. The key is compared byte for byte, so check for a trailing newline or space in whatever stores it.

## 403 read-only mode: write operations are disabled

`TELETHON_READ_ONLY` is on, which refuses every tool that changes something. Over MCP the same refusal is `{"error": "read_only", ...}`. Turn it off and restart if writes are meant to work. See [Security](security.md#safety-switches).

## 429 with telegram_error FloodWaitError

```json
{"detail": {"telegram_error": "FloodWaitError", "message": "A wait of 3600 seconds is required ...", "seconds": 3600}}
```

Telegram asked the account to wait longer than `TELETHON_FLOOD_SLEEP_THRESHOLD` (60 seconds) before repeating that kind of call. The response has a `Retry-After` header with the seconds; over MCP it is `{"error": "flood_wait", "seconds": ...}`. Wait that long before retrying that call. The [adaptive backoff](throttling.md#how-the-layers-stack) has already slowed everything down for the next hour.

Repeated long waits usually come from resolving many new usernames. Resolve them once with [`bulk_resolve`](tools.md#bulk_resolve), keep the `/cache` volume so the cache survives restarts, and keep `TELETHON_BUCKET_RESOLVE_PER_MIN` at its default of 5. [`/api/account/health`](observability.md#get-apiaccounthealth) says `high` while the account is at risk.

## 502 with telegram_error

```json
{"detail": {"telegram_error": "ChatWriteForbiddenError", "message": "You can't write in this chat ..."}}
```

Telegram refused the call for a reason other than flooding: no rights in the chat, a deleted message, a private channel the account is not in, and so on. `telegram_error` is the Telethon error class, which names the reason. Over MCP it is `{"error": "telegram_rpc", "type": ...}`.

## 400 Could not find the input entity

```json
{"detail": "Could not find the input entity for PeerUser(user_id=123456789) ..."}
```

The chat or user was given as a bare numeric ID that Telethon has never seen in this session. Telegram needs an access hash with the ID. Use the `@username` or a `t.me` link once, or list your dialogs, and the ID works after that. See [Chat references](tools.md#chat-references).

## 400 requires a channel or supergroup

```json
{"detail": "ban_user requires a channel or supergroup, but this chat is a Chat"}
```

Moderation tools, `join_chat`, `get_linked_chat` and `delete_chat` work only on channels and supergroups. A `Chat` is an old-style basic group; converting it to a supergroup in Telegram makes it work. `leave_chat` works on basic groups too.

## The websocket closes with 1013

The client fell `TELETHON_UPDATES_BUFFER_SIZE` events behind and lost some of them. Reconnect, then read what was missed with `GET /api/messages`. A client that keeps falling behind should process events faster, or raise the buffer. Close codes are listed on [Updates and webhooks](updates.md#websocket-wsupdates).

## The webhook gets nothing

`TELETHON_POST_TO_URL` only receives events while `TELETHON_UPDATES_ENABLED` is on. A delivery that fails or gets a non-2xx answer is logged as a warning (`webhook ... returned ...` or `webhook delivery failed`) and not retried, so check the container log for those lines.

## make login says "Login failed or was aborted."

The login was cancelled, or the login code or two-step password was wrong, so no session was written to `.env`. Run `make login` again. Login codes arrive in the Telegram app on another device first, not by SMS.
