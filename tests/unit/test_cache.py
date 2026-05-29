"""EntityCache unit tests — persistence, TTL, key normalization."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.cache import EntityCache


def _ent(eid: int, username: str | None = None, title: str | None = None) -> MagicMock:
    e = MagicMock()
    e.id = eid
    e.username = username
    e.title = title
    e.first_name = None
    e.last_name = None
    e.phone = None
    e.bot = False
    e.__class__.__name__ = "Channel" if title else "User"
    return e


def test_put_then_get_roundtrip(tmp_path: Path) -> None:
    c = EntityCache(str(tmp_path / "c.json"), ttl_seconds=3600)
    c.put("@somechan", _ent(12345, username="somechan"))
    hit = c.get("@somechan")
    assert hit is not None
    assert hit["id"] == 12345
    assert hit["username"] == "somechan"


def test_key_normalization_strips_at_and_lowercases(tmp_path: Path) -> None:
    c = EntityCache(str(tmp_path / "c.json"), ttl_seconds=3600)
    c.put("@SomeChan", _ent(42, username="somechan"))
    assert c.get("@SomeChan") is not None
    assert c.get("@somechan") is not None
    assert c.get("somechan") is not None
    assert c.get("SOMECHAN") is not None


def test_get_by_numeric_id_after_put_by_username(tmp_path: Path) -> None:
    c = EntityCache(str(tmp_path / "c.json"), ttl_seconds=3600)
    c.put("@foo", _ent(999, username="foo"))
    assert c.get("999") is not None


def test_ttl_expiry_removes_entry(tmp_path: Path) -> None:
    c = EntityCache(str(tmp_path / "c.json"), ttl_seconds=1)
    c.put("@stale", _ent(1, username="stale"))
    assert c.get("@stale") is not None
    # Force an expired timestamp.
    c._data["stale"]["cached_at"] = int(time.time()) - 5
    assert c.get("@stale") is None


def test_zero_ttl_means_no_expiry(tmp_path: Path) -> None:
    c = EntityCache(str(tmp_path / "c.json"), ttl_seconds=0)
    c.put("@forever", _ent(1, username="forever"))
    c._data["forever"]["cached_at"] = 0
    assert c.get("@forever") is not None


def test_disabled_cache_skips_all_ops(tmp_path: Path) -> None:
    path = str(tmp_path / "c.json")
    c = EntityCache(path, ttl_seconds=3600, enabled=False)
    c.put("@x", _ent(1, username="x"))
    assert c.get("@x") is None
    assert not os.path.exists(path), "disabled cache must not touch disk"


def test_persistence_across_instances(tmp_path: Path) -> None:
    path = str(tmp_path / "c.json")
    c1 = EntityCache(path, ttl_seconds=3600)
    c1.put("@persistent", _ent(7, username="persistent"))

    c2 = EntityCache(path, ttl_seconds=3600)
    assert c2.get("@persistent") is not None
    assert c2.get("7") is not None


def test_corrupt_file_does_not_explode(tmp_path: Path) -> None:
    path = str(tmp_path / "c.json")
    Path(path).write_text("{ this is not json")
    c = EntityCache(path, ttl_seconds=3600)
    assert c.stats() == {"entries": 0}


def test_invalidate_drops_all_indices(tmp_path: Path) -> None:
    c = EntityCache(str(tmp_path / "c.json"), ttl_seconds=3600)
    c.put("@gone", _ent(55, username="gone"))
    assert c.get("@gone") is not None
    c.invalidate("@gone")
    assert c.get("@gone") is None
    assert c.get("55") is None
    assert c.get("gone") is None


def test_save_is_atomic(tmp_path: Path) -> None:
    """The replace pattern means a partial write can never be observed."""
    path = str(tmp_path / "c.json")
    c = EntityCache(path, ttl_seconds=3600)
    c.put("@a", _ent(1, username="a"))
    c.put("@b", _ent(2, username="b"))
    # JSON parse what's on disk — should always be valid.
    on_disk = json.loads(Path(path).read_text())
    assert "a" in on_disk and "b" in on_disk


def test_entity_without_id_is_silently_skipped(tmp_path: Path) -> None:
    c = EntityCache(str(tmp_path / "c.json"), ttl_seconds=3600)
    weird = MagicMock()
    weird.id = None
    c.put("@noid", weird)
    assert c.get("@noid") is None
