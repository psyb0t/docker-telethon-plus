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

import base64
import contextlib
import datetime as _dt
import io
import mimetypes
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Annotated, Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import unquote, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field
from telethon import utils as tl_utils
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
    DeleteChatUserRequest,
    GetPollResultsRequest,
    ImportChatInviteRequest,
    SendReactionRequest,
    SendVoteRequest,
    UpdatePinnedMessageRequest,
)
from telethon.tl.types import (
    Channel,
    Chat,
    ChatAdminRights,
    ChatBannedRights,
    DocumentAttributeFilename,
    InputMediaPoll,
    InputPollAnswer,
    InputUserSelf,
    Poll,
    ReactionEmoji,
    TextWithEntities,
    UpdateMessagePoll,
    User,
)

from app.client import TelethonHolder

# Limits Telegram enforces server side. Declaring them on the params models
# turns an oversized value into a validation error instead of an RPC failure.
_MAX_CHANNEL_TITLE_LENGTH = 128
_MAX_POLL_QUESTION_LENGTH = 255
_MAX_POLL_OPTION_LENGTH = 100

# A poll that does not exist yet has no server-assigned id or hash.
_NEW_POLL_ID = 0
_NEW_POLL_HASH = 0
# messages.getPollResults hash for "send the results whatever I have".
_NO_POLL_RESULTS_HASH = 0

# Telethon's participants iterator asks for 200 users per request
# (_MAX_PARTICIPANTS_CHUNK_SIZE in telethon/client/chats.py).
_PARTICIPANTS_PAGE = 200

# Telethon cuts uploads and downloads into parts sized in KB by
# telethon.utils.get_appropriated_part_size.
_BYTES_PER_KB = 1024

# send_file staging.
_DOWNLOAD_TIMEOUT_SECONDS = 60.0
_DOWNLOAD_CHUNK_BYTES = 1 << 16
_STAGED_FILE_PREFIX = "upload-"
_HTTP_SCHEMES = ("http", "https")
# Real extensions are short; the cap limits what a URL can add to the name of
# the staged file.
_MAX_EXTENSION_LENGTH = 10
_EXTENSION_PATTERN = re.compile(rf"\.[A-Za-z0-9]{{1,{_MAX_EXTENSION_LENGTH}}}")
# Filesystems typically limit a file name to 255, so real file names fit; the
# cap keeps a URL from supplying a huge one.
_MAX_FILE_NAME_LENGTH = 255
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_PATH_SEPARATORS = re.compile(r"[/\\]")
_NON_NAMES = ("", ".", "..")

# PromoteUserParams fields that map one to one onto ChatAdminRights flags.
_ADMIN_RIGHT_NAMES = (
    "change_info",
    "post_messages",
    "edit_messages",
    "delete_messages",
    "ban_users",
    "invite_users",
    "pin_messages",
    "add_admins",
    "anonymous",
    "manage_call",
)

ParamsModel = type[BaseModel]
# Each handler takes its own params model; Callable parameters are
# contravariant, so the registry types the second argument as Any rather than
# BaseModel. server._invoke validates against params_model before the call.
Handler = Callable[[TelethonHolder, Any], Awaitable[Any]]


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
        origin = getattr(fwd_from, "from_id", None)
        fwd = {
            "date": fwd_from.date.isoformat() if getattr(fwd_from, "date", None) else None,
            "from_id": tl_utils.get_peer_id(origin) if origin is not None else None,
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
        # The property returns None for a reply to a story, whose header has
        # no message id.
        "reply_to_msg_id": msg.reply_to_msg_id,
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


def _require_user(entity: Any, action: str) -> None:
    """Raise ValueError unless entity is a user.

    EditAdminRequest takes an InputUser, so a channel or group passed as the
    user made Telethon raise TypeError, which surfaced as a 500.
    """
    if isinstance(entity, User):
        return
    raise ValueError(f"{action} needs a user, but this is a {type(entity).__name__}")


def _require_channel(entity: Any, action: str) -> None:
    """Raise ValueError unless entity is a channel or supergroup.

    Requests that take an InputChannel make Telethon raise TypeError for a
    basic group or a user, which the server would report as a 500 instead of
    a 400 the caller can act on.
    """
    if isinstance(entity, Channel):
        return
    raise ValueError(
        f"{action} requires a channel or supergroup, but this chat is a " f"{type(entity).__name__}"
    )


def _transfer_parts(size: int) -> int:
    """Requests Telethon needs to move size bytes: it cuts uploads and
    downloads into parts of get_appropriated_part_size(size) KB."""
    part_bytes = tl_utils.get_appropriated_part_size(size) * _BYTES_PER_KB
    return (size + part_bytes - 1) // part_bytes


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
    chat: str = Field(..., description="Username, phone, t.me link, or numeric ID as string.")


async def _get_entity(holder: TelethonHolder, params: GetEntityParams) -> Dict[str, Any]:
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
    reply_to: Optional[int] = Field(None, description="Message ID to reply to.")
    silent: bool = Field(False, description="Send without notification.")
    link_preview: bool = Field(True, description="Allow link previews.")
    schedule: Optional[_dt.datetime] = Field(
        None,
        description=(
            "Optional UTC datetime to schedule the send. ISO 8601 string; " "must be in the future."
        ),
    )


async def _send_message(holder: TelethonHolder, params: SendMessageParams) -> Dict[str, Any]:
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


async def _get_messages(holder: TelethonHolder, params: GetMessagesParams) -> List[Dict[str, Any]]:
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
    search: Optional[str] = Field(
        None,
        min_length=1,
        max_length=128,
        description="Optional case-insensitive substring filter on title/username.",
    )


async def _get_dialogs(holder: TelethonHolder, params: GetDialogsParams) -> List[Dict[str, Any]]:
    DIALOGS_PAGE = 100  # Telegram's messages.getDialogs page size

    # Search mode: iterate dialogs and filter locally — stop when we have
    # enough hits or run out of dialogs.
    if params.search:
        q = params.search.lower()
        matches: List[Dict[str, Any]] = []
        seen = 0
        async with holder.guard("read") as client:
            async for dialog in client.iter_dialogs(archived=params.archived):
                seen += 1
                name = (dialog.name or "").lower()
                username = (getattr(dialog.entity, "username", "") or "").lower()
                if q in name or q in username:
                    item = _entity_to_dict(dialog.entity)
                    item["unread_count"] = dialog.unread_count
                    item["pinned"] = dialog.pinned
                    if dialog.message is not None:
                        item["last_message"] = _message_to_dict(dialog.message)
                    if dialog.entity is not None:
                        holder.cache.put(str(getattr(dialog.entity, "id", "")), dialog.entity)
                        uname = getattr(dialog.entity, "username", None)
                        if uname:
                            holder.cache.put(uname, dialog.entity)
                    matches.append(item)
                    if len(matches) >= params.limit:
                        break
        holder.throttle.record_extra("read", max(0, (seen - 1) // DIALOGS_PAGE))
        return matches

    async with holder.guard("read") as client:
        dialogs = await client.get_dialogs(limit=params.limit, archived=params.archived)
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


async def _delete_messages(holder: TelethonHolder, params: DeleteMessagesParams) -> Dict[str, Any]:
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


async def _edit_message(holder: TelethonHolder, params: EditMessageParams) -> Dict[str, Any]:
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


async def _mark_read(holder: TelethonHolder, params: MarkReadParams) -> Dict[str, Any]:
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
    text: Optional[str] = Field(
        None,
        max_length=4096,
        description="Optional caption/message text to attach to the file.",
    )
    parse_mode: Optional[str] = None
    silent: bool = False
    force_document: bool = False
    max_bytes: int = Field(50 * 1024 * 1024, ge=1, le=2 * 1024 * 1024 * 1024)


def _file_name_from_url(url: str) -> Optional[str]:
    """Last path segment of url, cleaned for use as a file name shown to the
    recipient, or None when the path carries no usable name."""
    path = unquote(urlsplit(url).path)
    segment = _PATH_SEPARATORS.split(path)[-1]
    name = _CONTROL_CHARACTERS.sub("", segment).strip()
    if name in _NON_NAMES:
        return None
    return name[:_MAX_FILE_NAME_LENGTH]


def _staging_suffix(file_name: Optional[str], content_type: str) -> str:
    """Extension for the staged file. Telethon tells photos from documents and
    picks the MIME type from it, so it comes from the URL's file name and,
    failing that, from the response Content-Type."""
    if file_name:
        extension = os.path.splitext(file_name)[1]
        if _EXTENSION_PATTERN.fullmatch(extension):
            return extension
    mime_type = content_type.split(";", 1)[0].strip().lower()
    return mimetypes.guess_extension(mime_type) or ""


def _check_download_response(resp: httpx.Response, max_bytes: int) -> None:
    resp.raise_for_status()
    # Redirects are followed, so the final URL is what was fetched.
    if resp.url.scheme not in _HTTP_SCHEMES:
        raise ValueError(f"file_url redirected to a non-http(s) URL ({resp.url.scheme})")
    length = int(resp.headers.get("content-length") or 0)
    if length and length > max_bytes:
        raise ValueError(f"file too large: {length} > {max_bytes}")


async def _write_body(resp: httpx.Response, fd: int, max_bytes: int) -> int:
    """Stream the response body into the open descriptor fd, which is closed
    here, and return the byte count. Raises ValueError once the body outgrows
    max_bytes, since a streamed body may carry no Content-Length."""
    written = 0
    with os.fdopen(fd, "wb") as fh:
        async for chunk in resp.aiter_bytes(_DOWNLOAD_CHUNK_BYTES):
            written += len(chunk)
            if written > max_bytes:
                raise ValueError(f"file too large: exceeded {max_bytes}")
            fh.write(chunk)
    return written


async def _send_file(holder: TelethonHolder, params: SendFileParams) -> Dict[str, Any]:
    holder.assert_writeable()
    if not params.file_url.startswith(("http://", "https://")):
        raise ValueError("file_url must be http(s)")

    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_send_file": params.model_dump(mode="json")}

    file_name = _file_name_from_url(params.file_url)
    attributes = [DocumentAttributeFilename(file_name=file_name)] if file_name else None

    # TELETHON_DOWNLOAD_DIR; mkstemp gives the file an unguessable name that
    # only this process can open. It is created once the response headers are
    # in, because the Content-Type may supply the extension. One finally
    # removes it whether the download or the send fails.
    os.makedirs(holder.cfg.download_dir, exist_ok=True)
    staged_path: Optional[str] = None
    try:
        try:
            async with httpx.AsyncClient(
                timeout=_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True
            ) as http:
                async with http.stream("GET", params.file_url) as resp:
                    _check_download_response(resp, params.max_bytes)
                    fd, staged_path = tempfile.mkstemp(
                        prefix=_STAGED_FILE_PREFIX,
                        suffix=_staging_suffix(file_name, resp.headers.get("content-type", "")),
                        dir=holder.cfg.download_dir,
                    )
                    written = await _write_body(resp, fd, params.max_bytes)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise ValueError(f"file_url fetch failed: {exc}") from exc
        if written == 0:
            # Telegram refuses an empty file; say so before spending a send.
            raise ValueError("file_url returned an empty file")

        async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
            msg = await client.send_file(
                entity=entity,
                file=staged_path,
                caption=params.text,
                parse_mode=params.parse_mode,
                silent=params.silent,
                force_document=params.force_document,
                attributes=attributes,
            )
        # The guard paid for one request, but the upload sent one request per
        # part plus the SendMediaRequest that posts the file.
        holder.throttle.record_extra("send", _transfer_parts(written))
    finally:
        if staged_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(staged_path)

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


def _participant_requests(entity: Any, returned: int, limit: int, search: str) -> int:
    """Requests Telethon sent for one get_participants call, mirroring
    _ParticipantsIter in telethon/client/chats.py. Assumes full pages: it
    cannot see participants Telethon dropped, such as members who left."""
    if not isinstance(entity, Channel):
        # A basic group is one GetFullChat and a user one GetUsers.
        return 1
    pages = (returned + _PARTICIPANTS_PAGE - 1) // _PARTICIPANTS_PAGE
    if returned < limit:
        # Telethon only knows the list ended when a further page comes back
        # empty. A list that fills the limit is never asked for another page.
        pages += 1
    if search:
        # A search can narrow the list, so Telethon first asks for the total.
        pages += 1
    return pages


async def _get_participants(
    holder: TelethonHolder, params: GetParticipantsParams
) -> List[Dict[str, Any]]:
    entity = await holder.resolve_entity(params.chat)
    search = params.search or ""
    # get_participants pulls FullChannel-ish data; classify under get_full.
    async with holder.guard(
        "get_full", chat_key=_chat_key(params.chat), chat_kind="read"
    ) as client:
        participants = await client.get_participants(
            entity,
            limit=params.limit,
            search=search,
        )
    requests = _participant_requests(entity, len(participants), params.limit, search)
    holder.throttle.record_extra("get_full", max(0, requests - 1))
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
    title: str = Field(..., min_length=1, max_length=_MAX_CHANNEL_TITLE_LENGTH)
    megagroup: bool = Field(True, description="True = supergroup, False = broadcast channel.")


async def _create_group(holder: TelethonHolder, params: CreateGroupParams) -> Dict[str, Any]:
    holder.assert_writeable()
    if holder.dry_run:
        return {"dry_run": True, "would_create": params.model_dump(mode="json")}
    async with holder.guard("create") as client:
        result = await client(
            CreateChannelRequest(
                title=params.title,
                about="",
                # Telegram needs exactly one of the two flags.
                broadcast=not params.megagroup,
                megagroup=params.megagroup,
            )
        )
    chat = result.chats[0]
    # Cached by the id the answer reports, so that id still resolves after a
    # restart without a scan of the dialogs.
    holder.cache.put(str(chat.id), chat)
    return _entity_to_dict(chat)


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


async def _delete_chat(holder: TelethonHolder, params: DeleteChatParams) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    _require_channel(entity, "delete_chat")
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


async def _join_chat(holder: TelethonHolder, params: JoinChatParams) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    _require_channel(entity, "join_chat")
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


async def _leave_chat(holder: TelethonHolder, params: LeaveChatParams) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    is_basic_group = isinstance(entity, Chat)
    if not is_basic_group:
        _require_channel(entity, "leave_chat")
    if holder.dry_run:
        return {"dry_run": True, "would_leave": params.chat}
    # A basic group is left by removing yourself; LeaveChannelRequest only
    # takes a channel.
    request = (
        DeleteChatUserRequest(chat_id=entity.id, user_id=InputUserSelf())
        if is_basic_group
        else LeaveChannelRequest(channel=entity)
    )
    async with holder.guard("other") as client:
        await client(request)
    return {"ok": True}


_register(
    Tool(
        name="leave_chat",
        description="Leave a channel, supergroup or basic group.",
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


async def _bulk_resolve(holder: TelethonHolder, params: BulkResolveParams) -> Dict[str, Any]:
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


async def _get_message(holder: TelethonHolder, params: GetMessageParams) -> Dict[str, Any]:
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


_DEFAULT_FILENAMES_BY_MIME = {
    "image/jpeg": "photo.jpg",
    "image/png": "photo.png",
    "image/webp": "photo.webp",
    "image/gif": "photo.gif",
    "video/mp4": "video.mp4",
    "video/webm": "video.webm",
    "audio/ogg": "audio.ogg",
    "audio/mpeg": "audio.mp3",
}


async def download_media_bytes(
    holder: TelethonHolder,
    chat: str,
    message_id: int,
    max_bytes: int,
) -> Dict[str, Any]:
    """Resolve, fetch, download. Returns {"data", "mime_type", "filename",
    "media_type", "size"}. Used by both the base64 tool and the streaming
    REST endpoint."""
    entity = await holder.resolve_entity(chat)
    chat_key = _chat_key(chat)
    async with holder.guard("read", chat_key=chat_key, chat_kind="read") as client:
        msgs = await client.get_messages(entity, ids=[message_id])
        if not msgs or msgs[0] is None:
            raise ValueError(f"message {message_id} not found in chat")
        msg = msgs[0]
        if msg.media is None:
            raise ValueError("message has no media attachment")
        # Only photos and documents have a file; a location, poll or dice
        # roll has nothing to download.
        file_info = msg.file
        if file_info is None:
            raise ValueError(f"{type(msg.media).__name__} media has no downloadable file")
        # Refuse before the transfer when Telegram already reports the size.
        if file_info.size is not None and file_info.size > max_bytes:
            raise ValueError(f"media too large: {file_info.size} > max_bytes={max_bytes}")
        mime_type = file_info.mime_type
        filename = file_info.name
    async with holder.guard("read", chat_key=chat_key, chat_kind="read") as client:
        buf = io.BytesIO()
        await client.download_media(msg, file=buf)
        data = buf.getvalue()
    # The guard paid for one request, but the download sent one per part.
    holder.throttle.record_extra("read", max(0, _transfer_parts(len(data)) - 1))
    if not data:
        raise ValueError("download returned no data")
    if len(data) > max_bytes:
        raise ValueError(f"media too large: {len(data)} > max_bytes={max_bytes}")
    if not filename:
        filename = _DEFAULT_FILENAMES_BY_MIME.get(mime_type or "", "download.bin")
    return {
        "data": data,
        "size": len(data),
        "mime_type": mime_type or "application/octet-stream",
        "filename": filename,
        "media_type": type(msg.media).__name__,
    }


async def _download_media(holder: TelethonHolder, params: DownloadMediaParams) -> Dict[str, Any]:
    info = await download_media_bytes(holder, params.chat, params.message_id, params.max_bytes)
    return {
        "size": info["size"],
        "media_type": info["media_type"],
        "mime_type": info["mime_type"],
        "filename": info["filename"],
        "data_base64": base64.b64encode(info["data"]).decode("ascii"),
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


async def _set_reaction(holder: TelethonHolder, params: SetReactionParams) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_react": params.model_dump(mode="json")}
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        await client(
            SendReactionRequest(
                peer=entity,
                msg_id=params.message_id,
                reaction=[ReactionEmoji(emoticon=params.emoji)],
                big=params.big,
            )
        )
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


async def _remove_reaction(holder: TelethonHolder, params: RemoveReactionParams) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_unreact": params.model_dump(mode="json")}
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        await client(
            SendReactionRequest(
                peer=entity,
                msg_id=params.message_id,
                reaction=[],
            )
        )
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


async def _pin_message(holder: TelethonHolder, params: PinMessageParams) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_pin": params.model_dump(mode="json")}
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        await client(
            UpdatePinnedMessageRequest(
                peer=entity,
                id=params.message_id,
                silent=params.silent,
                pm_oneside=params.pm_oneside,
            )
        )
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


async def _unpin_message(holder: TelethonHolder, params: UnpinMessageParams) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_unpin": params.model_dump(mode="json")}
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        await client(
            UpdatePinnedMessageRequest(
                peer=entity,
                id=params.message_id,
                unpin=True,
            )
        )
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
# get_linked_chat — Telegram channels have linked discussion groups
# ---------------------------------------------------------------------------


class GetLinkedChatParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: str


async def _get_linked_chat(holder: TelethonHolder, params: GetLinkedChatParams) -> Dict[str, Any]:
    entity = await holder.resolve_entity(params.chat)
    _require_channel(entity, "get_linked_chat")
    async with holder.guard("get_full") as client:
        full = await client(GetFullChannelRequest(channel=entity))
    linked_id = getattr(full.full_chat, "linked_chat_id", None)
    if not linked_id:
        return {"linked_chat_id": None, "linked_entity": None}
    # Find the linked entity in the response's chats list.
    linked_entity = next((c for c in full.chats if getattr(c, "id", None) == linked_id), None)
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
        description=("Private invite hash (the part after t.me/+) or the full link."),
    )


def _extract_invite_hash(raw: str) -> str:
    s = raw.strip()
    for prefix in ("https://t.me/+", "http://t.me/+", "t.me/+", "@joinchat/", "+"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    if "joinchat/" in s:
        s = s.split("joinchat/", 1)[1]
    return s.strip("/")


async def _join_via_invite(holder: TelethonHolder, params: JoinViaInviteParams) -> Dict[str, Any]:
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
        description=(
            "Ban duration from now in seconds. 0 = permanent; Telegram also treats "
            "under 30 seconds or over 366 days as permanent."
        ),
    )


async def _ban_user(holder: TelethonHolder, params: BanUserParams) -> Dict[str, Any]:
    holder.assert_writeable()
    chat = await holder.resolve_entity(params.chat)
    _require_channel(chat, "ban_user")
    user = await holder.resolve_entity(params.user)
    if holder.dry_run:
        return {"dry_run": True, "would_ban": params.model_dump(mode="json")}
    # until_date is an absolute time. Passing the seconds themselves sent a
    # 1970 timestamp, which Telegram reads as "forever", so every timed ban
    # was permanent. Telegram also treats an end under 30 seconds or over
    # 366 days away as forever.
    until = (
        _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=params.until_seconds)
        if params.until_seconds
        else None
    )
    rights = ChatBannedRights(until_date=until, view_messages=True)
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


async def _unban_user(holder: TelethonHolder, params: UnbanUserParams) -> Dict[str, Any]:
    holder.assert_writeable()
    chat = await holder.resolve_entity(params.chat)
    _require_channel(chat, "unban_user")
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


async def _kick_user(holder: TelethonHolder, params: KickUserParams) -> Dict[str, Any]:
    """Ban-then-unban = kick without permanent ban."""
    holder.assert_writeable()
    chat = await holder.resolve_entity(params.chat)
    _require_channel(chat, "kick_user")
    user = await holder.resolve_entity(params.user)
    if holder.dry_run:
        return {"dry_run": True, "would_kick": params.model_dump(mode="json")}
    # One guard per request so each one is charged to the bucket.
    async with holder.guard("other") as client:
        await client(
            EditBannedRequest(
                channel=chat,
                participant=user,
                banned_rights=ChatBannedRights(until_date=0, view_messages=True),
            )
        )
    async with holder.guard("other") as client:
        await client(
            EditBannedRequest(
                channel=chat,
                participant=user,
                banned_rights=ChatBannedRights(until_date=0),
            )
        )
    return {"ok": True}


_register(
    Tool(
        name="kick_user",
        description=(
            "Kick a user from a chat (ban, then unban at once, so they can rejoin "
            "through an invite)."
        ),
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


async def _promote_user(holder: TelethonHolder, params: PromoteUserParams) -> Dict[str, Any]:
    holder.assert_writeable()
    granted = {name: True for name in _ADMIN_RIGHT_NAMES if getattr(params, name)}
    if not granted:
        # Telegram reads an empty set of admin rights as "remove the admin".
        raise ValueError(
            "promote_user needs at least one right set to true; "
            "use demote_user to remove admin rights"
        )
    chat = await holder.resolve_entity(params.chat)
    _require_channel(chat, "promote_user")
    user = await holder.resolve_entity(params.user)
    _require_user(user, "promote_user")
    if holder.dry_run:
        return {"dry_run": True, "would_promote": params.model_dump(mode="json")}
    rights = ChatAdminRights(**granted)
    async with holder.guard("other") as client:
        await client(
            EditAdminRequest(
                channel=chat,
                user_id=user,
                admin_rights=rights,
                rank=params.title or "",
            )
        )
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


async def _demote_user(holder: TelethonHolder, params: DemoteUserParams) -> Dict[str, Any]:
    holder.assert_writeable()
    chat = await holder.resolve_entity(params.chat)
    _require_channel(chat, "demote_user")
    user = await holder.resolve_entity(params.user)
    _require_user(user, "demote_user")
    if holder.dry_run:
        return {"dry_run": True, "would_demote": params.model_dump(mode="json")}
    rights = ChatAdminRights()  # all False = no admin powers
    async with holder.guard("other") as client:
        await client(
            EditAdminRequest(
                channel=chat,
                user_id=user,
                admin_rights=rights,
                rank="",
            )
        )
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
    question: str = Field(..., min_length=1, max_length=_MAX_POLL_QUESTION_LENGTH)
    options: List[Annotated[str, Field(min_length=1, max_length=_MAX_POLL_OPTION_LENGTH)]] = Field(
        ..., min_length=2, max_length=10
    )
    multiple_choice: bool = False
    quiz: bool = False
    correct_option: Optional[int] = Field(
        None, ge=0, description="0-based index of the correct answer for a quiz."
    )
    solution: Optional[str] = Field(
        None, max_length=200, description="Quiz explanation shown after answering."
    )


async def _create_poll(holder: TelethonHolder, params: CreatePollParams) -> Dict[str, Any]:
    holder.assert_writeable()
    if params.quiz and params.correct_option is None:
        raise ValueError("quiz polls require correct_option")
    if params.quiz and params.multiple_choice:
        # Telegram refuses a quiz that allows more than one answer.
        raise ValueError("a quiz cannot be multiple_choice")
    if not params.quiz and (params.correct_option is not None or params.solution):
        raise ValueError("correct_option and solution apply only to quiz polls")
    if params.correct_option is not None and params.correct_option >= len(params.options):
        raise ValueError(
            f"correct_option {params.correct_option} is out of range for "
            f"{len(params.options)} options"
        )
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_create_poll": params.model_dump(mode="json")}
    poll = Poll(
        id=_NEW_POLL_ID,
        hash=_NEW_POLL_HASH,
        question=TextWithEntities(text=params.question, entities=[]),
        answers=[
            InputPollAnswer(text=TextWithEntities(text=option, entities=[]))
            for option in params.options
        ],
        multiple_choice=params.multiple_choice,
        quiz=params.quiz,
    )
    media = InputMediaPoll(
        poll=poll,
        correct_answers=([params.correct_option] if params.correct_option is not None else None),
        solution=params.solution,
        # Telegram wants the entities whenever there is a solution.
        solution_entities=[] if params.solution is not None else None,
    )
    async with holder.guard("send", chat_key=_chat_key(params.chat)) as client:
        msg = await client.send_file(entity=entity, file=media)
    return _message_to_dict(msg)


_register(
    Tool(
        name="create_poll",
        description=(
            "Create a poll in a chat. Set quiz=true with correct_option for " "quiz-style polls."
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


async def _vote_poll(holder: TelethonHolder, params: VotePollParams) -> Dict[str, Any]:
    holder.assert_writeable()
    entity = await holder.resolve_entity(params.chat)
    if holder.dry_run:
        return {"dry_run": True, "would_vote": params.model_dump(mode="json")}
    # Telegram assigns each answer an opaque token; a vote must send those
    # tokens, so the poll is fetched to turn the indices into them.
    chat_key = _chat_key(params.chat)
    async with holder.guard("read", chat_key=chat_key, chat_kind="read") as client:
        msgs = await client.get_messages(entity, ids=[params.message_id])
    if not msgs or msgs[0] is None or msgs[0].poll is None:
        raise ValueError("message has no poll")
    answers = msgs[0].poll.poll.answers
    out_of_range = [i for i in params.options if not 0 <= i < len(answers)]
    if out_of_range:
        raise ValueError(
            f"option index {out_of_range} is out of range for a poll with "
            f"{len(answers)} options"
        )
    async with holder.guard("send", chat_key=chat_key) as client:
        await client(
            SendVoteRequest(
                peer=entity,
                msg_id=params.message_id,
                options=[answers[i].option for i in params.options],
            )
        )
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


async def _get_poll_results(holder: TelethonHolder, params: GetPollResultsParams) -> Dict[str, Any]:
    entity = await holder.resolve_entity(params.chat)
    async with holder.guard("read") as client:
        msgs = await client.get_messages(entity, ids=[params.message_id])
    if not msgs or msgs[0] is None or msgs[0].poll is None:
        raise ValueError("message has no poll")
    media = msgs[0].poll
    # The counts in the message can lag a vote by seconds; getPollResults
    # asks Telegram for the current ones.
    async with holder.guard("read") as client:
        fresh = await client(
            GetPollResultsRequest(
                peer=entity, msg_id=params.message_id, poll_hash=_NO_POLL_RESULTS_HASH
            )
        )
    results = _fresh_poll_results(fresh) or media.results
    # Results name an answer by its opaque token; report the index callers
    # pass to vote_poll instead.
    index_by_token = {answer.option: index for index, answer in enumerate(media.poll.answers)}
    return {
        "question": media.poll.question.text,
        "total_voters": results.total_voters,
        "results": [
            {
                "option_index": index_by_token.get(r.option),
                "voters": r.voters,
                "correct": r.correct,
                "chosen": r.chosen,
            }
            for r in results.results or []
        ],
    }


def _fresh_poll_results(updates: Any) -> Any:
    """The results in the UpdateMessagePoll getPollResults answers with, or
    None when the answer carries none."""
    for update in getattr(updates, "updates", None) or []:
        if isinstance(update, UpdateMessagePoll):
            return update.results
    return None


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


async def _throttle_status(holder: TelethonHolder, _: ThrottleStatusParams) -> Dict[str, Any]:
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


async def _account_health(holder: TelethonHolder, _: AccountHealthParams) -> Dict[str, Any]:
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
