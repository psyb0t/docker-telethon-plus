# docker-telethon-plus

[![CI](https://github.com/psyb0t/docker-telethon-plus/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/psyb0t/docker-telethon-plus/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/docker-telethon-plus/badges/version.svg)](https://github.com/psyb0t/docker-telethon-plus/releases)
[![license](https://raw.githubusercontent.com/psyb0t/docker-telethon-plus/badges/license.svg)](https://github.com/psyb0t/docker-telethon-plus/blob/main/LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/psyb0t/telethon-plus?style=flat-square)](https://hub.docker.com/r/psyb0t/telethon-plus)

Your Telegram account, but it takes HTTP requests. Wraps [Telethon](https://codeberg.org/Lonami/Telethon), the real MTProto userbot client and not that neutered Bot API garbage, behind a JSON HTTP API and a Model Context Protocol endpoint.

Same tools, two doors. POST some JSON, or point your AI agent at `/mcp/` and let it go nuts. Either way it's talking to Telegram as *you*, with full account access.

One login. One session string. Never type a code again.

## Table of Contents

- [Documentation](#documentation)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Agent integrations](#agent-integrations)
- [Sponsor](#sponsor)
- [License](#license)

## Documentation

The full documentation is at **[psyb0t.github.io/docker-telethon-plus](https://psyb0t.github.io/docker-telethon-plus/)**.

| You want to | Read |
| --- | --- |
| Log in once and get the container answering | [Getting started](https://psyb0t.github.io/docker-telethon-plus/getting-started/) |
| Set an environment variable, a proxy or the port | [Configuration](https://psyb0t.github.io/docker-telethon-plus/configuration/) |
| See every tool, its route and its parameters | [Tools](https://psyb0t.github.io/docker-telethon-plus/tools/) |
| Call the REST API: auth, responses, errors, examples | [HTTP API](https://psyb0t.github.io/docker-telethon-plus/http-api/) |
| Hook up Claude, Codex or another agent | [MCP and agents](https://psyb0t.github.io/docker-telethon-plus/mcp/) |
| Receive incoming messages live or by webhook | [Updates and webhooks](https://psyb0t.github.io/docker-telethon-plus/updates/) |
| Keep the account from getting rate-limited or banned | [Throttling and cache](https://psyb0t.github.io/docker-telethon-plus/throttling/) |
| Watch metrics, flood state and health | [Observability](https://psyb0t.github.io/docker-telethon-plus/observability/) |
| Lock it down before exposing it | [Security](https://psyb0t.github.io/docker-telethon-plus/security/) |
| Fix a startup error or a failing call | [Troubleshooting](https://psyb0t.github.io/docker-telethon-plus/troubleshooting/) |
| Change the code, run the tests, cut a release | [Development](https://psyb0t.github.io/docker-telethon-plus/development/) |

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

One Telethon client. One async lock. Both surfaces share the same tool registry, so there is no duplication, no weird state, no bullshit. On top of that sits a flood guard (per-method buckets, per-chat gaps, adaptive backoff and an entity cache) that keeps the account under Telegram's limits; [Throttling and cache](https://psyb0t.github.io/docker-telethon-plus/throttling/) has the details.

## Quick start

Get `API_ID` / `API_HASH` from <https://my.telegram.org/apps>, then log in once. Telegram makes you prove you're a human once: phone number, login code, optionally 2FA. The login prints a session string:

```bash
docker run --rm -it \
  -e TELETHON_API_ID=123456 \
  -e TELETHON_API_HASH=your-api-hash \
  psyb0t/telethon-plus login
```

Then run it with that string:

```yaml
services:
  telethon-plus:
    image: psyb0t/telethon-plus
    ports:
      - "127.0.0.1:8080:8080"
    environment:
      TELETHON_API_ID: "123456"
      TELETHON_API_HASH: "your-api-hash"
      TELETHON_SESSION: "1Aa...long-string-from-login..."
      TELETHON_AUTH_KEY: "a-long-random-string"
    volumes:
      - telethon-cache:/cache
    restart: unless-stopped

volumes:
  telethon-cache:
```

```bash
export TELETHON_AUTH_KEY=a-long-random-string  # the key from the compose file
curl -H "Authorization: Bearer $TELETHON_AUTH_KEY" http://127.0.0.1:8080/api/me
```

> **The session string is full account access.** Whoever has it is you. Don't commit it, don't paste it in Slack, don't tattoo it anywhere. Without `TELETHON_AUTH_KEY`, anyone who reaches the port is you too. Read [Security](https://psyb0t.github.io/docker-telethon-plus/security/) before exposing it.

From a checkout, `make login` writes the session string into `.env` and `make run` starts the container. [Getting started](https://psyb0t.github.io/docker-telethon-plus/getting-started/) has every step.

## Agent integrations

The [skill](https://github.com/psyb0t/docker-telethon-plus/tree/main/.agents/skills/telethon-plus) works in any agent that reads `.agents/skills/` (such as [peen](https://github.com/psyb0t/peen)), and installs natively in the clients below.

### Claude Code

```bash
claude plugin marketplace add psyb0t/agents
claude plugin install telethon-plus@psyb0t
```

Claude Code prompts for the telethon-plus server URL and, if auth is enabled, the bearer token, which it keeps in your OS keychain.

### Codex

```bash
codex plugin marketplace add psyb0t/agents
codex plugin add telethon-plus@psyb0t
```

Installed via the marketplace, the skill invokes as `$telethon-plus:telethon-plus`. Codex also picks the skill up automatically with no install in any repo containing `.agents/skills/`, where it invokes as plain `$telethon-plus`.

### OpenClaw

The skill is published to ClawHub on every release:

```bash
openclaw skills install @psyb0t/telethon-plus
```

For MCP clients that speak local stdio, the [`@psyb0t/telethon-plus`](https://github.com/psyb0t/docker-telethon-plus/tree/main/.agents/plugins/telethon-plus) plugin bridges to the service's `/mcp/` endpoint:

```bash
openclaw plugins install clawhub:@psyb0t/telethon-plus
```

Then set `TELETHON_PLUS_URL` (and `TELETHON_AUTH_KEY` if the server requires auth).

## Sponsor

<a href="https://www.buymeacoffee.com/psyb0t"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-blue.png" alt="Buy Me a Coffee" height="60" width="217"></a>

## License

Copyright (C) 2026 Ciprian Mandache. Licensed under the [GNU General Public License, version 2 or later](https://github.com/psyb0t/docker-telethon-plus/blob/main/LICENSE). The image includes [hachoir](https://github.com/vstinner/hachoir), which is GPL v2 only, so the image as a whole is distributed under GPL v2.
