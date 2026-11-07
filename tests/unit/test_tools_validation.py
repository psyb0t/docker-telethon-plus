"""Inputs Telegram would refuse are refused as ValueError (400) before any
request, instead of a TypeError or RPC error that surfaced as a 500."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from telethon.tl.types import Channel, ChatAdminRights, User


def _channel() -> Channel:
    return Channel(
        id=1001,
        title="test channel",
        photo=None,
        date=None,
        access_hash=1,
        megagroup=True,
    )


def _user() -> User:
    return User(id=42, access_hash=2, username="testuser")


@pytest.mark.parametrize("tool", ["promote_user", "demote_user"])
async def test_admin_changes_need_a_user_not_a_channel(holder: Any, tool: str) -> None:
    from app.tools import REGISTRY

    holder._client.get_entity = AsyncMock(side_effect=[_channel(), _channel()])
    body: dict[str, Any] = {"chat": "@testgroup", "user": "@somechannel"}
    if tool == "promote_user":
        body["pin_messages"] = True
    params = REGISTRY[tool].params_model.model_validate(body)
    with pytest.raises(ValueError, match="needs a user"):
        await REGISTRY[tool].handler(holder, params)
    holder._client.assert_not_awaited()


async def test_promote_user_still_promotes_a_user(holder: Any) -> None:
    from app.tools import REGISTRY

    holder._client.get_entity = AsyncMock(side_effect=[_channel(), _user()])
    holder._client.return_value = None
    params = REGISTRY["promote_user"].params_model.model_validate(
        {"chat": "@testgroup", "user": "@testuser", "pin_messages": True}
    )
    assert await REGISTRY["promote_user"].handler(holder, params) == {"ok": True}
    rights = holder._client.await_args.args[0].admin_rights
    assert isinstance(rights, ChatAdminRights) and rights.pin_messages


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"quiz": True, "correct_option": 0, "multiple_choice": True}, "cannot be multiple_choice"),
        ({"correct_option": 0}, "only to quiz polls"),
        ({"solution": "because"}, "only to quiz polls"),
    ],
)
async def test_poll_combinations_telegram_refuses_are_rejected(
    holder: Any, body: dict[str, Any], message: str
) -> None:
    from app.tools import REGISTRY

    params = REGISTRY["create_poll"].params_model.model_validate(
        {"chat": "@testgroup", "question": "q?", "options": ["a", "b"], **body}
    )
    with pytest.raises(ValueError, match=message):
        await REGISTRY["create_poll"].handler(holder, params)
    holder._client.assert_not_awaited()


def test_an_empty_poll_option_is_a_validation_error() -> None:
    from pydantic import ValidationError

    from app.tools import REGISTRY

    with pytest.raises(ValidationError):
        REGISTRY["create_poll"].params_model.model_validate(
            {"chat": "@testgroup", "question": "q?", "options": ["a", ""]}
        )


async def test_an_empty_download_is_not_sent(
    base_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Any, make_entity: Any
) -> None:
    import app.tools as tools
    from app.client import TelethonHolder
    from app.config import Config

    monkeypatch.setenv("TELETHON_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    holder = TelethonHolder(Config.from_env())
    holder._client = AsyncMock()
    holder._client.get_entity = AsyncMock(return_value=make_entity(1))
    real = httpx.AsyncClient
    monkeypatch.setattr(
        tools.httpx,
        "AsyncClient",
        lambda **kw: real(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"")), **kw
        ),
    )
    params = tools.REGISTRY["send_file"].params_model.model_validate(
        {"chat": "@test", "file_url": "https://files.example.invalid/a.bin"}
    )
    with pytest.raises(ValueError, match="empty file"):
        await tools.REGISTRY["send_file"].handler(holder, params)
    holder._client.send_file.assert_not_awaited()
    assert list((tmp_path / "downloads").iterdir()) == []
