---
title: "Getting started"
description: "Get a Telegram API ID, log in once to mint a session string, then run the telethon-plus container and make the first API call."
nav_order: 2
---

# Getting started

Three steps: get Telegram app credentials, log in once to get a session string, run the container with both.

## 1. Get an API ID and hash

Create an app at <https://my.telegram.org/apps> with the Telegram account you want to drive. Telegram shows an `api_id` (a number) and an `api_hash`. These identify the app, not the account.

## 2. Log in once

Telegram makes you prove you are a human once: phone number, the login code Telegram sends you, and the two-step verification password if the account has one. The login prints a session string, and the server uses that string from then on, so you never type a code again.

Without the repository:

```bash
docker run --rm -it \
  -e TELETHON_API_ID=123456 \
  -e TELETHON_API_HASH=your-api-hash \
  psyb0t/telethon-plus login
```

Copy the session string it prints.

From a checkout of the repository, `make login` does the same and writes `TELETHON_SESSION` into `.env` for you:

```bash
cp .env.example .env
$EDITOR .env  # set TELETHON_API_ID and TELETHON_API_HASH
make login
```

The session string is full access to the account. Whoever has it is you. Keep it out of Git, chat logs and screenshots, and see [Security](security.md).

## 3. Run it

```yaml
services:
  telethon-plus:
    image: psyb0t/telethon-plus
    ports:
      - "127.0.0.1:8080:8080"
    environment:
      TELETHON_API_ID: "123456"
      TELETHON_API_HASH: "your-api-hash"
      TELETHON_SESSION: "1Aa...the string from the login..."
      TELETHON_AUTH_KEY: "a-long-random-string"
    volumes:
      - telethon-cache:/cache
    restart: unless-stopped

volumes:
  telethon-cache:
```

`TELETHON_AUTH_KEY` makes every request carry `Authorization: Bearer <key>`; without it anyone who reaches the port can use your account. The `/cache` volume keeps the [entity cache](throttling.md#entity-cache) across restarts, which saves username lookups. Binding to `127.0.0.1` keeps the port off the network.

From a checkout, `make run` builds the image and starts it on port 8080 with the values from `.env`.

## 4. Make a call

```bash
export TELETHON_AUTH_KEY=a-long-random-string  # the key from the compose file
curl -s http://127.0.0.1:8080/healthz
curl -s -H "Authorization: Bearer $TELETHON_AUTH_KEY" http://127.0.0.1:8080/api/me
curl -s -H "Authorization: Bearer $TELETHON_AUTH_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"chat": "me", "text": "hello from telethon-plus"}' \
  http://127.0.0.1:8080/api/messages
```

`/healthz` answers without the key once the server is up, which it only is after the session authorized. `GET /api/me` returns the account profile. The last call sends a message to your own Saved Messages (`me`).

Next: every tool is on the [Tools](tools.md) page, the REST details are in [HTTP API](http-api.md), and agents connect as described in [MCP and agents](mcp.md).
