# Docker image

- `Dockerfile` has a builder stage that runs `uv sync --frozen --no-dev` into `/opt/venv` and a runtime stage that copies only `/opt/venv`, `app/`, `login.py` and `LICENSE`. GPL v2 requires `LICENSE` in the image; keep that copy.
- The entrypoint is `tini -- python -m app`; `CMD` is empty for the server and `login` for the login flow.
- The image runs as `telethon-plus` (uid 1000). `/cache` is a volume owned by that user at mode 700 and holds the entity cache. `/session` is a world-writable empty file so `make login` can bind-mount the session output there.
- `TELETHON_HTTP_LISTEN_ADDRESS` defaults to `0.0.0.0:8080` in the image; the `HEALTHCHECK` reads its port from that variable and calls `/healthz`. Keep the two in step.
- The `io.modelcontextprotocol.server.name` label must match `name` in `server.json`, or the MCP Registry refuses the entry.
- Keep `.dockerignore` excluding tests, docs, scripts, the dev Dockerfile, `.env` files and scratch output; the build context needs only what the `COPY` lines name.
