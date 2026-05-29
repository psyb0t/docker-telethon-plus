"""HTTP server: REST API, MCP streamable HTTP at /mcp, WS at /ws/updates.

Adds on top of the basic surface:
- /metrics (Prometheus exposition)
- /api/throttle/status (live rate-limit + cache state)
- /api/account/health (flood-risk tier)
- /api/entities/bulk (bulk_resolve)
- /api/messages/{id}/media (download_media)
- /api/messages/{id}/pin, /unpin
- /api/messages/{id}/reactions (set/remove)
- /api/chats/invite (join via t.me/+hash)
- /api/chats/{id}/admin (ban/unban/kick/promote/demote)
- /api/polls (create), /api/polls/{id}/vote, /api/polls/{id}/results
- /api/channels/{chat}/linked
- Read-only middleware (413-ish — 403 Forbidden on writes when TELETHON_READ_ONLY=true)
- X-Throttle-* response headers on every API call
"""

from __future__ import annotations

import inspect
import json
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ValidationError
from telethon.errors import RPCError

from app.client import TelethonHolder
from app.config import Config
from app.metrics import render as render_metrics
from app.tools import REGISTRY, ParamsModel, Tool
from app.updates import event_payload_json

log = logging.getLogger(__name__)

# HTTP methods that are considered "writes" for read-only mode enforcement.
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Paths that bypass read-only check (informational endpoints).
_READONLY_BYPASS_PATHS = {"/api/entities/bulk"}


def _model_to_signature(model: ParamsModel) -> inspect.Signature:
    _MISSING = object()
    params = []
    for name, field in model.model_fields.items():
        if field.is_required():
            default = inspect.Parameter.empty
        else:
            default = field.default if field.default is not _MISSING else inspect.Parameter.empty
        params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=field.annotation,
            )
        )
    return inspect.Signature(params)


def _validate_params(tool: Tool, raw: Any) -> BaseModel:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    try:
        return tool.params_model.model_validate(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()) from exc


async def _invoke(holder: TelethonHolder, tool: Tool, params: BaseModel) -> Any:
    holder.metrics.record_tool_call(tool.name)
    try:
        return await tool.handler(holder, params)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RPCError as exc:
        holder.metrics.record_tool_error(tool.name)
        log.warning("telegram RPC error in %s: %s", tool.name, exc)
        raise HTTPException(
            status_code=502,
            detail={"telegram_error": exc.__class__.__name__, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        holder.metrics.record_tool_error(tool.name)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _parse_body(request: Request) -> dict:
    body = await request.body()
    if not body:
        return {}
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    return raw


def build_mcp(holder: TelethonHolder, host: str, port: int) -> FastMCP:
    mcp = FastMCP(
        name="docker-telethon-plus",
        instructions=(
            "Telegram client tools backed by Telethon. "
            "All chat references accept usernames, phone numbers, t.me links, "
            "or numeric IDs as strings."
        ),
        host=host,
        port=port,
        streamable_http_path="/",
        stateless_http=True,
    )

    for tool in REGISTRY.values():
        _mount_mcp_tool(mcp, holder, tool)

    return mcp


def _mount_mcp_tool(mcp: FastMCP, holder: TelethonHolder, tool: Tool) -> None:
    params_model = tool.params_model

    async def _call(**kwargs: Any) -> Any:
        holder.metrics.record_tool_call(tool.name)
        try:
            params = params_model.model_validate(kwargs)
            return await tool.handler(holder, params)
        except PermissionError as exc:
            holder.metrics.record_tool_error(tool.name)
            return {"error": "read_only", "message": str(exc)}
        except RPCError as exc:
            holder.metrics.record_tool_error(tool.name)
            return {
                "error": "telegram_rpc",
                "type": exc.__class__.__name__,
                "message": str(exc),
            }
        except ValueError as exc:
            holder.metrics.record_tool_error(tool.name)
            return {"error": "invalid_argument", "message": str(exc)}

    _call.__name__ = tool.name
    _call.__doc__ = tool.description
    _call.__signature__ = _model_to_signature(params_model)
    _call.__annotations__ = {
        **{n: f.annotation for n, f in params_model.model_fields.items()},
        "return": Any,
    }
    mcp.tool(name=tool.name, description=tool.description)(_call)


def build_app(cfg: Config) -> FastAPI:
    holder = TelethonHolder(cfg)
    mcp = build_mcp(holder, cfg.listen_host, cfg.listen_port)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await holder.start()
        async with mcp.session_manager.run():
            try:
                yield
            finally:
                await holder.stop()

    app = FastAPI(
        title="docker-telethon-plus",
        description="HTTP + MCP front-end for the Telethon Telegram client.",
        version="1.1.0",
        lifespan=lifespan,
    )

    # ---- Middleware: auth, read-only, throttle headers -----------------

    if cfg.auth_key:
        @app.middleware("http")
        async def _auth(request: Request, call_next: Any) -> Response:
            if request.url.path in ("/healthz", "/metrics"):
                return await call_next(request)
            header = request.headers.get("Authorization", "")
            token = header.removeprefix("Bearer ").strip()
            if not secrets.compare_digest(token, cfg.auth_key):
                return Response(
                    content='{"detail":"unauthorized"}',
                    status_code=401,
                    media_type="application/json",
                )
            return await call_next(request)

    if cfg.read_only:
        @app.middleware("http")
        async def _readonly(request: Request, call_next: Any) -> Response:
            if (
                request.method in _WRITE_METHODS
                and request.url.path.startswith("/api/")
                and request.url.path not in _READONLY_BYPASS_PATHS
            ):
                return Response(
                    content='{"detail":"read-only mode: write operations are disabled"}',
                    status_code=403,
                    media_type="application/json",
                )
            return await call_next(request)

    @app.middleware("http")
    async def _throttle_headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        if request.url.path.startswith("/api/"):
            try:
                snap = holder.throttle.snapshot()
                response.headers["X-Throttle-Multiplier"] = f"{snap['multiplier']:.2f}"
                response.headers["X-Throttle-Flood-Events-1h"] = str(snap["flood_events_1h"])
                buckets = snap.get("buckets", {}) or {}
                for name, info in buckets.items():
                    remaining = max(0, info["limit"] - info["used"])
                    response.headers[f"X-RateLimit-Remaining-{name}"] = str(remaining)
            except Exception:  # noqa: BLE001 — never let header logic break the response
                log.debug("throttle header injection failed", exc_info=True)
        return response

    # ---- System endpoints ---------------------------------------------

    @app.get("/healthz")
    async def healthz() -> Dict[str, Any]:
        return {"status": "ok", "authorized": holder._client is not None}

    if cfg.metrics_enabled:
        @app.get("/metrics")
        async def metrics() -> PlainTextResponse:
            body = render_metrics(
                holder.metrics,
                holder.throttle.snapshot(),
                holder.cache.stats(),
            )
            return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

    @app.get("/api/throttle/status")
    async def throttle_status_route() -> JSONResponse:
        params = _validate_params(REGISTRY["throttle_status"], {})
        result = await _invoke(holder, REGISTRY["throttle_status"], params)
        return JSONResponse(content={"result": result})

    @app.get("/api/account/health")
    async def account_health_route() -> JSONResponse:
        params = _validate_params(REGISTRY["account_health"], {})
        result = await _invoke(holder, REGISTRY["account_health"], params)
        return JSONResponse(content={"result": result})

    # ---- Core message / chat endpoints (existing) ---------------------

    @app.get("/api/me")
    async def get_me() -> JSONResponse:
        params = _validate_params(REGISTRY["get_me"], {})
        result = await _invoke(holder, REGISTRY["get_me"], params)
        return JSONResponse(content={"result": result})

    @app.get("/api/entities")
    async def get_entity(request: Request) -> JSONResponse:
        raw = dict(request.query_params)
        params = _validate_params(REGISTRY["get_entity"], raw)
        result = await _invoke(holder, REGISTRY["get_entity"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/entities/bulk")
    async def bulk_resolve_route(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["bulk_resolve"], raw)
        result = await _invoke(holder, REGISTRY["bulk_resolve"], params)
        return JSONResponse(content={"result": result})

    @app.get("/api/dialogs")
    async def get_dialogs(request: Request) -> JSONResponse:
        raw = dict(request.query_params)
        params = _validate_params(REGISTRY["get_dialogs"], raw)
        result = await _invoke(holder, REGISTRY["get_dialogs"], params)
        return JSONResponse(content={"result": result})

    @app.get("/api/dialogs/search")
    async def search_dialogs_route(request: Request) -> JSONResponse:
        raw = dict(request.query_params)
        params = _validate_params(REGISTRY["search_dialogs"], raw)
        result = await _invoke(holder, REGISTRY["search_dialogs"], params)
        return JSONResponse(content={"result": result})

    @app.get("/api/messages")
    async def get_messages(request: Request) -> JSONResponse:
        raw = dict(request.query_params)
        params = _validate_params(REGISTRY["get_messages"], raw)
        result = await _invoke(holder, REGISTRY["get_messages"], params)
        return JSONResponse(content={"result": result})

    @app.get("/api/messages/{message_id}")
    async def get_message_route(message_id: int, request: Request) -> JSONResponse:
        raw = dict(request.query_params)
        raw["message_id"] = message_id
        params = _validate_params(REGISTRY["get_message"], raw)
        result = await _invoke(holder, REGISTRY["get_message"], params)
        return JSONResponse(content={"result": result})

    @app.get("/api/messages/{message_id}/media")
    async def download_media_route(message_id: int, request: Request) -> JSONResponse:
        raw = dict(request.query_params)
        raw["message_id"] = message_id
        params = _validate_params(REGISTRY["download_media"], raw)
        result = await _invoke(holder, REGISTRY["download_media"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/messages")
    async def send_message(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["send_message"], raw)
        result = await _invoke(holder, REGISTRY["send_message"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/messages/forward")
    async def forward_messages(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["forward_messages"], raw)
        result = await _invoke(holder, REGISTRY["forward_messages"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/messages/read")
    async def mark_read(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["mark_read"], raw)
        result = await _invoke(holder, REGISTRY["mark_read"], params)
        return JSONResponse(content={"result": result})

    @app.patch("/api/messages/{message_id}")
    async def edit_message(message_id: int, request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        raw["message_id"] = message_id
        params = _validate_params(REGISTRY["edit_message"], raw)
        result = await _invoke(holder, REGISTRY["edit_message"], params)
        return JSONResponse(content={"result": result})

    @app.delete("/api/messages")
    async def delete_messages(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["delete_messages"], raw)
        result = await _invoke(holder, REGISTRY["delete_messages"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/messages/{message_id}/pin")
    async def pin_message_route(message_id: int, request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        raw["message_id"] = message_id
        params = _validate_params(REGISTRY["pin_message"], raw)
        result = await _invoke(holder, REGISTRY["pin_message"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/messages/{message_id}/unpin")
    async def unpin_message_route(message_id: int, request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        raw["message_id"] = message_id
        params = _validate_params(REGISTRY["unpin_message"], raw)
        result = await _invoke(holder, REGISTRY["unpin_message"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/messages/{message_id}/reactions")
    async def set_reaction_route(message_id: int, request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        raw["message_id"] = message_id
        params = _validate_params(REGISTRY["set_reaction"], raw)
        result = await _invoke(holder, REGISTRY["set_reaction"], params)
        return JSONResponse(content={"result": result})

    @app.delete("/api/messages/{message_id}/reactions")
    async def remove_reaction_route(message_id: int, request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        raw["message_id"] = message_id
        params = _validate_params(REGISTRY["remove_reaction"], raw)
        result = await _invoke(holder, REGISTRY["remove_reaction"], params)
        return JSONResponse(content={"result": result})

    # ---- Files --------------------------------------------------------

    @app.post("/api/files")
    async def send_file(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["send_file"], raw)
        result = await _invoke(holder, REGISTRY["send_file"], params)
        return JSONResponse(content={"result": result})

    # ---- Participants / chats -----------------------------------------

    @app.get("/api/participants")
    async def get_participants(request: Request) -> JSONResponse:
        raw = dict(request.query_params)
        params = _validate_params(REGISTRY["get_participants"], raw)
        result = await _invoke(holder, REGISTRY["get_participants"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/chats")
    async def create_group(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["create_group"], raw)
        result = await _invoke(holder, REGISTRY["create_group"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/chats/join")
    async def join_chat(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["join_chat"], raw)
        result = await _invoke(holder, REGISTRY["join_chat"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/chats/invite")
    async def join_via_invite_route(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["join_via_invite"], raw)
        result = await _invoke(holder, REGISTRY["join_via_invite"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/chats/leave")
    async def leave_chat(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["leave_chat"], raw)
        result = await _invoke(holder, REGISTRY["leave_chat"], params)
        return JSONResponse(content={"result": result})

    @app.delete("/api/chats")
    async def delete_chat(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["delete_chat"], raw)
        result = await _invoke(holder, REGISTRY["delete_chat"], params)
        return JSONResponse(content={"result": result})

    @app.get("/api/channels/linked")
    async def get_linked_chat_route(request: Request) -> JSONResponse:
        raw = dict(request.query_params)
        params = _validate_params(REGISTRY["get_linked_chat"], raw)
        result = await _invoke(holder, REGISTRY["get_linked_chat"], params)
        return JSONResponse(content={"result": result})

    # ---- Channel admin actions ----------------------------------------

    @app.post("/api/admin/ban")
    async def ban_user_route(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["ban_user"], raw)
        result = await _invoke(holder, REGISTRY["ban_user"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/admin/unban")
    async def unban_user_route(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["unban_user"], raw)
        result = await _invoke(holder, REGISTRY["unban_user"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/admin/kick")
    async def kick_user_route(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["kick_user"], raw)
        result = await _invoke(holder, REGISTRY["kick_user"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/admin/promote")
    async def promote_user_route(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["promote_user"], raw)
        result = await _invoke(holder, REGISTRY["promote_user"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/admin/demote")
    async def demote_user_route(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["demote_user"], raw)
        result = await _invoke(holder, REGISTRY["demote_user"], params)
        return JSONResponse(content={"result": result})

    # ---- Polls --------------------------------------------------------

    @app.post("/api/polls")
    async def create_poll_route(request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        params = _validate_params(REGISTRY["create_poll"], raw)
        result = await _invoke(holder, REGISTRY["create_poll"], params)
        return JSONResponse(content={"result": result})

    @app.post("/api/polls/{message_id}/vote")
    async def vote_poll_route(message_id: int, request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        raw["message_id"] = message_id
        params = _validate_params(REGISTRY["vote_poll"], raw)
        result = await _invoke(holder, REGISTRY["vote_poll"], params)
        return JSONResponse(content={"result": result})

    @app.get("/api/polls/{message_id}/results")
    async def poll_results_route(message_id: int, request: Request) -> JSONResponse:
        raw = dict(request.query_params)
        raw["message_id"] = message_id
        params = _validate_params(REGISTRY["get_poll_results"], raw)
        result = await _invoke(holder, REGISTRY["get_poll_results"], params)
        return JSONResponse(content={"result": result})

    # ---- WebSocket updates --------------------------------------------

    if cfg.updates_enabled:
        @app.websocket("/ws/updates")
        async def ws_updates(ws: WebSocket) -> None:
            # If auth_key is set, require the same Bearer token via query string
            # or first text frame (browsers can't set Authorization on WS upgrade).
            if cfg.auth_key:
                token = ws.query_params.get("token", "")
                if not secrets.compare_digest(token, cfg.auth_key):
                    await ws.close(code=4401)
                    return
            await ws.accept()
            queue = holder.updates.subscribe()
            try:
                while True:
                    payload = await queue.get()
                    await ws.send_text(event_payload_json(payload))
            except WebSocketDisconnect:
                pass
            finally:
                holder.updates.unsubscribe(queue)

    app.mount("/mcp", mcp.streamable_http_app())

    return app
