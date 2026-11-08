---
title: "Security"
description: "What telethon-plus can reach and store, how the auth key and session string work, which routes stay public, and the read-only and dry-run switches."
nav_order: 10
---

# Security

telethon-plus is your Telegram account. Anyone who can call it can read every chat the account is in, send as you, delete messages, leave groups and change admin rights. Treat access to it like access to your phone.

## The session string

`TELETHON_SESSION` is a logged-in session, not a password: whoever holds it is the account, with no login code and no two-step verification password. Keep it in a secret store or an untracked `.env`, never in Git, a chat, an issue or a screenshot.

The `login` command writes `TELETHON_SESSION_OUTPUT_FILE` with mode `0600`, readable only by its owner. `make login` then copies the string into `.env`, which keeps its own permissions, so run `chmod 600 .env` once.

The session shows up in Telegram's own list of active sessions (Settings, Devices) under `TELETHON_DEVICE_MODEL`, `docker-telethon-plus` by default. Terminating it there revokes the string at once; the container then refuses to start until you [log in again](getting-started.md#2-log-in-once).

## The auth key

Without `TELETHON_AUTH_KEY` the server has no authentication at all. Set it to a long random string, such as the output of `openssl rand -hex 32`, before the port is reachable from anything but your own machine.

With it set:

| Route | Needs the key |
| --- | --- |
| `/api/...` | Yes, as `Authorization: Bearer <key>`. |
| `/mcp/` | Yes, as `Authorization: Bearer <key>`. |
| `/ws/updates` | Yes, as `?token=<key>`, because browsers cannot set headers on a websocket; a wrong token refuses the handshake with `403`. telethon-plus's own logs show the token as `[redacted]`, but a reverse proxy in front of it may log the query string whole. |
| `/healthz` | No. It only says whether the server is up. |
| `/metrics` | No. It shows tool names and call counts, flood events and cache size, but no message content. |

The key is compared in constant time. It is a single shared secret, with no users or scopes: everyone with the key has the whole account.

## Network exposure

Publish the port on `127.0.0.1` (`"127.0.0.1:8080:8080"` in compose) unless something on another machine needs it. To reach it from elsewhere, put a reverse proxy with TLS in front, or a private network such as a VPN or a tailnet, and keep the auth key on. The API itself speaks plain HTTP.

## What it stores and where it connects

| What | Where |
| --- | --- |
| Resolved chats and users (IDs, access hashes, names) | `TELETHON_CACHE_PATH`, `/cache/entities.json` by default. No message content. |
| Files sent with `send_file` | Private temp files in `TELETHON_DOWNLOAD_DIR`, deleted after each send. |
| Messages | Nothing is stored. Reads go to Telegram every time. |

Outbound, it connects to Telegram's servers (through `TELETHON_PROXY` when set), to any `file_url` a caller passes to `send_file`, and to `TELETHON_POST_TO_URL` when the webhook is on. Anyone with the auth key can make the server fetch an arbitrary `http(s)` URL through `send_file`, including addresses on the server's own network, so do not hand the key to anyone you would not let make requests from that host.

The webhook forwards every message the account receives, with its sender, to that one URL. Point it only at an endpoint you control, over HTTPS.

## Safety switches

| Switch | Effect | Use it for |
| --- | --- | --- |
| `TELETHON_READ_ONLY=true` | Every tool that changes something answers `403` (REST) or `read_only` (MCP). Reads, status and metrics keep working. | An agent or dashboard that should only read, or an emergency stop. |
| `TELETHON_DRY_RUN=true` | Writes are validated and resolved, then answered with `{"dry_run": true, "would_...": {...}}`; nothing is sent to Telegram. | Testing a script or an agent before it touches the real account. |

Both are set at startup, so changing one means restarting the container.

## The image

The container runs as an unprivileged user (uid 1000) on a digest-pinned Python base. Dependencies are locked with hashes in [`uv.lock`](https://github.com/psyb0t/docker-telethon-plus/blob/main/uv.lock), and nothing published in the seven days before a dependency change enters the lock. CI scans the code with semgrep and bandit, the locked dependencies with pip-audit, and the published image with Grype, and reports the findings in the repository's Security tab.
