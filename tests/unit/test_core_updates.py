"""Webhook delivery: every refused event is logged."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List

import httpx
import pytest

import app.updates as updates_mod
from app.updates import UpdateBroker

WEBHOOK_URL = "https://hooks.example.invalid/in"
WAIT_SECONDS = 3.0
# Event-loop turns for the worker to finish posting and logging once its queue
# is empty; the stub transport answers without any real I/O.
SETTLE_TURNS = 10


class _FakeClient:
    def add_event_handler(self, callback: Any, builder: Any) -> None:
        return None


async def _deliver_one_event(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    """Run the real webhook worker against a stub endpoint answering
    `status_code`, until it has handled one event."""
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    def stubbed_client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(updates_mod.httpx, "AsyncClient", stubbed_client)
    broker = UpdateBroker(enabled=True, post_to_url=WEBHOOK_URL, post_to_timeout=1, buffer_size=4)
    broker.attach(_FakeClient())  # type: ignore[arg-type]
    try:
        broker._dispatch({"type": "NewMessage"})
        queue = broker._webhook_queue
        assert queue is not None

        async def drained() -> None:
            while not queue.empty():
                await asyncio.sleep(0)
            # A few more turns: the worker logs after the response arrives.
            for _ in range(SETTLE_TURNS):
                await asyncio.sleep(0)

        await asyncio.wait_for(drained(), WAIT_SECONDS)
    finally:
        await broker.stop()


def _webhook_warnings(caplog: pytest.LogCaptureFixture) -> List[logging.LogRecord]:
    return [
        r for r in caplog.records if r.name == updates_mod.__name__ and r.levelno == logging.WARNING
    ]


@pytest.mark.parametrize("status_code", [301, 302, 400, 401, 404, 429, 500, 503])
async def test_a_non_2xx_webhook_response_is_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    status_code: int,
) -> None:
    """Regression: only status >= 500 was logged, so a 3xx or 4xx dropped the
    event without a trace."""
    with caplog.at_level(logging.WARNING):
        await _deliver_one_event(monkeypatch, status_code)
    (record,) = _webhook_warnings(caplog)
    assert record.args is not None
    assert status_code in record.args


@pytest.mark.parametrize("status_code", [200, 201, 202, 204])
async def test_a_2xx_webhook_response_is_not_logged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    status_code: int,
) -> None:
    with caplog.at_level(logging.WARNING):
        await _deliver_one_event(monkeypatch, status_code)
    assert _webhook_warnings(caplog) == []
