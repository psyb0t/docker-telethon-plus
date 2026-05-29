"""Tool definitions exposed via REST and MCP.

Each tool is a coroutine `(holder, params) -> dict | list`. Pydantic models
describe the input schema. The same registry drives the REST routes under
`/api/` and the MCP tools at `/mcp`.

Every Telegram call goes through `holder.guard(bucket, chat_key)` which
applies per-method rate limits, per-chat send intervals, global jitter,
and adaptive backoff on FLOOD_WAIT. Entity lookups go through
`holder.resolve_entity()` which prefers the persistent cache over fresh
resolveUsername calls.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

import base64
import datetime as _dt
import io

import httpx
from pydantic import BaseModel, ConfigDict, Field
from telethon.tl.custom.message import Message
from telethon.tl.functions.channels import (
    CreateChannelRequest,
    DeleteChannelRequest,
    EditAdminRequest,
    EditBannedRequest,
    GetFullChannelRequest,
    JoinChannelRequest,
    LeaveChannelRequest,
)
from telethon.tl.functions.messages import (
    GetMessagesReactionsRequest,
    ImportChatInviteRequest,
    SendReactionRequest,
    SendVoteRequest,
    UpdatePinnedMessageRequest,
)
from telethon.tl.types import (
    ChatAdminRights,
    ChatBannedRights,
    InputMediaPoll,
    Poll,
    PollAnswer,
    ReactionEmoji,
    User,
)

from app.client import TelethonHolder

ParamsModel = type[BaseModel]
Handler = Callable[[TelethonHolder, BaseModel], Awaitable[Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params_model: ParamsModel
    handler: Handler


REGISTRY: Dict[str, Tool] = {}


def _register(tool: Tool) -> Tool:
    if tool.name in REGISTRY:
        raise RuntimeError(f"duplicate tool: {tool.name}")
    REGISTRY[tool.name] = tool
    return tool


def list_tools() -> List[Tool]:
    return list(REGISTRY.values())


def _entity_to_dict(entity: Any) -> Dict[str, Any]:
    if entity is None:
        return {}
    out: Dict[str, Any] = {
        "id": getattr(entity, "id", None),
        "type": type(entity).__name__,
    }
    for attr in ("username", "first_name", "last_name", "title", "phone", "bot"):
        value = getattr(entity, attr, None)
        if value is not None:
            out[attr] = value
    return out


def _message_to_dict(msg: Message) -> Dict[str, Any]:
    fwd: Optional[Dict[str, Any]] = None
    fwd_from = getattr(msg, "fwd_from", None)
    if fwd_from is not None:
        fwd = {
            "date": fwd_from.date.isoformat() if getattr(fwd_from, "date", None) else None,
            "from_id": str(getattr(fwd_from, "from_id", "")) or None,
            "from_name": getattr(fwd_from, "from_name", None),
            "channel_post": getattr(fwd_from, "channel_post", None),
            "post_author": getattr(fwd_from, "post_author", None),
        }
    return {
        "id": msg.id,
        "date": msg.date.isoformat() if msg.date else None,
        "chat_id": getattr(msg.peer_id, "user_id", None)
        or getattr(msg.peer_id, "channel_id", None)
        or getattr(msg.peer_id, "chat_id", None),
        "sender_id": msg.sender_id,
        "text": msg.message or "",
        "out": msg.out,
        "reply_to_msg_id": msg.reply_to.reply_to_msg_id if msg.reply_to else None,
        "media": msg.media is not None,
        "media_type": type(msg.media).__name__ if msg.media else None,
        "fwd_from": fwd,
        "views": getattr(msg, "views", None),
        "forwards": getattr(msg, "forwards", None),
    }


def _chat_key(chat: str) -> str:
    """Normalize a chat reference for per-chat throttling bookkeeping."""
    s = chat.strip().lower()
    if s.startswith("@"):
        s = s[1:]
    return s


# ---------------------------------------------------------------------------
# get_me
# ---------------------------------------------------------------------------


class GetMeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _get_me(holder: TelethonHolder, _: GetMeParams) -> Dict[str, Any]:
    async with holder.guard("read") as client:
        me: User = await client.get_me()
    return _entity_to_dict(me)


_register(
    Tool(
        name="get_me",
        description="Return the authorized account profile.",
        params_model=GetMeParams,
        handler=_get_me,
    )
)


# ---------------------------------------------------------------------------
# get_entity
# ---------------------------------------------------------------------------


class GetEntityParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str = Field(
        ..., description="Username, phone, t.me link, or numeric ID as string."
    )


async def _get_entity(
    holder: TelethonHolder, params: GetEntityParams
) -> Dict[str, Any]:
    entity = await holder.resolve_entity(params.chat)
    return _entity_to_dict(entity)


_register(
    Tool(
        name="get_entity",
        description="Resolve a chat reference (username, phone, link, or ID) to a profile.",
        params_model=GetEntityParams,
        handler=_get_entity,
    )
)


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


class SendMessageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str = Field(..., description="Target chat (username/ID/phone).")
    text: str = Field(..., min_length=1, max_length=4096)
    parse_mode: Optional[str] = Field(
        None, description="One of: md, markdown, html, or null for plain text."
    )
    reply_to: Optional[int] = Field(
        None, description="Message ID to reply to."
    )
    silent: bool = Field(False, description="Send without notification.")
    link_preview: bool = Field(True, description="Allow link previews.")
    schedule: Optional[_dt.datetime] = Field(
        None,
        description=(
            "Optional UTC datetime to schedule the send. ISO 8601 string; "
            "must be in the future."
        ),
    )


async def _send_message(
    holder: TelethonHolder, params: SendMessageParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_send": params.model_dump(mode="json")}
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        msg = await client.send_message(
            entity=entity,
            message=params.text,
            parse_mode=params.parse_mode,
            reply_to=params.reply_to,
            silent=params.silent,
            link_preview=params.link_preview,
            schedule=params.schedule,
        )
    return _message_to_dict(msg)


_register(
    Tool(
        name="send_message",
        description="Send a text message to a chat.",
        params_model=SendMessageParams,
        handler=_send_message,
    )
)


# ---------------------------------------------------------------------------
# get_messages
# ---------------------------------------------------------------------------


class GetMessagesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    limit: int = Field(20, ge=1, le=200)
    offset_id: int = Field(0, ge=0)
    search: Optional[str] = None


async def _get_messages(
    holder: TelethonHolder, params: GetMessagesParams
) -> List[Dict[str, Any]]:
    """Manual pagination: each underlying server hit (≤100 msgs) consumes one
    read-bucket slot and respects the per-chat read interval. Avoids the
    bucket under-counting that bare `iter_messages` would cause."""
    entity = await holder.resolve_entity(params.chat)
    PAGE = 100  # Telegram caps messages.getHistory at 100 per call
    chat_key = _chat_key(params.chat)

    result: List[Dict[str, Any]] = []
    cur_offset = params.offset_id
    remaining = params.limit
    while remaining > 0:
        page_size = min(PAGE, remaining)
        async with holder.guard("read", chat_key=chat_key, chat_kind="read") as client:
            page = await client.get_messages(
                entity,
                limit=page_size,
                offset_id=cur_offset,
                search=params.search,
            )
        if not page:
            break
        for msg in page:
            if msg is None:
                continue
            result.append(_message_to_dict(msg))
        last_id = page[-1].id
        if last_id == cur_offset or len(page) < page_size:
            # Telegram returned fewer than asked, or we're stuck — no more.
            break
        cur_offset = last_id
        remaining -= len(page)
    return result


_register(
    Tool(
        name="get_messages",
        description="Read recent messages from a chat (newest first).",
        params_model=GetMessagesParams,
        handler=_get_messages,
    )
)


# ---------------------------------------------------------------------------
# get_dialogs
# ---------------------------------------------------------------------------


class GetDialogsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(20, ge=1, le=200)
    archived: bool = False


async def _get_dialogs(
    holder: TelethonHolder, params: GetDialogsParams
) -> List[Dict[str, Any]]:
    DIALOGS_PAGE = 100  # Telegram's messages.getDialogs page size
    async with holder.guard("read") as client:
        dialogs = await client.get_dialogs(
            limit=params.limit, archived=params.archived
        )
    # iter_dialogs paginated internally; charge extra slots for hidden hits.
    extra_pages = max(0, (len(dialogs) - 1) // DIALOGS_PAGE)
    holder.throttle.record_extra("read", extra_pages)
    out: List[Dict[str, Any]] = []
    for dialog in dialogs:
        item = _entity_to_dict(dialog.entity)
        item["unread_count"] = dialog.unread_count
        item["pinned"] = dialog.pinned
        if dialog.message is not None:
            item["last_message"] = _message_to_dict(dialog.message)
        # Opportunistically populate the entity cache — dialogs deliver fully
        # hydrated entities, so future lookups skip resolveUsername entirely.
        if dialog.entity is not None:
            holder.cache.put(str(getattr(dialog.entity, "id", "")), dialog.entity)
            uname = getattr(dialog.entity, "username", None)
            if uname:
                holder.cache.put(uname, dialog.entity)
        out.append(item)
    return out


_register(
    Tool(
        name="get_dialogs",
        description="List your dialogs (chats, groups, channels).",
        params_model=GetDialogsParams,
        handler=_get_dialogs,
    )
)


# ---------------------------------------------------------------------------
# forward_messages
# ---------------------------------------------------------------------------


class ForwardMessagesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_chat: str
    to_chat: str
    message_ids: List[int] = Field(..., min_length=1, max_length=100)
    silent: bool = False


async def _forward_messages(
    holder: TelethonHolder, params: ForwardMessagesParams
) -> List[Dict[str, Any]]:
    holder.assert_writeable()
    source = await holder.resolve_entity(params.from_chat)
    target = await holder.resolve_entity(params.to_chat)
    if holder.dry_run:
        return [{"dry_run": True, "would_forward": params.model_dump(mode="json")}]
    async with holder.guard("send", chat_key=_chat_key(params.to_chat)) as client:
        result = await client.forward_messages(
            entity=target,
            messages=params.message_ids,
            from_peer=source,
            silent=params.silent,
        )
    if not isinstance(result, list):
        result = [result]
    return [_message_to_dict(m) for m in result if m is not None]


_register(
    Tool(
        name="forward_messages",
        description="Forward one or more messages between chats.",
        params_model=ForwardMessagesParams,
        handler=_forward_messages,
    )
)


# ---------------------------------------------------------------------------
# delete_messages
# ---------------------------------------------------------------------------


class DeleteMessagesParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    message_ids: List[int] = Field(..., min_length=1, max_length=100)
    revoke: bool = Field(True, description="Delete for everyone, not just you.")


async def _delete_messages(
    holder: TelethonHolder, params: DeleteMessagesParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_delete": params.model_dump(mode="json")}
    async with holder.guard("other") as client:
        affected = await client.delete_messages(
            entity=entity,
            message_ids=params.message_ids,
            revoke=params.revoke,
        )
    deleted = sum(getattr(a, "pts_count", 0) for a in affected)
    return {"deleted": deleted, "requested": len(params.message_ids)}


_register(
    Tool(
        name="delete_messages",
        description="Delete messages by ID.",
        params_model=DeleteMessagesParams,
        handler=_delete_messages,
    )
)


# ---------------------------------------------------------------------------
# edit_message
# ---------------------------------------------------------------------------


class EditMessageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    message_id: int
    text: str = Field(..., min_length=1, max_length=4096)
    parse_mode: Optional[str] = None
    link_preview: bool = True


async def _edit_message(
    holder: TelethonHolder, params: EditMessageParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_edit": params.model_dump(mode="json")}
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        msg = await client.edit_message(
            entity=entity,
            message=params.message_id,
            text=params.text,
            parse_mode=params.parse_mode,
            link_preview=params.link_preview,
        )
    return _message_to_dict(msg)


_register(
    Tool(
        name="edit_message",
        description="Edit a message you sent.",
        params_model=EditMessageParams,
        handler=_edit_message,
    )
)


# ---------------------------------------------------------------------------
# mark_read
# ---------------------------------------------------------------------------


class MarkReadParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    max_id: int = Field(0, ge=0, description="Mark up to this message ID. 0 = all.")


async def _mark_read(
    holder: TelethonHolder, params: MarkReadParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_mark": params.model_dump(mode="json")}
    async with holder.guard("other") as client:
        ok = await client.send_read_acknowledge(
            entity=entity,
            max_id=params.max_id,
        )
    return {"ok": bool(ok)}


_register(
    Tool(
        name="mark_read",
        description="Mark messages in a chat as read.",
        params_model=MarkReadParams,
        handler=_mark_read,
    )
)


# ---------------------------------------------------------------------------
# send_file
# ---------------------------------------------------------------------------


class SendFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    file_url: str = Field(
        ...,
        description="HTTPS URL of the file to download and forward to Telegram.",
    )
    caption: Optional[str] = None
    parse_mode: Optional[str] = None
    silent: bool = False
    force_document: bool = False
    max_bytes: int = Field(50 * 1024 * 1024, ge=1, le=2 * 1024 * 1024 * 1024)


async def _send_file(
    holder: TelethonHolder, params: SendFileParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    if not params.file_url.startswith(("http://", "https://")):
        raise ValueError("file_url must be http(s)")

    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_send_file": params.model_dump(mode="json")}

    os.makedirs("/tmp/telethon-plus", exist_ok=True)
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
        async with http.stream("GET", params.file_url) as resp:
            resp.raise_for_status()
            length = int(resp.headers.get("content-length") or 0)
            if length and length > params.max_bytes:
                raise ValueError(
                    f"file too large: {length} > {params.max_bytes}"
                )

            tmp_path = f"/tmp/telethon-plus/upload-{os.getpid()}-{id(resp)}"
            written = 0
            with open(tmp_path, "wb") as fh:
                async for chunk in resp.aiter_bytes(1 << 16):
                    written += len(chunk)
                    if written > params.max_bytes:
                        os.unlink(tmp_path)
                        raise ValueError(
                            f"file too large: exceeded {params.max_bytes}"
                        )
                    fh.write(chunk)

    try:
        async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
            msg = await client.send_file(
                entity=entity,
                file=tmp_path,
                caption=params.caption,
                parse_mode=params.parse_mode,
                silent=params.silent,
                force_document=params.force_document,
            )
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    return _message_to_dict(msg)


_register(
    Tool(
        name="send_file",
        description=(
            "Download a file from an HTTP(S) URL and send it to a chat. "
            "Use force_document=true to send as a generic file instead of "
            "letting Telegram pick a media type."
        ),
        params_model=SendFileParams,
        handler=_send_file,
    )
)


# ---------------------------------------------------------------------------
# get_participants
# ---------------------------------------------------------------------------


class GetParticipantsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    limit: int = Field(100, ge=1, le=1000)
    search: Optional[str] = None


async def _get_participants(
    holder: TelethonHolder, params: GetParticipantsParams
) -> List[Dict[str, Any]]:
    PARTICIPANTS_PAGE = 200  # channels.getParticipants page size
    entity = await holder.resolve_entity(params.chat)
    # get_participants pulls FullChannel-ish data; classify under get_full.
    async with holder.guard("get_full", chat_key=_chat_key(params.chat), chat_kind="read") as client:
        participants = await client.get_participants(
            entity,
            limit=params.limit,
            search=params.search or "",
        )
    extra_pages = max(0, (len(participants) - 1) // PARTICIPANTS_PAGE)
    holder.throttle.record_extra("get_full", extra_pages)
    return [_entity_to_dict(p) for p in participants]


_register(
    Tool(
        name="get_participants",
        description="List members of a group or channel.",
        params_model=GetParticipantsParams,
        handler=_get_participants,
    )
)


# ---------------------------------------------------------------------------
# create_group
# ---------------------------------------------------------------------------


class CreateGroupParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(..., min_length=1, max_length=255)
    megagroup: bool = Field(True, description="True = supergroup, False = broadcast channel.")


async def _create_group(
    holder: TelethonHolder, params: CreateGroupParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    if holder.dry_run:
        return {"dry_run": True, "would_create": params.model_dump(mode="json")}
    async with holder.guard("create") as client:
        result = await client(
            CreateChannelRequest(
                title=params.title,
                about="",
                megagroup=params.megagroup,
            )
        )
    return _entity_to_dict(result.chats[0])


_register(
    Tool(
        name="create_group",
        description="Create a new supergroup or broadcast channel.",
        params_model=CreateGroupParams,
        handler=_create_group,
    )
)


# ---------------------------------------------------------------------------
# delete_chat
# ---------------------------------------------------------------------------


class DeleteChatParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str


async def _delete_chat(
    holder: TelethonHolder, params: DeleteChatParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_delete_chat": params.chat}
    async with holder.guard("other") as client:
        await client(DeleteChannelRequest(channel=entity))
    holder.cache.invalidate(params.chat)
    return {"ok": True}


_register(
    Tool(
        name="delete_chat",
        description="Delete a supergroup or channel you own.",
        params_model=DeleteChatParams,
        handler=_delete_chat,
    )
)


# ---------------------------------------------------------------------------
# join_chat
# ---------------------------------------------------------------------------


class JoinChatParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str


async def _join_chat(
    holder: TelethonHolder, params: JoinChatParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_join": params.chat}
    async with holder.guard("join") as client:
        await client(JoinChannelRequest(channel=entity))
    return {"ok": True}


_register(
    Tool(
        name="join_chat",
        description="Join a public channel or supergroup.",
        params_model=JoinChatParams,
        handler=_join_chat,
    )
)


# ---------------------------------------------------------------------------
# leave_chat
# ---------------------------------------------------------------------------


class LeaveChatParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str


async def _leave_chat(
    holder: TelethonHolder, params: LeaveChatParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_leave": params.chat}
    async with holder.guard("other") as client:
        await client(LeaveChannelRequest(channel=entity))
    return {"ok": True}


_register(
    Tool(
        name="leave_chat",
        description="Leave a channel or supergroup.",
        params_model=LeaveChatParams,
        handler=_leave_chat,
    )
)


# ---------------------------------------------------------------------------
# bulk_resolve — resolve many handles at once, respecting the resolve bucket
# ---------------------------------------------------------------------------


class BulkResolveParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chats: List[str] = Field(..., min_length=1, max_length=500)


async def _bulk_resolve(
    holder: TelethonHolder, params: BulkResolveParams
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for chat in params.chats:
        try:
            entity = await holder.resolve_entity(chat)
            results.append({"chat": chat, "entity": _entity_to_dict(entity)})
        except Exception as exc:  # noqa: BLE001
            errors.append({"chat": chat, "error": str(exc), "type": type(exc).__name__})
    return {"resolved": results, "errors": errors, "count": len(results), "failed": len(errors)}


_register(
    Tool(
        name="bulk_resolve",
        description=(
            "Resolve many chat references in one call, honoring the "
            "resolve-username rate limit. Returns per-handle success/error "
            "and populates the entity cache."
        ),
        params_model=BulkResolveParams,
        handler=_bulk_resolve,
    )
)


# ---------------------------------------------------------------------------
# get_message — fetch a single message by ID
# ---------------------------------------------------------------------------


class GetMessageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    message_id: int = Field(..., ge=1)


async def _get_message(
    holder: TelethonHolder, params: GetMessageParams
) -> Dict[str, Any]:
    entity = await holder.resolve_entity(params.chat)
    async with holder.guard("read", chat_key=_chat_key(params.chat), chat_kind="read") as client:
        msgs = await client.get_messages(entity, ids=[params.message_id])
    if not msgs or msgs[0] is None:
        raise ValueError(f"message {params.message_id} not found in chat")
    return _message_to_dict(msgs[0])


_register(
    Tool(
        name="get_message",
        description="Fetch a single message by ID from a chat.",
        params_model=GetMessageParams,
        handler=_get_message,
    )
)


# ---------------------------------------------------------------------------
# download_media — counterpart to send_file
# ---------------------------------------------------------------------------


class DownloadMediaParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    message_id: int = Field(..., ge=1)
    max_bytes: int = Field(50 * 1024 * 1024, ge=1, le=2 * 1024 * 1024 * 1024)


async def _download_media(
    holder: TelethonHolder, params: DownloadMediaParams
) -> Dict[str, Any]:
    entity = await holder.resolve_entity(params.chat)
    chat_key = _chat_key(params.chat)
    async with holder.guard("read", chat_key=chat_key, chat_kind="read") as client:
        msgs = await client.get_messages(entity, ids=[params.message_id])
        if not msgs or msgs[0] is None:
            raise ValueError(f"message {params.message_id} not found in chat")
        msg = msgs[0]
        if msg.media is None:
            raise ValueError("message has no media attachment")
    # Download under a second slot: large downloads can spawn many file-part
    # requests internally; we count that as one read for simplicity but it
    # would be safer to record more.
    async with holder.guard("read", chat_key=chat_key, chat_kind="read") as client:
        buf = io.BytesIO()
        await client.download_media(msg, file=buf)
        data = buf.getvalue()
    if len(data) > params.max_bytes:
        raise ValueError(
            f"media too large: {len(data)} > max_bytes={params.max_bytes}"
        )
    return {
        "size": len(data),
        "media_type": type(msg.media).__name__,
        "data_base64": base64.b64encode(data).decode("ascii"),
    }


_register(
    Tool(
        name="download_media",
        description=(
            "Download the media attachment of a message. Returns the file "
            "as base64-encoded bytes. Reject files larger than max_bytes."
        ),
        params_model=DownloadMediaParams,
        handler=_download_media,
    )
)


# ---------------------------------------------------------------------------
# set_reaction / remove_reaction
# ---------------------------------------------------------------------------


class SetReactionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    message_id: int = Field(..., ge=1)
    emoji: str = Field(..., min_length=1, max_length=16)
    big: bool = Field(False, description="Animate as 'big' reaction.")


async def _set_reaction(
    holder: TelethonHolder, params: SetReactionParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_react": params.model_dump(mode="json")}
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        await client(SendReactionRequest(
            peer=entity,
            msg_id=params.message_id,
            reaction=[ReactionEmoji(emoticon=params.emoji)],
            big=params.big,
        ))
    return {"ok": True}


_register(
    Tool(
        name="set_reaction",
        description="React to a message with an emoji.",
        params_model=SetReactionParams,
        handler=_set_reaction,
    )
)


class RemoveReactionParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    message_id: int = Field(..., ge=1)


async def _remove_reaction(
    holder: TelethonHolder, params: RemoveReactionParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_unreact": params.model_dump(mode="json")}
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        await client(SendReactionRequest(
            peer=entity,
            msg_id=params.message_id,
            reaction=[],
        ))
    return {"ok": True}


_register(
    Tool(
        name="remove_reaction",
        description="Remove your reaction from a message.",
        params_model=RemoveReactionParams,
        handler=_remove_reaction,
    )
)


# ---------------------------------------------------------------------------
# pin_message / unpin_message
# ---------------------------------------------------------------------------


class PinMessageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    message_id: int = Field(..., ge=1)
    silent: bool = Field(True, description="Pin without notification.")
    pm_oneside: bool = Field(False, description="In DMs, pin only for yourself.")


async def _pin_message(
    holder: TelethonHolder, params: PinMessageParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_pin": params.model_dump(mode="json")}
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        await client(UpdatePinnedMessageRequest(
            peer=entity,
            id=params.message_id,
            silent=params.silent,
            pm_oneside=params.pm_oneside,
        ))
    return {"ok": True}


_register(
    Tool(
        name="pin_message",
        description="Pin a message in a chat.",
        params_model=PinMessageParams,
        handler=_pin_message,
    )
)


class UnpinMessageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    message_id: int = Field(..., ge=1)


async def _unpin_message(
    holder: TelethonHolder, params: UnpinMessageParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_unpin": params.model_dump(mode="json")}
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        await client(UpdatePinnedMessageRequest(
            peer=entity,
            id=params.message_id,
            unpin=True,
        ))
    return {"ok": True}


_register(
    Tool(
        name="unpin_message",
        description="Unpin a previously pinned message.",
        params_model=UnpinMessageParams,
        handler=_unpin_message,
    )
)


# ---------------------------------------------------------------------------
# search_dialogs — find chats by title fragment
# ---------------------------------------------------------------------------


class SearchDialogsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=1, max_length=128)
    limit: int = Field(20, ge=1, le=100)


async def _search_dialogs(
    holder: TelethonHolder, params: SearchDialogsParams
) -> List[Dict[str, Any]]:
    """Iterate dialogs locally and filter. Charges read-bucket per server page."""
    DIALOGS_PAGE = 100
    q = params.query.lower()
    out: List[Dict[str, Any]] = []
    seen = 0
    async with holder.guard("read") as client:
        async for dialog in client.iter_dialogs():
            seen += 1
            name = (dialog.name or "").lower()
            username = (getattr(dialog.entity, "username", "") or "").lower()
            if q in name or q in username:
                item = _entity_to_dict(dialog.entity)
                item["unread_count"] = dialog.unread_count
                item["pinned"] = dialog.pinned
                out.append(item)
                if len(out) >= params.limit:
                    break
    # Account for internal pagination beyond the first acquired slot.
    holder.throttle.record_extra("read", max(0, (seen - 1) // DIALOGS_PAGE))
    return out


_register(
    Tool(
        name="search_dialogs",
        description=(
            "Search your dialogs by title or @username substring. "
            "Case-insensitive."
        ),
        params_model=SearchDialogsParams,
        handler=_search_dialogs,
    )
)


# ---------------------------------------------------------------------------
# get_linked_chat — Telegram channels have linked discussion groups
# ---------------------------------------------------------------------------


class GetLinkedChatParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str


async def _get_linked_chat(
    holder: TelethonHolder, params: GetLinkedChatParams
) -> Dict[str, Any]:
    entity = await holder.resolve_entity(params.chat)
    async with holder.guard("get_full") as client:
        full = await client(GetFullChannelRequest(channel=entity))
    linked_id = getattr(full.full_chat, "linked_chat_id", None)
    if not linked_id:
        return {"linked_chat_id": None, "linked_entity": None}
    # Find the linked entity in the response's chats list.
    linked_entity = next(
        (c for c in full.chats if getattr(c, "id", None) == linked_id), None
    )
    return {
        "linked_chat_id": linked_id,
        "linked_entity": _entity_to_dict(linked_entity) if linked_entity else None,
    }


_register(
    Tool(
        name="get_linked_chat",
        description=(
            "Resolve a channel's linked discussion group (if any). Returns "
            "linked_chat_id and the linked entity profile."
        ),
        params_model=GetLinkedChatParams,
        handler=_get_linked_chat,
    )
)


# ---------------------------------------------------------------------------
# join_via_invite — t.me/+hash invite link
# ---------------------------------------------------------------------------


class JoinViaInviteParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invite: str = Field(
        ...,
        description=(
            "Private invite hash (the part after t.me/+) or the full link."
        ),
    )


def _extract_invite_hash(raw: str) -> str:
    s = raw.strip()
    for prefix in ("https://t.me/+", "http://t.me/+", "t.me/+", "@joinchat/", "+"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if "joinchat/" in s:
        s = s.split("joinchat/", 1)[1]
    return s.strip("/")


async def _join_via_invite(
    holder: TelethonHolder, params: JoinViaInviteParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    invite_hash = _extract_invite_hash(params.invite)
    if not invite_hash:
        raise ValueError("could not extract invite hash from input")
    if holder.dry_run:
        return {"dry_run": True, "would_join_invite": invite_hash}
    async with holder.guard("join") as client:
        result = await client(ImportChatInviteRequest(hash=invite_hash))
    chats = getattr(result, "chats", []) or []
    return {
        "ok": True,
        "joined": [_entity_to_dict(c) for c in chats],
    }


_register(
    Tool(
        name="join_via_invite",
        description="Join a private chat via t.me/+hash invite link.",
        params_model=JoinViaInviteParams,
        handler=_join_via_invite,
    )
)


# ---------------------------------------------------------------------------
# Channel admin actions: ban / unban / kick / promote / demote
# ---------------------------------------------------------------------------


class BanUserParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    user: str = Field(..., description="User reference (username/ID).")
    until_seconds: int = Field(
        0,
        ge=0,
        description="Ban duration from now in seconds. 0 = permanent.",
    )


async def _ban_user(
    holder: TelethonHolder, params: BanUserParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    chat = await holder.resolve_entity(params.chat)
    user = await holder.resolve_entity(params.user)
    if holder.dry_run:
        return {"dry_run": True, "would_ban": params.model_dump(mode="json")}
    rights = ChatBannedRights(
        until_date=params.until_seconds or 0,
        view_messages=True,
    )
    async with holder.guard("other") as client:
        await client(EditBannedRequest(channel=chat, participant=user, banned_rights=rights))
    return {"ok": True}


_register(
    Tool(
        name="ban_user",
        description="Ban a user from a supergroup/channel. until_seconds=0 = permanent.",
        params_model=BanUserParams,
        handler=_ban_user,
    )
)


class UnbanUserParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    user: str


async def _unban_user(
    holder: TelethonHolder, params: UnbanUserParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    chat = await holder.resolve_entity(params.chat)
    user = await holder.resolve_entity(params.user)
    if holder.dry_run:
        return {"dry_run": True, "would_unban": params.model_dump(mode="json")}
    rights = ChatBannedRights(until_date=0)
    async with holder.guard("other") as client:
        await client(EditBannedRequest(channel=chat, participant=user, banned_rights=rights))
    return {"ok": True}


_register(
    Tool(
        name="unban_user",
        description="Lift a ban on a user in a supergroup/channel.",
        params_model=UnbanUserParams,
        handler=_unban_user,
    )
)


class KickUserParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    user: str


async def _kick_user(
    holder: TelethonHolder, params: KickUserParams
) -> Dict[str, Any]:
    """Ban-then-unban = kick without permanent ban."""
    holder.assert_writeable()
    chat = await holder.resolve_entity(params.chat)
    user = await holder.resolve_entity(params.user)
    if holder.dry_run:
        return {"dry_run": True, "would_kick": params.model_dump(mode="json")}
    async with holder.guard("other") as client:
        await client(EditBannedRequest(
            channel=chat,
            participant=user,
            banned_rights=ChatBannedRights(until_date=0, view_messages=True),
        ))
        await client(EditBannedRequest(
            channel=chat,
            participant=user,
            banned_rights=ChatBannedRights(until_date=0),
        ))
    return {"ok": True}


_register(
    Tool(
        name="kick_user",
        description="Kick a user from a chat (ban + immediate unban — they can rejoin via invite).",
        params_model=KickUserParams,
        handler=_kick_user,
    )
)


class PromoteUserParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    user: str
    title: Optional[str] = Field(None, max_length=16, description="Admin custom title.")
    change_info: bool = False
    post_messages: bool = False
    edit_messages: bool = False
    delete_messages: bool = False
    ban_users: bool = False
    invite_users: bool = False
    pin_messages: bool = False
    add_admins: bool = False
    anonymous: bool = False
    manage_call: bool = False


async def _promote_user(
    holder: TelethonHolder, params: PromoteUserParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    chat = await holder.resolve_entity(params.chat)
    user = await holder.resolve_entity(params.user)
    if holder.dry_run:
        return {"dry_run": True, "would_promote": params.model_dump(mode="json")}
    rights = ChatAdminRights(
        change_info=params.change_info,
        post_messages=params.post_messages,
        edit_messages=params.edit_messages,
        delete_messages=params.delete_messages,
        ban_users=params.ban_users,
        invite_users=params.invite_users,
        pin_messages=params.pin_messages,
        add_admins=params.add_admins,
        anonymous=params.anonymous,
        manage_call=params.manage_call,
    )
    async with holder.guard("other") as client:
        await client(EditAdminRequest(
            channel=chat,
            user_id=user,
            admin_rights=rights,
            rank=params.title or "",
        ))
    return {"ok": True}


_register(
    Tool(
        name="promote_user",
        description=(
            "Grant admin rights to a user in a supergroup/channel. Each "
            "boolean toggles a specific permission. Pass title for the custom rank."
        ),
        params_model=PromoteUserParams,
        handler=_promote_user,
    )
)


class DemoteUserParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    user: str


async def _demote_user(
    holder: TelethonHolder, params: DemoteUserParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    chat = await holder.resolve_entity(params.chat)
    user = await holder.resolve_entity(params.user)
    if holder.dry_run:
        return {"dry_run": True, "would_demote": params.model_dump(mode="json")}
    rights = ChatAdminRights()  # all False = no admin powers
    async with holder.guard("other") as client:
        await client(EditAdminRequest(
            channel=chat,
            user_id=user,
            admin_rights=rights,
            rank="",
        ))
    return {"ok": True}


_register(
    Tool(
        name="demote_user",
        description="Strip admin rights from a user.",
        params_model=DemoteUserParams,
        handler=_demote_user,
    )
)


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------


class CreatePollParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    question: str = Field(..., min_length=1, max_length=255)
    options: List[str] = Field(..., min_length=2, max_length=10)
    multiple_choice: bool = False
    quiz: bool = False
    correct_option: Optional[int] = Field(
        None, ge=0, description="0-based index of the correct answer for a quiz."
    )
    solution: Optional[str] = Field(
        None, max_length=200, description="Quiz explanation shown after answering."
    )


async def _create_poll(
    holder: TelethonHolder, params: CreatePollParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_create_poll": params.model_dump(mode="json")}
    if params.quiz and params.correct_option is None:
        raise ValueError("quiz polls require correct_option")
    answers = [
        PollAnswer(text=opt, option=bytes([i])) for i, opt in enumerate(params.options)
    ]
    poll = Poll(
        id=0,
        question=params.question,
        answers=answers,
        multiple_choice=params.multiple_choice,
        quiz=params.quiz,
    )
    media = InputMediaPoll(
        poll=poll,
        correct_answers=(
            [bytes([params.correct_option])] if params.correct_option is not None else None
        ),
        solution=params.solution,
    )
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        msg = await client.send_file(entity=entity, file=media)
    return _message_to_dict(msg)


_register(
    Tool(
        name="create_poll",
        description=(
            "Create a poll in a chat. Set quiz=true with correct_option for "
            "quiz-style polls."
        ),
        params_model=CreatePollParams,
        handler=_create_poll,
    )
)


class VotePollParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    message_id: int = Field(..., ge=1)
    options: List[int] = Field(..., min_length=1, max_length=10, description="0-based indices.")


async def _vote_poll(
    holder: TelethonHolder, params: VotePollParams
) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_vote": params.model_dump(mode="json")}
    options_bytes = [bytes([i]) for i in params.options]
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        await client(SendVoteRequest(
            peer=entity,
            msg_id=params.message_id,
            options=options_bytes,
        ))
    return {"ok": True}


_register(
    Tool(
        name="vote_poll",
        description="Vote on a poll by 0-based option index/indices.",
        params_model=VotePollParams,
        handler=_vote_poll,
    )
)


class GetPollResultsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str
    message_id: int = Field(..., ge=1)


async def _get_poll_results(
    holder: TelethonHolder, params: GetPollResultsParams
) -> Dict[str, Any]:
    entity = await holder.resolve_entity(params.chat)
    async with holder.guard("read") as client:
        result = await client(GetMessagesReactionsRequest(
            peer=entity,
            id=[params.message_id],
        ))
    # The poll results are part of the message; fetch the message directly too.
    async with holder.guard("read") as client:
        msgs = await client.get_messages(entity, ids=[params.message_id])
    if not msgs or msgs[0] is None or msgs[0].poll is None:
        raise ValueError("message has no poll")
    poll_msg = msgs[0]
    results = getattr(poll_msg.poll, "results", None) or getattr(poll_msg, "results", None)
    return {
        "question": getattr(poll_msg.poll.poll, "question", None),
        "total_voters": getattr(results, "total_voters", None) if results else None,
        "results": [
            {
                "option_index": r.option[0] if r.option else None,
                "voters": r.voters,
                "correct": r.correct,
                "chosen": r.chosen,
            }
            for r in (getattr(results, "results", None) or [])
        ] if results else [],
        "_reactions_envelope_received": result is not None,
    }


_register(
    Tool(
        name="get_poll_results",
        description="Fetch current results of a poll message.",
        params_model=GetPollResultsParams,
        handler=_get_poll_results,
    )
)


# ---------------------------------------------------------------------------
# throttle_status — observability tool also exposed as REST endpoint
# ---------------------------------------------------------------------------


class ThrottleStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _throttle_status(
    holder: TelethonHolder, _: ThrottleStatusParams
) -> Dict[str, Any]:
    return {
        "throttle": holder.throttle.snapshot(),
        "cache": holder.cache.stats(),
        "read_only": holder.cfg.read_only,
        "dry_run": holder.cfg.dry_run,
    }


_register(
    Tool(
        name="throttle_status",
        description=(
            "Return current throttling state: bucket usage, adaptive "
            "multiplier, recent flood events, cache stats, safety flags."
        ),
        params_model=ThrottleStatusParams,
        handler=_throttle_status,
    )
)


# ---------------------------------------------------------------------------
# account_health
# ---------------------------------------------------------------------------


class AccountHealthParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _account_health(
    holder: TelethonHolder, _: AccountHealthParams
) -> Dict[str, Any]:
    snap = holder.throttle.snapshot()
    risk = "ok"
    mult = float(snap.get("multiplier", 1.0))
    if mult >= 8:
        risk = "high"
    elif mult >= 2:
        risk = "warning"
    return {
        "authorized": holder._client is not None,
        "risk": risk,
        "multiplier": mult,
        "flood_events_1h": snap.get("flood_events_1h", 0),
        "read_only": holder.cfg.read_only,
        "dry_run": holder.cfg.dry_run,
    }


_register(
    Tool(
        name="account_health",
        description=(
            "Account-level health: are we connected, what's our flood-risk "
            "tier based on recent FLOOD_WAITs and the adaptive multiplier."
        ),
        params_model=AccountHealthParams,
        handler=_account_health,
    )
)

