---
title: "Home"
description: "telethon-plus runs your Telegram account as an HTTP API and an MCP server: Telethon (MTProto) in a Docker container, with flood protection built in."
nav_order: 1
permalink: /
seo:
  type: WebSite
---

# docker-telethon-plus

Your Telegram account, but it takes HTTP requests. telethon-plus wraps [Telethon](https://codeberg.org/Lonami/Telethon), the real MTProto user client and not the Bot API, behind a JSON HTTP API and a [Model Context Protocol](https://modelcontextprotocol.io) endpoint, in one Docker image.

Same tools, two doors. POST some JSON, or point an AI agent at `/mcp/` and let it work. Either way it talks to Telegram as you, with full account access, so read [Security](security.md) before you put it anywhere.

```
+-------------------+        +-----------------------+
|  Any HTTP client  | -----> |  REST  /api/...       | --+
+-------------------+        +-----------------------+   |
                                                         |   +-----------+        Telegram
+-------------------+        +-----------------------+   +-> |  Telethon | <----> servers
|  MCP-aware agent  | -----> |  /mcp/ (streamable    | --+   +-----------+       (MTProto)
|  (Claude, etc.)   |        |   HTTP transport)     |
+-------------------+        +-----------------------+
```

One Telethon client behind one lock. Both doors share one tool registry, so a tool takes the same parameters and does the same thing over REST and MCP.

## Install

```bash
docker pull psyb0t/telethon-plus
```

The image runs on `linux/amd64` and `linux/arm64`. You need a Telegram app ID and hash from <https://my.telegram.org/apps> and a session string from the one-time login. [Getting started](getting-started.md) walks through both.

## Where to go next

| You want to | Read |
| --- | --- |
| Log in once and get the container answering | [Getting started](getting-started.md) |
| Set an environment variable, a proxy or the port | [Configuration](configuration.md) |
| See every tool, its route and its parameters | [Tools](tools.md) |
| Call the REST API: auth, responses, errors, examples | [HTTP API](http-api.md) |
| Hook up Claude, Codex or another agent | [MCP and agents](mcp.md) |
| Receive incoming messages live or by webhook | [Updates and webhooks](updates.md) |
| Keep the account from getting rate-limited or banned | [Throttling and cache](throttling.md) |
| Watch metrics, flood state and health | [Observability](observability.md) |
| Lock it down before exposing it | [Security](security.md) |
| Fix a startup error or a failing call | [Troubleshooting](troubleshooting.md) |
| Change the code, run the tests, cut a release | [Development](development.md) |
