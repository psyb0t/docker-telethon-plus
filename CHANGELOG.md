# Changelog

All notable changes per release. Versions follow [semver](https://semver.org).

## v0.6.1, 2026-11-07

The first published release with the v0.6.0 changes below. v0.6.0 was tagged, but its image build failed at the last step, so no `v0.6.0` image, GitHub release or registry entry exists.

### Fixed

- **Image builds failed while syncing the Docker Hub description.** Docker Hub refuses a long description over 25,000 bytes, and the README, which the build pushes there, had grown past that. The full HTTP API reference moved from the README to [docs/http-api.md](docs/http-api.md), and the README links to it.

## v0.6.0, 2026-11-07

### Fixed

- **A `/ws/updates` client that fell behind was cut off without being told.** When its queue of `TELETHON_UPDATES_BUFFER_SIZE` events filled, the server stopped sending it events but kept the websocket open, so the client waited forever, and the server's own shutdown hung on that connection. Such a client is now closed with code `1013`, which tells it that it lost events and should reconnect and catch up with `GET /api/messages`. A shutdown closes open clients with `1001`, and a client that disconnects frees its subscription at once instead of at the next event.
- **`TELETHON_PROXY` did nothing.** It was read but never passed to Telegram, so the server and the login helper always connected directly. Both now connect through it. It takes `socks5://`, `socks4://` or `http://` as `scheme://[user:password@]host:port`, and an invalid value stops startup with a message.
- **`TELETHON_DOWNLOAD_DIR` did nothing.** `send_file` always staged downloads under `/tmp/telethon-plus` with a predictable file name. It now uses `TELETHON_DOWNLOAD_DIR`, with a private temp file per upload, and deletes it whether the download, the size check or the send fails.

### Changed

- **Python 3.14 and current dependencies.** The image is built on `python:3.14.9-slim` (was 3.12) with Telethon 1.45.0 (was 1.41.2), FastAPI 0.162.0, uvicorn 0.54.0, pydantic 2.14.2, mcp 1.30.0 and cryptg 0.6.0, which now ships arm64 wheels too. `python-socks` is added for the proxy support. Dependencies are locked with hashes in `uv.lock`, and nothing released in the seven days before a dependency change enters it.
- **The image has a healthcheck** on `/healthz`, on the port `TELETHON_HTTP_LISTEN_ADDRESS` sets.
- **Development runs in a dev image.** `make lint` (flake8 + mypy), `make test-unit`, `make test`, `make sec` (semgrep, bandit and pip-audit into `sec.sarif`) and `make audit` run in `Dockerfile.dev`; `requirements.txt`, `tests/requirements.txt` and `tests/Dockerfile` are gone.
- **CI checks every push to main and every pull request** with lint, unit tests and the security scan before the image is built, and a release tag no longer rebuilds the image: it waits for the main run of its commit and promotes the image that run built.
- The scheduled pull-back of issues from the Codeberg and GitLab mirrors runs at a spread-out time of day, and the Claude Code and Codex plugin manifests no longer use em dashes in their text.

## v0.5.8, 2026-10-10

Documentation only. The README's agent integrations section names [peen](https://github.com/psyb0t/peen) as an example of an agent that reads `.agents/skills/`.

## v0.5.7 — 2026-08-01

Infrastructure only. No code in this repo changed — every commit since v0.5.6 touches `.github/workflows/`.

- The pipeline was split: building and publishing stay in `pipeline.yml`, and everything that leaves the host now lives beside it in `mirror-and-archive.yml`.
- The repo is mirrored to Codeberg as well as GitLab.
- It is archived to the Wayback Machine, Software Heritage and archive.org.
- Issues opened on either mirror are copied back to GitHub every six hours, and closed here when the original closes.
- Pull requests are switched off on the mirrors: they are force-pushed from GitHub, so anything merged there would be destroyed by the next sync. Issues and forking stay enabled.

## v0.5.6 — 2026-07-27

- Fixed the "## Agent integrations" README section: the Codex subsection was missing its install command. It now includes `codex plugin add telethon-plus@psyb0t` right after the marketplace-add step.
- Clarified that the skill's invocation form differs by source: installed via the marketplace it's `$telethon-plus:telethon-plus`, while Codex's automatic pickup from a repo's own `.agents/skills/` (no install needed) uses plain `$telethon-plus`.

## v0.5.5 — 2026-07-27

- Added Claude Code (`.agents/.claude-plugin/plugin.json`) and Codex (`.agents/.codex-plugin/plugin.json`) plugin manifests so the existing skill installs natively in both clients.
- Added a "## Agent integrations" README section with copy-pasteable install commands for Claude Code, Codex, and OpenClaw (including the MCP-bridge plugin), and a matching Table of Contents entry.

## v0.5.4 — 2026-07-27

- Added a GitHub Actions CI status badge to the README.

## v0.5.3 — 2026-07-27

Add README status badges.

- Added self-hosted version and license badges (rendered as SVGs on the `badges` branch by the `create-badges` CI job, no third-party render service) plus a Docker Hub pulls badge. Wired a badges job into pipeline.yml.
