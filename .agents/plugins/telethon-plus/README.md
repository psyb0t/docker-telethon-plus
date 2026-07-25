# @psyb0t/telethon-plus

An OpenClaw/MCP plugin that connects your agent to a self-hosted
[docker-telethon-plus](https://github.com/psyb0t/docker-telethon-plus)
Telegram userbot API over the [Model Context Protocol](https://modelcontextprotocol.io).

docker-telethon-plus wraps [Telethon](https://codeberg.org/Lonami/Telethon) —
a real MTProto **userbot** client, a full Telegram user account, not the Bot
API — and already serves a Streamable-HTTP MCP endpoint at `/mcp/`. This
package is a thin stdio↔HTTP bridge (via
[`mcp-remote`](https://www.npmjs.com/package/mcp-remote)) for MCP clients that
speak local stdio servers — it forwards everything to your running instance
and authenticates with your bearer token when the server requires one.

> docker-telethon-plus is **self-hosted** and drives a **real Telegram user
> account** — full read/write access to every DM, group, and channel that
> account can reach. This plugin does not ship the Telegram client or
> provision/log in an account — it connects to a server that **you** run and
> that is already authorized. See the
> [docker-telethon-plus repo](https://github.com/psyb0t/docker-telethon-plus)
> to stand one up.

## Tools

The 34 docker-telethon-plus MCP tools become available to your agent:
messages (`send_message`, `send_file`, `get_messages`, `get_message`,
`edit_message`, `delete_messages`, `forward_messages`, `mark_read`,
`download_media`), reactions/pins (`set_reaction`, `remove_reaction`,
`pin_message`, `unpin_message`), dialogs/entities (`get_me`, `get_entity`,
`bulk_resolve`, `get_dialogs`), chats (`create_group`, `delete_chat`,
`join_chat`, `leave_chat`, `join_via_invite`, `get_linked_chat`,
`get_participants`), admin (`ban_user`, `unban_user`, `kick_user`,
`promote_user`, `demote_user`), polls (`create_poll`, `vote_poll`,
`get_poll_results`), and health (`throttle_status`, `account_health`).

## Configuration

| Env var | Required | Description |
|---|---|---|
| `TELETHON_PLUS_URL` | yes | Base URL of your running docker-telethon-plus server, e.g. `http://localhost:8080`. The bridge appends `/mcp/`. |
| `TELETHON_AUTH_KEY` | no | Bearer token — only if the docker-telethon-plus server was started with `TELETHON_AUTH_KEY` set. |

## Install

Install it into your OpenClaw agent from ClawHub:

```bash
openclaw plugins install clawhub:@psyb0t/telethon-plus
```

Then set `TELETHON_PLUS_URL` (and `TELETHON_AUTH_KEY` if your server uses
auth) in the plugin's environment.

## Native remote MCP (no install)

If your MCP client already supports **remote** Streamable-HTTP servers, you
don't need this bridge — point the client straight at
`$TELETHON_PLUS_URL/mcp/` with an `Authorization: Bearer <token>` header.

## License

MIT. See [LICENSE](LICENSE).
