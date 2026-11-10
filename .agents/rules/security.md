# Security

- The session string is the account. `TELETHON_SESSION`, `TELETHON_AUTH_KEY`, `TELETHON_API_HASH` and proxy credentials never appear in logs, responses, test output, fixtures, docs or commits. Read `.env.example`, never `.env` or `tests/.env`.
- Auth middleware in `app/server.py` compares the bearer token in constant time (`_token_matches`) and leaves only `/healthz` and `/metrics` public. It is added last so it runs first and a 401 carries no throttle headers. Keep both properties.
- `/ws/updates` accepts the key as `?token=`. `RedactQueryToken` in `app/__main__.py` removes it from uvicorn's log lines; a new log path that can carry a URL needs the same treatment.
- `send_file` fetches a caller-supplied URL from the server. Keep its scheme check (`http`/`https`), the size cap and the timeout, and stage downloads in a temporary file that is always removed.
- File names that reach a header or the disk go through the existing sanitizers (`_content_disposition`, the control-character and path-separator checks in `app/tools.py`).
- The image runs as uid 1000 with `/cache` at mode 700. Do not add root steps after `USER telethon-plus` in the `Dockerfile`.
- `make sec` reports semgrep, bandit and pip-audit findings to the Security tab and never fails. `make audit` fails on a vulnerable locked runtime dependency.
- Security-relevant behaviour changes update `docs/security.md` in the same change.
