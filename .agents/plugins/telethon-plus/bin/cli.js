#!/usr/bin/env node
// telethon-plus MCP bridge. A thin stdio<->HTTP proxy: forwards MCP over stdio
// to a running docker-telethon-plus server's Streamable-HTTP endpoint
// (`$TELETHON_PLUS_URL/mcp/`), authenticating with `$TELETHON_AUTH_KEY` when
// the server requires it.
//
// stdout IS the MCP protocol channel, so diagnostics go to stderr only — the
// sole output here is a fatal pre-launch console.error (user-facing CLI
// output). The token is passed to the proxy as an argv header, never logged.
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

const MCP_PATH = "/mcp/";

const base = process.env.TELETHON_PLUS_URL;

if (!base) {
  console.error(
    `[telethon-plus-mcp] Missing TELETHON_PLUS_URL.

Point this bridge at your running docker-telethon-plus server, e.g.:
  export TELETHON_PLUS_URL=http://localhost:8080

docker-telethon-plus is self-hosted — see https://github.com/psyb0t/docker-telethon-plus`,
  );
  process.exit(1);
}

const url = `${base.replace(/\/+$/, "")}${MCP_PATH}`;
const token = process.env.TELETHON_AUTH_KEY;
const proxyEntry = require.resolve("mcp-remote/dist/proxy.js");

const args = [proxyEntry, url, "--transport", "http-only"];
if (token) {
  args.push("--header", `Authorization: Bearer ${token}`);
}
args.push(...process.argv.slice(2));

const result = spawnSync(process.execPath, args, { stdio: "inherit" });
process.exit(result.status ?? 1);
