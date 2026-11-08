---
title: "MCP and agents"
description: "Connect Claude Code, Codex, OpenClaw or any MCP client to telethon-plus's /mcp/ endpoint: transport, auth, tool errors and the agent plugins."
nav_order: 6
---

# MCP and agents

telethon-plus serves a [Model Context Protocol](https://modelcontextprotocol.io) endpoint beside the REST API, so an agent can use the account directly. Every tool on the [Tools](tools.md) page is an MCP tool with the same name and parameters.

## The endpoint

```
http://your-host:8080/mcp/
```

It uses the [streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports#streamable-http) in stateless mode, so every request stands alone and there is no session to keep. When `TELETHON_AUTH_KEY` is set, send it as `Authorization: Bearer <key>`, the same as for REST.

An agent can do anything the account can, including sending, deleting and leaving chats. For an agent that should only read, start the server with `TELETHON_READ_ONLY=true`; see [Security](security.md#safety-switches).

## Tool results and errors

A tool returns the same JSON its REST route returns. A failure comes back as an error result (`isError: true`) whose text is a JSON object with an `error` field, in place of the HTTP status:

| `error` | When | Other fields |
| --- | --- | --- |
| `invalid_argument` | The arguments failed validation, or the tool refused a value. | `message` |
| `read_only` | A write while `TELETHON_READ_ONLY` is on. | `message` |
| `flood_wait` | Telegram sent a `FLOOD_WAIT` longer than `TELETHON_FLOOD_SLEEP_THRESHOLD`. Wait `seconds` before repeating that call. | `seconds`, `message` |
| `telegram_rpc` | Telegram refused the call for another reason, such as a chat the account may not write to. | `type` (the Telethon error class), `message` |

`download_media` returns the file as base64 over MCP; REST streams the raw bytes instead.

## Claude Code

```bash
claude plugin marketplace add psyb0t/agents
claude plugin install telethon-plus@psyb0t
```

Claude Code asks for the server URL and, when auth is on, the key, which it keeps in the OS keychain.

## Codex

```bash
codex plugin marketplace add psyb0t/agents
codex plugin add telethon-plus@psyb0t
```

Installed from the marketplace, the skill runs as `$telethon-plus:telethon-plus`. Codex also picks it up with no install in any checkout that has [`.agents/skills/`](https://github.com/psyb0t/docker-telethon-plus/tree/main/.agents/skills), where it runs as `$telethon-plus`. Any other agent that reads `.agents/skills/`, such as [peen](https://github.com/psyb0t/peen), uses it the same way.

## OpenClaw

The skill is on ClawHub:

```bash
openclaw skills install @psyb0t/telethon-plus
```

For MCP clients that only speak local stdio, the [`@psyb0t/telethon-plus` plugin](https://github.com/psyb0t/docker-telethon-plus/tree/main/.agents/plugins/telethon-plus) bridges stdio to the server's `/mcp/`:

```bash
openclaw plugins install clawhub:@psyb0t/telethon-plus
```

Then set `TELETHON_PLUS_URL` to the server's base URL (such as `http://localhost:8080`; the bridge adds `/mcp/`) and `TELETHON_AUTH_KEY` when the server requires it.

## Any other MCP client

Point it at `http://your-host:8080/mcp/` with the streamable HTTP transport, and add the `Authorization` header when auth is on. Claude Desktop and most agent frameworks accept that directly.
