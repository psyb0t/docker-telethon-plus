"""HTTP server: REST API, MCP streamable HTTP at /mcp, WS at /ws/updates.

Response conventions
--------------------
- 2xx returns the resource directly. No `{"result": ...}` wrapper. Lists
  are JSON arrays. Singles are JSON objects.
- 4xx/5xx returns `{"detail": ...}` (FastAPI standard).

Route conventions
-----------------
- Chat references live in path params where possible (`/api/chats/{chat}/...`).
- For GETs that aren't chat-scoped, params go in the query string.
- For mutations, params go in the JSON body.
- DELETE keeps bodies for multi-id / multi-field bulk operations (Telegram's
  delete shapes don't fit query strings cleanly).

Routes
------
- /metrics, /healthz, /api/throttle/status, /api/account/health
- /api/me, /api/entities, /api/entities/bulk
- /api/dialogs                  (optional ?search=)
- /api/messages                 (GET list, POST send-or-file, DELETE bulk)
- /api/messages/{id}            (GET, PATCH, sub-routes for /pin, /unpin, /reactions, /media)
- /api/messages/forward         (POST)
- /api/messages/read            (POST)
- /api/files                    (REMOVED — POST /api/messages with file_url)
- /api/participants
- /api/chats                    (POST create, DELETE delete)
- /api/chats/join, /api/chats/leave, /api/chats/invite
- /api/chats/{chat}/linked
- /api/chats/{chat}/admin/{action}    ({ban,unban,kick,promote,demote})
- /api/polls                    (POST create)
- /api/polls/{id}/vote, /api/polls/{id}/results
- /ws/updates
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
from app.tools import REGISTRY, ParamsModel, Tool, download_media_bytes
from app.updates import event_payload_json

log = logging.getLogger(__name__)

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Paths that bypass the read-only check (informational endpoints that
# happen to use POST for body-carrying).
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


async def _run(holder: TelethonHolder, tool_name: str, raw: dict) -> Any:
    tool = REGISTRY[tool_name]
    params = _validate_params(tool, raw)
    return await _invoke(holder, tool, params)


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
        version="1.2.0",
        lifespan=lifespan,
    )

    # ---- Middleware ----------------------------------------------------

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
            except Exception:  # noqa: BLE001
                log.debug("throttle header injection failed", exc_info=True)
        return response

    # ---- System --------------------------------------------------------

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
        return JSONResponse(content=await _run(holder, "throttle_status", {}))

    @app.get("/api/account/health")
    async def account_health_route() -> JSONResponse:
        return JSONResponse(content=await _run(holder, "account_health", {}))

    # ---- Identity / entity resolution ---------------------------------

    @app.get("/api/me")
    async def get_me() -> JSONResponse:
        return JSONResponse(content=await _run(holder, "get_me", {}))

    @app.get("/api/entities")
    async def get_entity(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "get_entity", dict(request.query_params)))

    @app.post("/api/entities/bulk")
    async def bulk_resolve_route(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "bulk_resolve", await _parse_body(request)))

    # ---- Dialogs -------------------------------------------------------

    @app.get("/api/dialogs")
    async def get_dialogs(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "get_dialogs", dict(request.query_params)))

    # ---- Messages ------------------------------------------------------

    @app.get("/api/messages")
    async def get_messages(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "get_messages", dict(request.query_params)))

    @app.post("/api/messages")
    async def send_message_or_file(request: Request) -> JSONResponse:
        """One endpoint, two flavors: body has `file_url` → send file; else
        plain text via `text`. Dispatches to the matching tool."""
        raw = await _parse_body(request)
        tool_name = "send_file" if raw.get("file_url") else "send_message"
        return JSONResponse(content=await _run(holder, tool_name, raw))

    @app.post("/api/messages/forward")
    async def forward_messages(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "forward_messages", await _parse_body(request)))

    @app.post("/api/messages/read")
    async def mark_read(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "mark_read", await _parse_body(request)))

    @app.delete("/api/messages")
    async def delete_messages(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "delete_messages", await _parse_body(request)))

    @app.get("/api/messages/{message_id}")
    async def get_message_route(message_id: int, request: Request) -> JSONResponse:
        raw = dict(request.query_params)
        raw["message_id"] = message_id
        return JSONResponse(content=await _run(holder, "get_message", raw))

    @app.patch("/api/messages/{message_id}")
    async def edit_message(message_id: int, request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        raw["message_id"] = message_id
        return JSONResponse(content=await _run(holder, "edit_message", raw))

    @app.get("/api/messages/{message_id}/media")
    async def download_media_route(message_id: int, request: Request) -> Response:
        """Stream the file as raw bytes with proper Content-Type +
        Content-Disposition. For base64 use the `download_media` MCP tool."""
        chat = request.query_params.get("chat", "")
        if not chat:
            raise HTTPException(status_code=400, detail="`chat` query param required")
        try:
            max_bytes = int(request.query_params.get("max_bytes", 50 * 1024 * 1024))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid max_bytes: {exc}") from exc
        holder.metrics.record_tool_call("download_media")
        try:
            info = await download_media_bytes(holder, chat, message_id, max_bytes)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except RPCError as exc:
            holder.metrics.record_tool_error("download_media")
            raise HTTPException(
                status_code=502,
                detail={"telegram_error": exc.__class__.__name__, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            holder.metrics.record_tool_error("download_media")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(
            content=info["data"],
            media_type=info["mime_type"],
            headers={
                "Content-Disposition": f'attachment; filename="{info["filename"]}"',
                "X-Media-Type": info["media_type"],
                "Content-Length": str(info["size"]),
            },
        )

    @app.post("/api/messages/{message_id}/pin")
    async def pin_message_route(message_id: int, request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        raw["message_id"] = message_id
        return JSONResponse(content=await _run(holder, "pin_message", raw))

    @app.post("/api/messages/{message_id}/unpin")
    async def unpin_message_route(message_id: int, request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        raw["message_id"] = message_id
        return JSONResponse(content=await _run(holder, "unpin_message", raw))

    @app.post("/api/messages/{message_id}/reactions")
    async def set_reaction_route(message_id: int, request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        raw["message_id"] = message_id
        return JSONResponse(content=await _run(holder, "set_reaction", raw))

    @app.delete("/api/messages/{message_id}/reactions")
    async def remove_reaction_route(message_id: int, request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        raw["message_id"] = message_id
        return JSONResponse(content=await _run(holder, "remove_reaction", raw))

    # ---- Participants --------------------------------------------------

    @app.get("/api/participants")
    async def get_participants(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "get_participants", dict(request.query_params)))

    # ---- Chats ---------------------------------------------------------

    @app.post("/api/chats")
    async def create_group(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "create_group", await _parse_body(request)))

    @app.delete("/api/chats")
    async def delete_chat(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "delete_chat", await _parse_body(request)))

    @app.post("/api/chats/join")
    async def join_chat(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "join_chat", await _parse_body(request)))

    @app.post("/api/chats/leave")
    async def leave_chat(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "leave_chat", await _parse_body(request)))

    @app.post("/api/chats/invite")
    async def join_via_invite_route(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "join_via_invite", await _parse_body(request)))

    @app.get("/api/chats/{chat}/linked")
    async def get_linked_chat_route(chat: str) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "get_linked_chat", {"chat": chat}))

    # ---- Chat admin actions (nested under the chat) -------------------

    _ADMIN_ACTIONS = {
        "ban": "ban_user",
        "unban": "unban_user",
        "kick": "kick_user",
        "promote": "promote_user",
        "demote": "demote_user",
    }

    @app.post("/api/chats/{chat}/admin/{action}")
    async def chat_admin_route(chat: str, action: str, request: Request) -> JSONResponse:
        tool_name = _ADMIN_ACTIONS.get(action)
        if tool_name is None:
            raise HTTPException(
                status_code=404,
                detail=f"unknown admin action {action!r}; valid: {sorted(_ADMIN_ACTIONS)}",
            )
        raw = await _parse_body(request)
        raw["chat"] = chat
        return JSONResponse(content=await _run(holder, tool_name, raw))

    # ---- Polls ---------------------------------------------------------

    @app.post("/api/polls")
    async def create_poll_route(request: Request) -> JSONResponse:
        return JSONResponse(content=await _run(holder, "create_poll", await _parse_body(request)))

    @app.post("/api/polls/{message_id}/vote")
    async def vote_poll_route(message_id: int, request: Request) -> JSONResponse:
        raw = await _parse_body(request)
        raw["message_id"] = message_id
        return JSONResponse(content=await _run(holder, "vote_poll", raw))

    @app.get("/api/polls/{message_id}/results")
    async def poll_results_route(message_id: int, request: Request) -> JSONResponse:
        raw = dict(request.query_params)
        raw["message_id"] = message_id
        return JSONResponse(content=await _run(holder, "get_poll_results", raw))

    # ---- WebSocket updates --------------------------------------------

    if cfg.updates_enabled:
        @app.websocket("/ws/updates")
        async def ws_updates(ws: WebSocket) -> None:
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
