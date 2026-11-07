"""EntityCache: access hashes survive a restart, the file belongs to one
account, and a damaged file never stops startup."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from app.cache import ACCESS_HASH_FIELD, ACCOUNT_ID_KEY, EntityCache

ACCOUNT = 4242
OTHER_ACCOUNT = 7
ACCESS_HASH = 987654321987654321
TTL = 3600


def _entity(
    eid: int,
    *,
    kind: str = "Channel",
    username: Optional[str] = None,
    access_hash: Any = ACCESS_HASH,
    **extra: Any,
) -> MagicMock:
    e = MagicMock()
    e.id = eid
    e.username = username
    e.title = None
    e.first_name = None
    e.last_name = None
    e.phone = None
    e.bot = False
    e.access_hash = access_hash
    e.__class__.__name__ = kind
    for name, value in extra.items():
        setattr(e, name, value)
    return e


def _write(path: Path, content: Any) -> None:
    path.write_text(json.dumps(content))


# ---------- access_hash ------------------------------------------------------


def test_the_access_hash_is_cached_and_survives_a_reload(tmp_path: Path) -> None:
    """Regression: only id and type were stored, so after a restart there was
    nothing to build an InputPeer from and the cache saved no resolve."""
    path = str(tmp_path / "c.json")
    EntityCache(path, TTL).put("@chan", _entity(5, username="chan"))

    hit = EntityCache(path, TTL).get("@chan")
    assert hit is not None
    assert hit[ACCESS_HASH_FIELD] == ACCESS_HASH


@pytest.mark.parametrize("access_hash", [None, "not-an-int", MagicMock()])
def test_an_entity_without_an_integer_hash_is_cached_without_one(
    tmp_path: Path, access_hash: Any
) -> None:
    c = EntityCache(str(tmp_path / "c.json"), TTL)
    c.put("@chan", _entity(5, username="chan", access_hash=access_hash))
    hit = c.get("@chan")
    assert hit is not None
    assert ACCESS_HASH_FIELD not in hit


def test_a_min_entitys_hash_is_not_cached(tmp_path: Path) -> None:
    """Telethon refuses a min entity's hash for anything but downloads."""
    c = EntityCache(str(tmp_path / "c.json"), TTL)
    c.put("@chan", _entity(5, username="chan", min=True))
    hit = c.get("@chan")
    assert hit is not None
    assert ACCESS_HASH_FIELD not in hit


# ---------- account binding ---------------------------------------------------


def test_the_account_id_is_written_to_the_file(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    c = EntityCache(str(path), TTL)
    c.bind_account(ACCOUNT)
    c.put("@chan", _entity(5, username="chan"))
    assert json.loads(path.read_text())[ACCOUNT_ID_KEY] == ACCOUNT


def test_the_account_id_is_not_an_entry(tmp_path: Path) -> None:
    path = str(tmp_path / "c.json")
    c = EntityCache(path, TTL)
    c.bind_account(ACCOUNT)
    c.put("@chan", _entity(5, username="chan"))

    reloaded = EntityCache(path, TTL)
    assert reloaded.get(ACCOUNT_ID_KEY) is None
    # One entity, indexed by handle and by id.
    assert reloaded.stats() == {"entries": 2}


def test_the_same_account_keeps_its_entries(tmp_path: Path) -> None:
    path = str(tmp_path / "c.json")
    first = EntityCache(path, TTL)
    first.bind_account(ACCOUNT)
    first.put("@chan", _entity(5, username="chan"))

    second = EntityCache(path, TTL)
    second.bind_account(ACCOUNT)
    assert second.get("@chan") is not None


def test_another_account_finds_the_cache_cleared(tmp_path: Path) -> None:
    """Access hashes are per account: one account's hash is useless to
    another, and would be used against the wrong peer."""
    path = tmp_path / "c.json"
    first = EntityCache(str(path), TTL)
    first.bind_account(ACCOUNT)
    first.put("@chan", _entity(5, username="chan"))

    second = EntityCache(str(path), TTL)
    second.bind_account(OTHER_ACCOUNT)
    assert second.get("@chan") is None
    assert second.stats() == {"entries": 0}
    # The cleared state is what reaches the disk.
    on_disk = json.loads(path.read_text())
    assert on_disk == {ACCOUNT_ID_KEY: OTHER_ACCOUNT}


def test_a_file_without_an_account_id_is_cleared_on_bind(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    _write(path, {"chan": {"id": 5, "type": "Channel", "username": "chan", "cached_at": 1}})

    c = EntityCache(str(path), TTL)
    assert c.stats() == {"entries": 1}
    c.bind_account(ACCOUNT)
    assert c.stats() == {"entries": 0}
    assert json.loads(path.read_text()) == {ACCOUNT_ID_KEY: ACCOUNT}


def test_binding_an_empty_cache_does_not_touch_the_disk(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    EntityCache(str(path), TTL).bind_account(ACCOUNT)
    assert not path.exists()


def test_a_disabled_cache_ignores_bind_account(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    c = EntityCache(str(path), TTL, enabled=False)
    c.bind_account(ACCOUNT)
    assert not path.exists()


# ---------- a damaged file ---------------------------------------------------


@pytest.mark.parametrize("raw", [b"\xff\xfe\x00{", b'{"a": "\xc3\x28"}'])
def test_a_file_with_invalid_utf8_starts_fresh(tmp_path: Path, raw: bytes) -> None:
    """Regression: UnicodeDecodeError was not caught, so startup crashed."""
    path = tmp_path / "c.json"
    path.write_bytes(raw)
    assert EntityCache(str(path), TTL).stats() == {"entries": 0}


def test_entries_that_are_not_objects_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    _write(
        path,
        {
            "good": {"id": 1, "type": "User", "cached_at": 1},
            "number": 5,
            "list": [1, 2],
            "nothing": None,
            "text": "x",
        },
    )
    c = EntityCache(str(path), 0)
    assert c.stats() == {"entries": 1}
    assert c.get("good") is not None
    assert c.get("number") is None
    assert c.get("list") is None


def test_a_non_object_account_id_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    _write(path, {ACCOUNT_ID_KEY: {"id": 1}, "good": {"id": 1, "type": "User", "cached_at": 1}})
    c = EntityCache(str(path), 0)
    assert c.stats() == {"entries": 1}
    c.bind_account(ACCOUNT)
    assert c.stats() == {"entries": 0}
