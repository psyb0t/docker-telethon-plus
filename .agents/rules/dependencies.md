# Dependencies

- Runtime and dev dependencies are pinned exactly in `pyproject.toml` and locked with hashes in `uv.lock`. Change them only through `make pkg-add`, `make pkg-update`, `make pkg-remove` or `make pkg-upgrade`, which first move `[tool.uv] exclude-newer` to seven days ago with `scripts/bump_exclude_newer.sh`. Never edit `exclude-newer` or `uv.lock` by hand.
- After a dependency change run `make dev-image`, `make lint`, `make test-unit` and `make audit`.
- Both Dockerfiles install with `uv sync --frozen`, so a lock that does not match `pyproject.toml` fails the build.
- Base images and the copied `uv` binary appear in both `Dockerfile` and `Dockerfile.dev`. Update tag and digest together, in both files.
- `mcp` stays on v1: v2 removes `mcp.server.fastmcp`, which `app/server.py` builds on.
- A new runtime dependency must be compatible with GPL-2.0-or-later.
