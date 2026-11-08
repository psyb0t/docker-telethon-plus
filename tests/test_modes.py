"""The safety switches and the entity cache, on the real account. Each test
restarts the one container with the settings it needs (see conftest.Server)."""

from __future__ import annotations

import json

import httpx
import pytest
from conftest import Server, marker, ok
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


def test_read_only_refuses_every_write_and_keeps_reads(server: Server, env: dict[str, str]) -> None:
    url = server.ensure(TELETHON_READ_ONLY="true")
    with httpx.Client(base_url=url, timeout=60.0) as http:
        assert ok(http.get("/api/me"))["id"]
        assert ok(http.get("/api/throttle/status"))["read_only"] is True

        send = http.post("/api/messages", json={"chat": env["TEST_CHAT"], "text": marker("ro")})
        assert send.status_code == 403
        create = http.post("/api/chats", json={"title": "never created"})
        assert create.status_code == 403


@pytest.mark.asyncio
async def test_read_only_refuses_writes_over_mcp(server: Server, env: dict[str, str]) -> None:
    url = server.ensure(TELETHON_READ_ONLY="true")
    async with streamablehttp_client(f"{url}/mcp/") as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            call = await session.call_tool("send_message", {"chat": env["TEST_CHAT"], "text": "x"})
            assert call.isError
            assert json.loads(call.content[0].text)["error"] == "read_only"


def test_dry_run_answers_what_it_would_do_and_sends_nothing(
    server: Server, env: dict[str, str]
) -> None:
    chat = env["TEST_CHAT"]
    text = marker("dry")
    url = server.ensure(TELETHON_DRY_RUN="true")
    with httpx.Client(base_url=url, timeout=60.0) as http:
        answer = ok(http.post("/api/messages", json={"chat": chat, "text": text}))
        assert answer["dry_run"] is True
        assert answer["would_send"]["text"] == text

        found = ok(http.get("/api/messages", params={"chat": chat, "search": text.split()[-1]}))
        assert found == []


def test_the_entity_cache_survives_a_restart(server: Server, env: dict[str, str]) -> None:
    with httpx.Client(base_url=server.ensure(), timeout=60.0) as http:
        ok(http.get("/api/entities", params={"chat": env["TEST_PUBLIC_CHANNEL"]}))
        assert ok(http.get("/api/throttle/status"))["cache"]["entries"] > 0

    # Any different setting restarts the container; the cache volume stays.
    with httpx.Client(
        base_url=server.ensure(TELETHON_METRICS_ENABLED="false"), timeout=60.0
    ) as http:
        status = ok(http.get("/api/throttle/status"))
        assert status["cache"]["entries"] > 0
        assert status["throttle"]["buckets"]["resolve_username"]["used"] == 0
        ok(http.get("/api/entities", params={"chat": env["TEST_PUBLIC_CHANNEL"]}))
        # Served from the cache: no username resolution was charged.
        assert (
            ok(http.get("/api/throttle/status"))["throttle"]["buckets"]["resolve_username"]["used"]
            == 0
        )
