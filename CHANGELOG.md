# Changelog

All notable changes per release. Versions follow [semver](https://semver.org).

## v0.7.2, 2026-11-08

### Fixed

- **A numeric chat ID stopped working after a restart.** The IDs the API returns, such as the `id` of a group from `create_group`, resolved only while Telethon still remembered the chat in memory. After a restart a bare channel ID was read as a user and failed with `Could not find the input entity`: numeric IDs never consulted the entity cache, and `create_group` did not cache the chat it made. They now resolve from the cache, then through Telethon, then by a search of the account's dialogs, and `create_group` caches the chat it creates. See [docs/tools.md](https://psyb0t.github.io/docker-telethon-plus/tools/#chat-references).
- **`get_poll_results` showed old counts right after a vote.** It read the counts stored with the poll message, which Telegram updates seconds later, so a vote just made could read as 0 voters. It now asks Telegram for the current results, which costs one more `read` slot.
- **MCP refusals looked like successful calls.** A `read_only`, `flood_wait`, `telegram_rpc` or `invalid_argument` answer came back as a plain result, so an agent took the call as done. It now comes back as an error result (`isError: true`) with the same JSON text. See [docs/mcp.md](https://psyb0t.github.io/docker-telethon-plus/mcp/#tool-results-and-errors).

### Changed

- **The documentation says that the server's own actions raise no event.** A message telethon-plus sends, edits or deletes is not echoed on `/ws/updates` or the webhook. See [docs/updates.md](https://psyb0t.github.io/docker-telethon-plus/updates/).
- **`make test` covers every tool on the real account,** with optional settings in `tests/.env` for chats outside the suite. See [docs/development.md](https://psyb0t.github.io/docker-telethon-plus/development/).

## v0.7.1, 2026-11-07

The first published release with the v0.7.0 changes below. v0.7.0 was tagged, but its CI run failed, so no `v0.7.0` image, GitHub release or registry entry exists.

### Fixed

- **A `/ws/updates` connection could end with a stray `CancelledError`.** When the server cancelled the route while it was cleaning up after a disconnect, the route raised the cancellation of its own helper task, which the server did not recognise as its own. The route now waits for its helpers without passing their cancellation on.

## v0.7.0, 2026-11-07

### Fixed

- **Flood protection let bursts through.** Concurrent requests all passed a rate-limit bucket together, because the slot was recorded only after the wait, so 10 username lookups started together all went out at once, despite the 5-a-minute cap. Requests that queued behind a long call also went out back to back, ignoring the per-chat gap. Slots are now booked before waiting, and the gap is checked again at the moment the request is sent. A send waiting for its own chat's gap no longer holds up calls to other chats.
- **The entity cache did nothing after a restart.** It kept only IDs, and the session string does not carry Telegram's access hashes, so every lookup after a restart went back to a username resolution. The cache now stores the access hash and uses it directly, and clears itself when the session belongs to a different account.
- **A `FLOOD_WAIT` on a cached lookup was swallowed** and followed straight away by a username resolution, the call most likely to get the account locked out. Only a "not found" falls back now; any other error is returned.
- **Floods Telethon sleeps through were invisible.** A `FLOOD_WAIT` up to `TELETHON_FLOOD_SLEEP_THRESHOLD` never reached the adaptive backoff or `/metrics`. It now counts in both, under the bucket `telethon_sleep`.
- **Every incoming event said `"type": "Event"`.** Telethon names all its event classes `Event`, so the payload never said `NewMessage`, `MessageEdited`, `MessageDeleted` or `ChatAction`, as documented, and a client filtering on those matched nothing. Each event now carries its real type.
- **A timed `ban_user` was permanent.** `until_seconds` reached Telegram as a timestamp in 1970, which Telegram reads as forever. A ban now ends `until_seconds` from now.
- **`create_poll` failed on every call**, `get_poll_results` answered 500 on every poll, and `vote_poll` sent the wrong answer, because they did not match Telegram's current poll types. All three now build what Telegram expects, and `get_poll_results` reports each answer's real index. A quiz without `correct_option`, a quiz with `multiple_choice`, `correct_option` or `solution` on a normal poll, an empty option and an index outside the poll are refused with a 400.
- **`promote_user` with no right set demoted the user,** because Telegram reads an empty set of rights as "not an admin". It is refused with a 400 now. `promote_user` and `demote_user` also refuse a `user` that is a channel or group.
- **One message replying to a story broke `get_messages` and `get_dialogs`** with a 500 for the whole page. Forwarded messages also reported `fwd_from.from_id` as text such as `"PeerUser(user_id=...)"` or `"None"`; it is the numeric ID or `null` now.
- **`send_file` sent every file as a document named `upload-xxxx`.** Photos now go out as photos, videos as playable videos with their real width, height and duration, audio files with their duration, title and artist, and documents keep the name from the URL. A failed download, a redirect away from `http(s)` or an empty file answers 400 instead of 500, and nothing is sent.
- **`download_media` downloaded a whole file before refusing it for `max_bytes`,** and answered success with 0 bytes for media with nothing to download, such as a location. The size is checked first now, and empty media answers 400. A non-ASCII file name no longer breaks the response with a 500, and the sender's MIME type is only passed on when it is a plain `type/subtype`.
- **Channel-only tools answered 500 for basic groups and users.** `join_chat`, `delete_chat`, `get_linked_chat` and the moderation tools answer 400 with what they need, and `leave_chat` now also leaves basic groups.
- **Rate-limit accounting undercounted** file transfers (one slot for a transfer of hundreds of parts), member lists (which can send an extra request) and `kick_user` (two requests). Each now charges what it sends.
- **`create_group` with `megagroup: false` did not ask for a broadcast channel.** It does now. Channel titles are capped at Telegram's 128 characters and poll options at 100.
- **Bad settings crashed or misbehaved instead of stopping cleanly.** A bucket limit of 0 made every matching call fail with 500, a buffer size of 0 removed the slow-client limit, `TELETHON_REQUEST_TIMEOUT=60s` printed a traceback, and `TELETHON_LOG_LEVEL` accepted any name from Python's `logging` module, some of which broke startup, while a typo silently meant `INFO`. Bad values now stop startup with `config error: <NAME> must be ...`.
- **A non-ASCII `TELETHON_AUTH_KEY` or token answered 500,** and a cache file with invalid UTF-8 stopped the server from starting. Both work now.
- **`TELETHON_LOG_JSON` did nothing.** It now logs one JSON object per line, uvicorn's lines included.
- **The webhook dropped events silently on a 3xx or 4xx answer.** Every non-2xx answer is logged now.
- **The `login` command wrote the session file readable by everyone.** It is created with mode `0600`.
- **Tool error counts in `/metrics` missed** read-only refusals over REST and unexpected exceptions on both REST and MCP. Every failure counts now.
- **A 401 answer carried the throttle headers,** showing rate-limit state to callers without the key. It no longer does.

### Changed

- **The license is now GPL-2.0-or-later, instead of WTFPL.** The image now includes [hachoir](https://github.com/vstinner/hachoir), which Telethon uses to read video and audio metadata. hachoir is GPL v2 only, so the image as a whole is distributed under GPL v2, and anyone who redistributes it, modified or not, has to follow the GPL. The license text ships in the image as `/app/LICENSE`. hachoir's warnings about a file's format are logged at `DEBUG` under the `hachoir` logger.
- **A `FLOOD_WAIT` too long to sleep through answers `429`** with a `Retry-After` header and `seconds` in the body, instead of `502`. Over MCP it is `{"error": "flood_wait", "seconds": ...}`. Other Telegram refusals stay `502` / `telegram_rpc`.
- **The documentation moved to a site,** <https://psyb0t.github.io/docker-telethon-plus/>, built from `docs/`. The README keeps the quick start and links to every page. `make docs` builds the site locally.
- **`make test-unit` fails below 93% coverage,** and with it CI. The suite grew from 65 to 472 tests and covers every REST route and tool.

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
