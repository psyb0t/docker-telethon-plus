"""The login helper writes the session string where only its owner can read it."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.login as login_mod
from app.config import Config

SESSION_STRING = "1BQANOTAREALSESSION"
OWNER_ONLY = 0o600
WORLD_READABLE = 0o644


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_new_session_file_is_owner_only(tmp_path: Path) -> None:
    """Regression: the file was created with the default 0644."""
    target = tmp_path / "session"
    login_mod._write_session_file(str(target), SESSION_STRING)
    assert _mode(target) == OWNER_ONLY
    assert target.read_text() == SESSION_STRING


def test_an_existing_world_readable_file_is_tightened_and_replaced(tmp_path: Path) -> None:
    target = tmp_path / "session"
    target.write_text("old session string that is longer than the new one")
    target.chmod(WORLD_READABLE)

    login_mod._write_session_file(str(target), SESSION_STRING)

    assert _mode(target) == OWNER_ONLY
    assert target.read_text() == SESSION_STRING


def test_a_file_that_cannot_be_chmodded_is_still_written_with_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(fd: int, mode: int) -> None:
        raise PermissionError("not the owner")

    monkeypatch.setattr(os, "fchmod", refuse)
    target = tmp_path / "session"

    login_mod._write_session_file(str(target), SESSION_STRING)

    assert target.read_text() == SESSION_STRING
    assert "could not restrict" in capsys.readouterr().err


def test_login_writes_the_output_file_owner_only(
    base_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "session"
    monkeypatch.setenv("TELETHON_SESSION_OUTPUT_FILE", str(target))

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.session = SimpleNamespace(save=lambda: SESSION_STRING)

        async def start(self) -> None:
            return None

        async def get_me(self) -> Any:
            return SimpleNamespace(id=1, username="me")

        async def disconnect(self) -> None:
            return None

    monkeypatch.setattr(login_mod, "TelegramClient", FakeClient)
    monkeypatch.setattr(login_mod, "StringSession", lambda session: None)

    login_mod.run(Config.for_login())

    assert target.read_text() == SESSION_STRING
    assert _mode(target) == OWNER_ONLY
