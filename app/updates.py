"""Incoming-event broker.

Telethon fires events for new messages, edits, deletes, chat actions,
etc. This module fans them out to:

- WebSocket clients connected to /ws/updates (in-process, low latency)
- An optional outbound HTTP POST URL (TELETHON_POST_TO_URL), so external
  workers can subscribe without holding a persistent connection.

Both paths are best-effort: a slow client/webhook does not block Telethon.
Events queue per-subscriber up to `buffer_size`, then are dropped (with a
warning log) — we never apply backpressure to the upstream MTProto loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, Dict, List, Optional, Set

import httpx
from telethon import TelegramClient, events

log = logging.getLogger(__name__)


def _event_to_dict(event: Any) -> Dict[str, Any]:
    """Render a Telethon event as a plain JSON-safe dict.

    Conservative: emits a small known-shape payload, no raw TL objects.
    Subscribers can hit /api/messages or /api/entities for full detail.
    """
    payload: Dict[str, Any] = {"type": type(event).__name__}

    msg = getattr(event, "message", None)
    if msg is not None:
        payload["message"] = {
            "id": getattr(msg, "id", None),
            "date": msg.date.isoformat() if getattr(msg, "date", None) else None,
            "text": getattr(msg, "message", "") or "",
            "out": getattr(msg, "out", False),
            "sender_id": getattr(msg, "sender_id", None),
            "chat_id": getattr(event, "chat_id", None),
            "reply_to_msg_id": (
                msg.reply_to.reply_to_msg_id
                if getattr(msg, "reply_to", None) else None
            ),
            "media": getattr(msg, "media", None) is not None,
            "media_type": (
                type(msg.media).__name__ if getattr(msg, "media", None) else None
            ),
        }

    # Specifics by event subclass
    for attr in ("chat_id", "user_id", "deleted_ids", "edited"):
        val = getattr(event, attr, None)
        if val is not None and not callable(val):
            payload[attr] = val if not hasattr(val, "isoformat") else val.isoformat()

    return payload


class UpdateBroker:
    """Fan-out hub for Telethon events.

    Lifecycle:
      - Instantiate once during app startup.
      - Call `attach(client)` after the TelegramClient is connected.
      - Use `subscribe()` to get a per-connection queue (WS routes do this).
      - Call `stop()` during shutdown to cancel the webhook worker.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        post_to_url: str,
        post_to_timeout: float,
        buffer_size: int,
    ) -> None:
        self._enabled = enabled
        self._post_to_url = post_to_url
        self._post_to_timeout = post_to_timeout
        self._buffer_size = buffer_size
        self._subscribers: Set[asyncio.Queue[Dict[str, Any]]] = set()
        self._webhook_queue: Optional[asyncio.Queue[Dict[str, Any]]] = None
        self._webhook_task: Optional[asyncio.Task[None]] = None
        self._client: Optional[TelegramClient] = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def attach(self, client: TelegramClient) -> None:
        if not self._enabled:
            return
        self._client = client

        @client.on(events.NewMessage)
        async def _on_new(event: Any) -> None:
            self._dispatch(_event_to_dict(event))

        @client.on(events.MessageEdited)
        async def _on_edit(event: Any) -> None:
            self._dispatch(_event_to_dict(event))

        @client.on(events.MessageDeleted)
        async def _on_delete(event: Any) -> None:
            self._dispatch(_event_to_dict(event))

        @client.on(events.ChatAction)
        async def _on_chat_action(event: Any) -> None:
            self._dispatch(_event_to_dict(event))

        if self._post_to_url:
            self._webhook_queue = asyncio.Queue(maxsize=self._buffer_size)
            self._webhook_task = asyncio.create_task(
                self._webhook_worker(), name="updates-webhook"
            )
            log.info("update webhook enabled → %s", self._post_to_url)

    def _dispatch(self, payload: Dict[str, Any]) -> None:
        # WS subscribers
        dead: List[asyncio.Queue[Dict[str, Any]]] = []
        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                log.warning("update subscriber dropped event (buffer full)")
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)

        # Webhook queue
        if self._webhook_queue is not None:
            try:
                self._webhook_queue.put_nowait(payload)
            except asyncio.QueueFull:
                log.warning("webhook queue full — dropping event")

    async def _webhook_worker(self) -> None:
        assert self._webhook_queue is not None
        async with httpx.AsyncClient(timeout=self._post_to_timeout) as http:
            while True:
                payload = await self._webhook_queue.get()
                try:
                    resp = await http.post(self._post_to_url, json=payload)
                    if resp.status_code >= 500:
                        log.warning(
                            "webhook %s returned %d — event dropped",
                            self._post_to_url,
                            resp.status_code,
                        )
                except httpx.HTTPError as exc:
                    log.warning("webhook delivery failed: %s", exc)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    log.exception("webhook worker crashed handling event")

    def subscribe(self) -> asyncio.Queue[Dict[str, Any]]:
        q: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=self._buffer_size)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    async def stop(self) -> None:
        if self._webhook_task is not None:
            self._webhook_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._webhook_task
            self._webhook_task = None
        self._subscribers.clear()


def event_payload_json(payload: Dict[str, Any]) -> str:
    """Stable JSON encoder used by WS send-text path."""
    return json.dumps(payload, separators=(",", ":"), default=str)
