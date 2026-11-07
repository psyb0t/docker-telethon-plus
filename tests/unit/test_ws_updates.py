"""/ws/updates end to end: the real app under uvicorn on a local port, with
the Telegram connection stubbed out, and a real websocket client."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.updates import CLOSE_SHUTDOWN, CLOSE_SLOW_SUBSCRIBER


class _Stack:
    def __init__(self, server: uvicorn.Server, holder: Any) -> None:
        self.server = server
        self.holder = holder

    @property
    def url(self) -> str:
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}/ws/updates"

    async def subscribers(self, count: int, timeout: float = 3.0) -> None:
        async def _wait() -> None:
            while len(self.holder.updates._subscribers) != count:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(_wait(), timeout)


@pytest.fixture
async def stack(base_env: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Stack]:
    monkeypatch.setenv("TELETHON_UPDATES_ENABLED", "true")
    monkeypatch.setenv("TELETHON_UPDATES_BUFFER_SIZE", "3")
    import app.server as server_mod
    from app.client import TelethonHolder
    from app.config import Config

    holders: list[Any] = []

    class OfflineHolder(TelethonHolder):
        def __init__(self, cfg: Config) -> None:
            super().__init__(cfg)
            holders.append(self)

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            await self.updates.stop()

    monkeypatch.setattr(server_mod, "TelethonHolder", OfflineHolder)
    app = server_mod.build_app(Config.from_env())
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    yield _Stack(server, holders[0])
    server.should_exit = True
    await asyncio.wait_for(task, 10)


async def test_a_client_that_falls_behind_is_closed_not_left_hanging(stack: _Stack) -> None:
    async with connect(stack.url) as ws:
        await stack.subscribers(1)
        # A burst bigger than the buffer (3) with no await in between, so the
        # route cannot drain the queue while it fills.
        for n in range(6):
            stack.holder.updates._dispatch({"type": "NewMessage", "n": n})
        received = []
        with pytest.raises(ConnectionClosed) as closed:
            while True:
                received.append(json.loads(await asyncio.wait_for(ws.recv(), 3))["n"])
        assert closed.value.rcvd is not None
        assert closed.value.rcvd.code == CLOSE_SLOW_SUBSCRIBER
        assert received == [1, 2]
    await stack.subscribers(0)


async def test_events_reach_a_client_that_keeps_up(stack: _Stack) -> None:
    async with connect(stack.url) as ws:
        await stack.subscribers(1)
        for n in range(10):
            stack.holder.updates._dispatch({"type": "NewMessage", "n": n})
            assert json.loads(await asyncio.wait_for(ws.recv(), 3))["n"] == n


async def test_a_client_that_leaves_frees_its_subscription_at_once(stack: _Stack) -> None:
    async with connect(stack.url):
        await stack.subscribers(1)
    # No event is dispatched: the route notices the disconnect by itself.
    await stack.subscribers(0)


async def test_shutdown_closes_open_clients(stack: _Stack) -> None:
    async with connect(stack.url) as ws:
        await stack.subscribers(1)
        await stack.holder.updates.stop()
        with pytest.raises(ConnectionClosed) as closed:
            await asyncio.wait_for(ws.recv(), 3)
        assert closed.value.rcvd is not None
        assert closed.value.rcvd.code == CLOSE_SHUTDOWN


async def test_a_wrong_token_refuses_the_websocket_handshake(
    base_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route closes before accepting, which reaches the client as an HTTP
    403 on the handshake, not as a close code: the docs say exactly that."""
    from websockets.exceptions import InvalidStatus

    monkeypatch.setenv("TELETHON_AUTH_KEY", "right-key")
    # base_env turns updates off, and a missing route is also refused with
    # 403; the right-key connect below proves the route exists.
    monkeypatch.setenv("TELETHON_UPDATES_ENABLED", "true")
    import app.server as server_mod
    from app.client import TelethonHolder
    from app.config import Config

    class OfflineHolder(TelethonHolder):
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(server_mod, "TelethonHolder", OfflineHolder)
    server = uvicorn.Server(
        uvicorn.Config(
            server_mod.build_app(Config.from_env()), host="127.0.0.1", port=0, log_level="error"
        )
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        with pytest.raises(InvalidStatus) as refused:
            async with connect(f"ws://127.0.0.1:{port}/ws/updates?token=wrong"):
                pass
        assert refused.value.response.status_code == 403
        async with connect(f"ws://127.0.0.1:{port}/ws/updates?token=right-key"):
            pass
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
