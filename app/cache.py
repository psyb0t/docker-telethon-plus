"""Persistent entity cache.

Telethon's session file already caches resolved entities, but a stale
session, a regenerated string session, or a freshly-pulled container
forces every `resolveUsername` call again — that's the operation most
likely to trigger long FLOOD_WAITs.

This cache is a simple JSON file keyed by both `@username` and numeric
ID, holding everything needed to skip the resolveUsername call and feed
Telethon directly via `client.get_entity(id)` (which works once the
entity is in Telethon's in-memory entity DB after a prior resolve).

The cache is best-effort: corrupt file = start fresh, write failures =
log and continue.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from threading import RLock
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class EntityCache:
    """JSON-backed cache. Thread-safe (but used from a single asyncio loop)."""

    def __init__(self, path: str, ttl_seconds: int, enabled: bool = True) -> None:
        self._path = path
        self._ttl = ttl_seconds
        self._enabled = enabled
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = RLock()
        if enabled:
            self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._data = data
                log.info("entity cache loaded: %d entries from %s", len(data), self._path)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("entity cache load failed (%s) — starting fresh", exc)
            self._data = {}

    def _save(self) -> None:
        if not self._enabled:
            return
        try:
            directory = os.path.dirname(self._path) or "."
            os.makedirs(directory, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", dir=directory, delete=False, encoding="utf-8"
            ) as tmp:
                json.dump(self._data, tmp)
                tmp_path = tmp.name
            os.replace(tmp_path, self._path)
        except OSError as exc:
            log.warning("entity cache save failed: %s", exc)

    @staticmethod
    def _normalize_key(raw: str) -> str:
        s = raw.strip().lower()
        if s.startswith("@"):
            s = s[1:]
        return s

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        if not self._enabled:
            return None
        nk = self._normalize_key(key)
        with self._lock:
            entry = self._data.get(nk)
        if entry is None:
            return None
        if self._ttl > 0 and (time.time() - entry.get("cached_at", 0)) > self._ttl:
            with self._lock:
                self._data.pop(nk, None)
            return None
        return entry

    def put(self, raw_key: str, entity: Any) -> None:
        """Store entity data + secondary index by numeric ID."""
        if not self._enabled:
            return
        record = self._entity_to_record(entity)
        if record is None:
            return
        record["cached_at"] = int(time.time())

        keys = {self._normalize_key(raw_key)}
        eid = record.get("id")
        if eid is not None:
            keys.add(str(eid))
        uname = record.get("username")
        if uname:
            keys.add(self._normalize_key(uname))

        with self._lock:
            for k in keys:
                self._data[k] = record
            self._save()

    def invalidate(self, raw_key: str) -> None:
        if not self._enabled:
            return
        nk = self._normalize_key(raw_key)
        with self._lock:
            entry = self._data.pop(nk, None)
            if entry is None:
                return
            # also drop secondary indices pointing at same entity
            eid = entry.get("id")
            if eid is not None:
                self._data.pop(str(eid), None)
            uname = entry.get("username")
            if uname:
                self._data.pop(self._normalize_key(uname), None)
            self._save()

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"entries": len(self._data)}

    @staticmethod
    def _entity_to_record(entity: Any) -> Optional[Dict[str, Any]]:
        eid = getattr(entity, "id", None)
        if eid is None:
            return None
        rec: Dict[str, Any] = {
            "id": int(eid),
            "type": type(entity).__name__,
        }
        for attr in ("username", "first_name", "last_name", "title", "phone", "bot"):
            val = getattr(entity, attr, None)
            if val is not None:
                rec[attr] = val
        # access_hash is mtproto-private — Telethon won't accept it from us
        # for input_peer construction across restarts safely, so we don't
        # store it. We rely on Telethon's session for that and use the cache
        # purely as a "we've seen this handle before, skip resolve" hint.
        return rec
