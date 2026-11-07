"""TELETHON_PROXY reaches TelegramClient as the proxy dict Telethon takes."""

from __future__ import annotations

from typing import Any

import pytest

from app.config import parse_proxy


def test_an_empty_proxy_is_none() -> None:
    assert parse_proxy("") is None
    assert parse_proxy("   ") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "socks5://user:p%40ss@proxy.example.invalid:1080",
            {
                "proxy_type": "socks5",
                "addr": "proxy.example.invalid",
                "port": 1080,
                "username": "user",
                "password": "p@ss",
                "rdns": True,
            },
        ),
        (
            "HTTP://10.0.0.1:3128",
            {
                "proxy_type": "http",
                "addr": "10.0.0.1",
                "port": 3128,
                "username": None,
                "password": None,
                "rdns": True,
            },
        ),
        (
            "socks4://proxy.example.invalid:1080",
            {
                "proxy_type": "socks4",
                "addr": "proxy.example.invalid",
                "port": 1080,
                "username": None,
                "password": None,
                "rdns": True,
            },
        ),
    ],
)
def test_a_proxy_url_is_parsed(raw: str, expected: dict[str, Any]) -> None:
    assert parse_proxy(raw) == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("mtproto://proxy.example.invalid:443", "must start with"),
        ("proxy.example.invalid:1080", "must start with"),
        ("socks5://proxy.example.invalid", "host:port"),
        ("socks5://proxy.example.invalid:notaport", "invalid port"),
        ("socks5://:1080", "host:port"),
    ],
)
def test_a_bad_proxy_stops_startup(raw: str, message: str) -> None:
    with pytest.raises(RuntimeError, match=message):
        parse_proxy(raw)


def test_config_carries_the_parsed_proxy(base_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import Config

    monkeypatch.setenv("TELETHON_PROXY", "socks5://proxy.example.invalid:1080")
    assert Config.from_env().proxy == parse_proxy("socks5://proxy.example.invalid:1080")


async def test_the_client_connects_through_the_proxy(
    base_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: TELETHON_PROXY was read into the config but never passed
    to TelegramClient, so the client always connected directly."""
    import app.client as client_mod
    from app.config import Config

    monkeypatch.setenv("TELETHON_PROXY", "socks5://proxy.example.invalid:1080")
    seen: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen.update(kwargs)

        async def connect(self) -> None:
            return None

        async def is_user_authorized(self) -> bool:
            return False

        async def disconnect(self) -> None:
            return None

    monkeypatch.setattr(client_mod, "TelegramClient", FakeClient)
    # The fixture's session "stub" is not a real session string.
    monkeypatch.setattr(client_mod, "StringSession", lambda session: None)
    holder = client_mod.TelethonHolder(Config.from_env())
    with pytest.raises(RuntimeError, match="not authorized"):
        await holder.start()
    assert seen["proxy"]["addr"] == "proxy.example.invalid"
    assert seen["proxy"]["proxy_type"] == "socks5"


def test_the_login_client_connects_through_the_proxy(
    base_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.login as login_mod
    from app.config import Config

    monkeypatch.setenv("TELETHON_PROXY", "http://proxy.example.invalid:3128")
    seen: dict[str, Any] = {}

    class Stop(Exception):
        pass

    def fake_client(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise Stop

    monkeypatch.setattr(login_mod, "TelegramClient", fake_client)
    monkeypatch.setattr(login_mod, "StringSession", lambda session: None)
    with pytest.raises(Stop):
        login_mod.run(Config.for_login())
    assert seen["proxy"]["proxy_type"] == "http"
