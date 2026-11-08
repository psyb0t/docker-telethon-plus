"""The MCP endpoint: the same tools as REST, over streamable HTTP."""

from __future__ import annotations

import base64
import json

import pytest
from conftest import LocalServer
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

EXPECTED_TOOLS = {
    # Original
    "get_me",
    "get_entity",
    "send_message",
    "get_messages",
    "get_dialogs",
    "forward_messages",
    "delete_messages",
    "edit_message",
    "mark_read",
    "send_file",
    "get_participants",
    "create_group",
    "delete_chat",
    "join_chat",
    "leave_chat",
    # Added in 1.1
    "bulk_resolve",
    "get_message",
    "download_media",
    "set_reaction",
    "remove_reaction",
    "pin_message",
    "unpin_message",
    "get_linked_chat",
    "join_via_invite",
    "ban_user",
    "unban_user",
    "kick_user",
    "promote_user",
    "demote_user",
    "create_poll",
    "vote_poll",
    "get_poll_results",
    "throttle_status",
    "account_health",
}


def _mcp_url(base_url: str) -> str:
    return f"{base_url}/mcp/"


def _extract_payload(call_result) -> dict | list:
    if call_result.structuredContent:
        return call_result.structuredContent
    assert call_result.content, "tool returned no content"
    text_block = call_result.content[0]
    text = getattr(text_block, "text", None)
    assert text, "tool returned non-text content"
    return json.loads(text)


@pytest.mark.asyncio
async def test_mcp_list_tools(base_url: str) -> None:
    async with streamablehttp_client(_mcp_url(base_url)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            names = {t.name for t in result.tools}
            assert names == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_mcp_get_me(base_url: str) -> None:
    async with streamablehttp_client(_mcp_url(base_url)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            call = await session.call_tool("get_me", {})
            assert not call.isError, call.content
            me = _extract_payload(call)
            assert isinstance(me, dict)
            assert isinstance(me["id"], int)


@pytest.mark.asyncio
async def test_mcp_send_and_delete(base_url: str, env: dict[str, str]) -> None:
    chat = env["TEST_CHAT"]
    text = "docker-telethon-plus-mcp-test"

    async with streamablehttp_client(_mcp_url(base_url)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            sent = await session.call_tool(
                "send_message",
                {"chat": chat, "text": text, "silent": True},
            )
            assert not sent.isError, sent.content
            sent_msg = _extract_payload(sent)
            assert isinstance(sent_msg, dict)
            assert sent_msg["text"] == text
            msg_id = sent_msg["id"]

            deleted = await session.call_tool(
                "delete_messages",
                {"chat": chat, "message_ids": [msg_id]},
            )
            assert not deleted.isError, deleted.content
            payload = _extract_payload(deleted)
            assert isinstance(payload, dict)
            assert payload["requested"] == 1


@pytest.mark.asyncio
async def test_mcp_validation_error_surfaces(base_url: str) -> None:
    async with streamablehttp_client(_mcp_url(base_url)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            call = await session.call_tool(
                "send_message",
                {"chat": "me"},
            )
            assert call.isError


@pytest.mark.asyncio
async def test_mcp_send_file_and_download_it_back(
    base_url: str, env: dict[str, str], local: LocalServer, media: dict[str, bytes]
) -> None:
    chat = env["TEST_CHAT"]
    local.files["clip.mp4"] = media["clip.mp4"]

    async with streamablehttp_client(_mcp_url(base_url)) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            sent = await session.call_tool(
                "send_file", {"chat": chat, "file_url": local.file_url("clip.mp4"), "silent": True}
            )
            assert not sent.isError, sent.content
            message_id = _extract_payload(sent)["id"]
            try:
                got = await session.call_tool(
                    "download_media", {"chat": chat, "message_id": message_id}
                )
                assert not got.isError, got.content
                payload = _extract_payload(got)
                assert base64.b64decode(payload["data_base64"]) == media["clip.mp4"]
                assert payload["mime_type"] == "video/mp4"
                assert payload["filename"] == "clip.mp4"
            finally:
                await session.call_tool(
                    "delete_messages", {"chat": chat, "message_ids": [message_id]}
                )
