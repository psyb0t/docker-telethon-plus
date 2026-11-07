---
title: "Development"
description: "Work on telethon-plus: the repository layout, the dev image and make targets, unit and integration tests, dependency updates under the age gate, CI and releases."
nav_order: 12
---

# Development

Everything except `make build`, `make run` and `make login` runs inside a dev image ([`Dockerfile.dev`](https://github.com/psyb0t/docker-telethon-plus/blob/main/Dockerfile.dev)) with the locked environment baked in, so the host needs only Docker and make.

## Layout

| Path | Holds |
| --- | --- |
| [`app/server.py`](https://github.com/psyb0t/docker-telethon-plus/blob/main/app/server.py) | FastAPI app: REST routes, the auth middleware, the `/mcp/` mount and `/ws/updates`. |
| [`app/tools.py`](https://github.com/psyb0t/docker-telethon-plus/blob/main/app/tools.py) | The tool registry: one pydantic parameter model and one handler per tool. REST and MCP both call these. |
| [`app/client.py`](https://github.com/psyb0t/docker-telethon-plus/blob/main/app/client.py) | The single Telethon client, its lock, entity resolution and the throttle guard every call goes through. |
| [`app/throttle.py`](https://github.com/psyb0t/docker-telethon-plus/blob/main/app/throttle.py), [`app/cache.py`](https://github.com/psyb0t/docker-telethon-plus/blob/main/app/cache.py) | Flood protection and the entity cache ([Throttling and cache](throttling.md)). |
| [`app/updates.py`](https://github.com/psyb0t/docker-telethon-plus/blob/main/app/updates.py) | Incoming events, the websocket fan-out and the webhook ([Updates and webhooks](updates.md)). |
| [`app/config.py`](https://github.com/psyb0t/docker-telethon-plus/blob/main/app/config.py) | Every environment variable ([Configuration](configuration.md)). |
| [`tests/unit/`](https://github.com/psyb0t/docker-telethon-plus/tree/main/tests/unit) | Offline tests with a mocked Telethon client. |
| [`tests/`](https://github.com/psyb0t/docker-telethon-plus/tree/main/tests) | The integration suite, against a real account. |
| [`docs/`](https://github.com/psyb0t/docker-telethon-plus/tree/main/docs) | This site. |

To add a tool, write its parameter model and handler in `app/tools.py` and register it; it shows up over MCP on its own. A REST route for it goes in `app/server.py`, and the tool gets a section on the [Tools](tools.md) page.

## Make targets

```bash
make build        # build psyb0t/telethon-plus:latest
make run          # run it on :8080 with the values from .env
make login        # interactive login, writes TELETHON_SESSION to .env
make lint         # flake8 + mypy
make format       # isort + black
make test-unit    # unit tests with coverage, no Docker or Telegram needed
make test         # integration tests against a real Telegram account
make sec          # semgrep + bandit + pip-audit into sec.sarif (reports, never fails)
make audit        # pip-audit on the locked runtime dependencies (fails on a finding)
make docs        # build this site locally into .testing/docs-site
make dev-image    # rebuild the dev image
make shell        # a shell in the dev image
make clean        # remove the built images
```

`make help` lists them with their descriptions.

## Tests

`make test-unit` runs [`tests/unit/`](https://github.com/psyb0t/docker-telethon-plus/tree/main/tests/unit) with a mocked `TelegramClient`: tools, throttling, the cache, config parsing, the proxy, `send_file` staging, and `/ws/updates` against the real app served by uvicorn on a local port. It needs no account and is what CI runs. It fails when coverage of `app/` drops below `COVERAGE_FLOOR` in the [Makefile](https://github.com/psyb0t/docker-telethon-plus/blob/main/Makefile) (93%); raise the floor as coverage grows, never lower it to get a build through. Two tests walk the whole tool registry, so a new tool must be added to the route table in `tests/unit/test_routes.py` and, if it changes anything, to `WRITE_TOOLS` in `tests/unit/test_tools_registry.py`, where it is checked for read-only and dry-run handling.

`make test` runs the integration suite against a real account. It builds the image, starts it, and calls both REST and MCP: messages get sent and deleted.

```bash
cp .env.example .env
$EDITOR .env  # TELETHON_API_ID, TELETHON_API_HASH, TELETHON_SESSION, TEST_CHAT
make test
```

`TEST_CHAT` is where test messages land; `me` (Saved Messages) keeps them private. Without credentials the suite skips. CI never runs it, because it would need an account.

| Test file | Covers |
| --- | --- |
| `test_health.py` | The container boots, the session authorizes, and the OpenAPI spec lists every route. |
| `test_rest.py` | Validation errors, extra fields refused, a send, edit, fetch and delete round trip, dialogs, entity resolution, a public channel read, group create and delete, participants. |
| `test_mcp.py` | MCP over streamable HTTP: tool discovery, `get_me`, a send and delete round trip, validation errors. |
| `test_auth.py` | 401 on a missing or wrong key, 200 with the right one, `/healthz` public, `/mcp/` protected. |

## Dependencies

Dependencies are in [`pyproject.toml`](https://github.com/psyb0t/docker-telethon-plus/blob/main/pyproject.toml) and locked with hashes in `uv.lock`. `[tool.uv] exclude-newer` keeps any release from the seven days before the last dependency change out of the lock, which gives the community time to catch a malicious upload.

```bash
make pkg-add PKG=name==1.2.3          # add a runtime dependency
make pkg-add PKG=name==1.2.3 GROUP=dev
make pkg-update PKG=name V=1.2.4
make pkg-remove PKG=name
make pkg-upgrade                      # newest allowed version of everything
```

Each of these moves `exclude-newer` to seven days ago first. Rebuild with `make dev-image` afterwards, then run the lint and tests.

## CI and releases

[`.github/workflows/pipeline.yml`](https://github.com/psyb0t/docker-telethon-plus/blob/main/.github/workflows/pipeline.yml) runs on pushes to `main`, on `v*` tags and on non-draft pull requests:

1. `code` runs `make lint`, `make test-unit` and `make sec`, and uploads `sec.sarif` to the Security tab.
2. `docker` builds the image for `linux/amd64` and `linux/arm64` once `code` passes. On `main` it pushes `latest` and `sha-<commit>` and syncs the README to Docker Hub. A pull request builds without pushing.
3. A tag never rebuilds. `wait` waits for the `main` run of the tagged commit to pass, `docker-promote` re-tags that `sha-<commit>` image as the version, `release` writes the GitHub release from the annotated tag's notes, and the skill and plugin go to ClawHub and the MCP registry.

So push to `main`, let the run pass, then tag that commit. The Docker Hub sync fails when `README.md` is over Docker Hub's 25,000-byte limit; move detail from the README into `docs/` rather than trimming the sync.

This site is built by GitHub Pages from `docs/` on `main`. `make docs` builds it locally into `.testing/docs-site` with the same image GitHub uses; check the result before pushing a docs change.
