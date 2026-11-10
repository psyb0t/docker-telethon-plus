# docker-telethon-plus

Instructions for coding agents (Codex, Claude Code and others) and for people changing this repository. Users of the image want the documentation site, https://psyb0t.github.io/docker-telethon-plus/, not this file.

## Agent setup

`CLAUDE.md` is a committed symlink to `AGENTS.md`, and `.claude` is a committed symlink to `.agents`, so Claude Code reads the same instructions, rules and skills as Codex. Claude's Edit and Write tools refuse to write through a symlink, so edit `AGENTS.md` and `.agents/rules/*.md` directly.

## What it is

One Docker image, `psyb0t/telethon-plus`, that logs into a Telegram user account with [Telethon](https://codeberg.org/Lonami/Telethon) (MTProto, not the Bot API) and exposes it as a JSON HTTP API under `/api/` and an MCP server at `/mcp/`, from one tool registry. On top sit a flood guard (per-method buckets, per-chat gaps, global gap with jitter, adaptive backoff), a persistent entity cache, live updates over `/ws/updates` and an optional webhook, Prometheus metrics, bearer auth, read-only and dry-run modes. The same image runs the one-time interactive login that prints the session string. The repository also ships an agent skill, an OpenClaw stdio-to-HTTP MCP bridge plugin, and Claude Code and Codex plugin manifests.

## Architecture

- `app/__main__.py`: entry point. `python -m app` (or `serve`) reads `Config.from_env()`, configures logging (text or JSON, with `token=` query values redacted from uvicorn's lines) and runs uvicorn. `python -m app login` runs the login flow with `Config.for_login()`. A config error prints `config error: ...` and exits 2.
- `app/config.py`: the frozen `Config`, built from `TELETHON_*` environment variables. Invalid values raise `RuntimeError` at startup.
- `app/server.py`: `build_app()` creates the FastAPI app. Middleware, outermost first: auth (when `TELETHON_AUTH_KEY` is set; `/healthz` and `/metrics` stay public), the `X-Throttle-*`/`X-RateLimit-*` response headers on `/api/` paths, and the read-only gate (403 on write methods under `/api/`, except `/api/entities/bulk`). Hand-written REST routes call the registry tools, `build_mcp()` mounts every registry tool on a stateless FastMCP streamable HTTP app at `/mcp/`, and `/ws/updates` streams events. Handler exceptions map to HTTP codes in `_http_error` (403 read-only, 429 with `Retry-After` on `FloodWaitError`, 502 other Telegram RPC errors, 400 `ValueError`) and to an `isError` result in `_mcp_error` over MCP.
- `app/tools.py`: `REGISTRY`, one `Tool` per name (34 of them), each a pydantic params model with `extra="forbid"` plus an async handler `(holder, params)`. Write tools call `holder.assert_writeable()` and return a `dry_run` answer when `TELETHON_DRY_RUN` is on.
- `app/client.py`: `TelethonHolder`, the single `TelegramClient` behind one `asyncio.Lock`. `guard(bucket, chat_key)` takes the throttle slot, then the lock, and records `FloodWaitError`. `resolve_entity()` tries the entity cache, then Telethon, then the account's dialogs for numeric ids. `start()` refuses to boot an unauthorized session.
- `app/throttle.py`: `Throttler` with the token buckets, per-chat send and read intervals, global gap and jitter, and the adaptive multiplier.
- `app/cache.py`: `EntityCache`, a JSON file (default `/cache/entities.json`) of id, type and access hash, bound to the account id.
- `app/updates.py`: `UpdateBroker`, which fans Telethon events out to websocket subscribers (bounded queues, slow subscribers closed with 1013) and to `TELETHON_POST_TO_URL`.
- `app/metrics.py`: in-process counters rendered as Prometheus text, no extra dependency.
- `app/login.py`: interactive login; prints the session string and writes it to `TELETHON_SESSION_OUTPUT_FILE` (mode 0600) when set. `login.py` at the root is a shim that calls it.

Request path: REST route or MCP tool call -> params model validation -> tool handler -> `holder.resolve_entity()` and `holder.guard()` -> Telethon -> Telegram. Incoming events: Telethon handler -> `UpdateBroker` -> websocket queues and the webhook.

## Stack

- Python 3.14 (`requires-python >=3.14,<3.15`), image based on `python:3.14.9-slim` pinned by digest, `tini` as PID 1, runs as uid 1000.
- Runtime dependencies pinned exactly in `pyproject.toml` and locked with hashes in `uv.lock`: telethon, fastapi, uvicorn, pydantic, mcp (v1, for `mcp.server.fastmcp`), httpx, cryptg, python-socks, hachoir.
- uv for locking and installs, with `[tool.uv] exclude-newer` as a seven-day supply-chain age gate.
- Dev tools in the `dev` group: pytest, pytest-asyncio, pytest-cov, flake8, mypy, black, isort. Security scanners (semgrep, bandit, pip-audit) live in a separate venv in `Dockerfile.dev`.
- Docs: GitHub Pages from `docs/`, Jekyll with Just the Docs.
- CI: `.github/workflows/pipeline.yml` calling `psyb0t/reusable-github-workflows`.
- License: GPL-2.0-or-later (hachoir is GPL v2 only). The OpenClaw plugin under `.agents/plugins/telethon-plus/` is MIT.

## Layout

| Path | What it holds |
| --- | --- |
| `app/` | The service (see Architecture) |
| `login.py` | Backwards-compatible login shim |
| `Dockerfile` | The production image (builder stage runs `uv sync --frozen --no-dev`) |
| `Dockerfile.dev` | The dev image every lint, test and package target runs in |
| `pyproject.toml`, `uv.lock` | Dependencies, the age gate, black/isort/mypy settings |
| `.flake8` | flake8 settings (line length 100) |
| `Makefile` | Every build, run, lint, test, scan, docs and package command |
| `scripts/sec.sh` | semgrep + bandit + pip-audit merged into `sec.sarif`; never fails |
| `scripts/bump_exclude_newer.sh` | Moves `exclude-newer` to seven days ago before a dependency change |
| `tests/unit/` | Offline unit tests with a mocked Telethon client |
| `tests/` | Integration suite against a real account (`tests/conftest.py` starts the image) |
| `docs/` | The documentation site |
| `.agents/skills/telethon-plus/` | The agent skill, its `references/setup.md` and `scripts/telethon.sh` |
| `.agents/plugins/telethon-plus/` | The OpenClaw stdio-to-HTTP MCP bridge plugin |
| `.agents/.claude-plugin/`, `.agents/.codex-plugin/` | Claude Code and Codex plugin manifests |
| `.agents/rules/` | Rules for changing this repository |
| `server.json`, `glama.json` | MCP Registry and Glama metadata |
| `.env.example`, `tests/.env.example` | Settings templates for `make run`/`make test` and the optional integration chats |
| `.github/workflows/` | `pipeline.yml` plus mirror, issue-pull and collaborators-only housekeeping |

## Commands

Docker and Make are enough. Everything except `build`, `build-test`, `run` and `login` runs in the dev image.

```bash
make help        # every target
make build       # psyb0t/telethon-plus:latest
make run         # run on :8080 with TELETHON_* from .env
make login       # interactive login, writes TELETHON_SESSION to .env
make lint        # flake8 + mypy
make format      # isort + black
make test-unit   # unit tests with the coverage floor, what CI runs
make test        # integration suite, needs .env with a real account and TEST_CHAT
make sec         # security scan into sec.sarif, reports only
make audit       # pip-audit on the locked runtime deps, fails on a finding
make docs        # build the site into .testing/docs-site
make dev-image   # rebuild the dev image after a dependency change
```

## Project-specific landmines

- Every Telegram call goes through `holder.guard(...)`. A direct `holder.client` call skips the throttle, the lock and the flood accounting, and can get the account limited or banned.
- One session, one client. Two containers on the same session string make Telegram end the session, so the integration suite runs one container at a time.
- The session string and `TELETHON_AUTH_KEY` are full account access. Never log them, never put them in test output, fixtures, docs examples or commits. `/ws/updates` takes the key as `?token=`, which is why `__main__.py` redacts it from uvicorn's logs.
- `make test` writes to a real account. Do not run it unless asked.
- The FastAPI `version` in `build_app()` and the `0.0.0` in `pyproject.toml`, `server.json` and the plugin `package.json` are not the release version. The release version is the git tag.

## Rules

Rules for every change live in `.agents/rules/`, one file per topic. Read the ones that match what you are touching before editing.
