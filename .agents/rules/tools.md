# Tools, routes and the MCP surface

- A tool is a pydantic params model with `model_config = ConfigDict(extra="forbid")` and an async handler `(holder, params)`, registered with `_register(Tool(...))` in `app/tools.py`. `_register` raises on a duplicate name. Registering it is enough for MCP: `build_mcp()` in `app/server.py` mounts every `REGISTRY` entry.
- Put Telegram's server-side limits on the params model (`Field(max_length=...)` and the `_MAX_*` constants), so an oversized value is a validation error and not an RPC failure.
- A tool that changes anything calls `holder.assert_writeable()` first and returns `{"dry_run": True, ...}` without calling Telegram when `holder.dry_run` is set.
- A REST route goes in `build_app()` in `app/server.py`, following its conventions: chat in the path where possible, query string for non-chat GETs, JSON body for mutations, the resource returned bare and errors as `{"detail": ...}`. Run the tool through `_run()` so validation, error mapping and metrics stay the same as every other route. A write route that must stay open in read-only mode goes in `_READONLY_BYPASS_PATHS`.
- Map new exception types in both `_http_error` and `_mcp_error`, so REST and MCP answer the same way.
- A new tool must be added to `ROUTES` in `tests/unit/test_routes.py` and, if it writes, to `WRITE_TOOLS` in `tests/unit/test_tools_registry.py`. Both tests walk the whole registry and fail otherwise.
- Document the tool in `docs/tools.md` (a `###` section named after the tool) and its route in the table in `docs/http-api.md`. Update the tool count in `.agents/plugins/telethon-plus/openclaw.plugin.json` and the skill's tool tables in `.agents/skills/telethon-plus/SKILL.md`.
