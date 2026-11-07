"""Persistent entity cache.

Telethon's session file already caches resolved entities, but a stale
session, a regenerated string session, or a freshly-pulled container
forces every `resolveUsername` call again — that's the operation most
likely to trigger long FLOOD_WAITs.

This cache is a simple JSON file keyed by both `@username` and numeric
ID. Each record carries the entity's id, type and access_hash, which is
enough to skip the resolveUsername call and build an InputPeer for
`client.get_entity(...)`. The StringSession keeps no entities across a
restart, so an id alone would not resolve.

Access hashes are only valid for the account that saw them, so the file also
records the account id (bind_account); a file from another account, or one
that predates the account id, is dropped.

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

# Record field holding the entity's access_hash.
ACCESS_HASH_FIELD = "access_hash"
# Top-level file key holding the account id. Telegram usernames start with a
# letter, so it cannot collide with a cache key.
ACCOUNT_ID_KEY = "_account_id"


class EntityCache:
    """JSON-backed cache. Thread-safe (but used from a single asyncio loop)."""

    def __init__(self, path: str, ttl_seconds: int, enabled: bool = True) -> None:
        self._path = path
        self._ttl = ttl_seconds
        self._enabled = enabled
        self._data: Dict[str, Dict[str, Any]] = {}
        # Account the loaded entries belong to; None for a file without one.
        self._account_id: Optional[int] = None
        self._lock = RLock()
        if enabled:
            self._load()

    def _load(self) -> None:
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        # json.JSONDecodeError and UnicodeDecodeError are both ValueErrors.
        except (OSError, ValueError) as exc:
            log.warning("entity cache load failed (%s) — starting fresh", exc)
            self._data = {}
            return
        if not isinstance(data, dict):
            return
        account_id = data.pop(ACCOUNT_ID_KEY, None)
        self._account_id = account_id if isinstance(account_id, int) else None
        self._data = {k: v for k, v in data.items() if isinstance(v, dict)}
        skipped = len(data) - len(self._data)
        if skipped:
            log.warning("entity cache: skipped %d malformed entries in %s", skipped, self._path)
        log.info("entity cache loaded: %d entries from %s", len(self._data), self._path)

    def bind_account(self, account_id: int) -> None:
        """Tie the cache to one account, dropping entries cached for another.

        A file that records a different account, or none (written before the
        account id was stored), is cleared: access hashes are per account and
        another account's would fail or point at the wrong peer.
        """
        if not self._enabled:
            return
        with self._lock:
            if self._account_id == account_id:
                return
            dropped = len(self._data)
            self._data = {}
            self._account_id = account_id
            if dropped == 0:
                return
            log.warning(
                "entity cache belonged to another account or predates account binding;"
                " dropped %d entries",
                dropped,
            )
            self._save()

    def _save(self) -> None:
        if not self._enabled:
            return
        try:
            directory = os.path.dirname(self._path) or "."
            os.makedirs(directory, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", dir=directory, delete=False, encoding="utf-8"
            ) as tmp:
                json.dump(self._file_content(), tmp)
                tmp_path = tmp.name
            os.replace(tmp_path, self._path)
        except OSError as exc:
            log.warning("entity cache save failed: %s", exc)

    def _file_content(self) -> Dict[str, Any]:
        if self._account_id is None:
            return dict(self._data)
        return {**self._data, ACCOUNT_ID_KEY: self._account_id}

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
        # A min entity's hash only works for downloading files, and Telethon
        # refuses it for anything else, so it is not worth caching.
        access_hash = getattr(entity, ACCESS_HASH_FIELD, None)
        if isinstance(access_hash, int) and getattr(entity, "min", False) is not True:
            rec[ACCESS_HASH_FIELD] = access_hash
        return rec
