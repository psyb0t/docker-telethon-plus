"""Config validation: a value the app cannot run with stops startup with a
"config error", never a traceback and never a crash on the first request."""

from __future__ import annotations

import sys

import pytest

from app.config import Config

BUCKET_VARS = [
    "TELETHON_BUCKET_RESOLVE_PER_MIN",
    "TELETHON_BUCKET_GET_FULL_PER_MIN",
    "TELETHON_BUCKET_JOIN_PER_HOUR",
    "TELETHON_BUCKET_CREATE_PER_HOUR",
    "TELETHON_BUCKET_SEND_PER_MIN",
    "TELETHON_BUCKET_READ_PER_MIN",
]
NON_NEGATIVE_INT_VARS = [
    "TELETHON_THROTTLE_GLOBAL_INTERVAL_MS",
    "TELETHON_THROTTLE_JITTER_MS",
    "TELETHON_THROTTLE_PER_CHAT_INTERVAL_MS",
    "TELETHON_THROTTLE_PER_CHAT_READ_INTERVAL_MS",
    "TELETHON_CACHE_TTL_SECONDS",
    "TELETHON_FLOOD_SLEEP_THRESHOLD",
]
TIMEOUT_VARS = ["TELETHON_REQUEST_TIMEOUT", "TELETHON_POST_TO_TIMEOUT"]


def _rejects(monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=message) as raised:
        Config.from_env()
    assert name in str(raised.value)


@pytest.mark.parametrize("name", BUCKET_VARS)
@pytest.mark.parametrize("value", ["0", "-3"])
def test_a_bucket_limit_below_one_is_rejected(
    base_env: None, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    """Regression: a limit of 0 crashed every call with an IndexError."""
    _rejects(monkeypatch, name, value, r"must be >= 1")


@pytest.mark.parametrize("value", ["0", "-1"])
def test_an_updates_buffer_below_one_is_rejected(
    base_env: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Regression: asyncio.Queue(maxsize<=0) is unbounded."""
    _rejects(monkeypatch, "TELETHON_UPDATES_BUFFER_SIZE", value, r"must be >= 1")


@pytest.mark.parametrize("name", NON_NEGATIVE_INT_VARS)
def test_a_negative_interval_ttl_or_threshold_is_rejected(
    base_env: None, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _rejects(monkeypatch, name, "-1", r"must be >= 0")


@pytest.mark.parametrize("name", TIMEOUT_VARS)
def test_a_timeout_that_is_not_a_number_is_a_config_error(
    base_env: None, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Regression: a bare float() gave a ValueError traceback for "60s"."""
    _rejects(monkeypatch, name, "60s", r"must be a number")


@pytest.mark.parametrize("name", TIMEOUT_VARS)
@pytest.mark.parametrize("value", ["0", "-5", "0.0"])
def test_a_timeout_that_is_not_positive_is_rejected(
    base_env: None, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _rejects(monkeypatch, name, value, r"must be > 0")


@pytest.mark.parametrize("name", TIMEOUT_VARS)
@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_a_timeout_that_is_not_finite_is_rejected(
    base_env: None, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _rejects(monkeypatch, name, value, r"must be a finite number")


def test_the_minimums_themselves_are_accepted(
    base_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in BUCKET_VARS:
        monkeypatch.setenv(name, "1")
    for name in NON_NEGATIVE_INT_VARS:
        monkeypatch.setenv(name, "0")
    monkeypatch.setenv("TELETHON_UPDATES_BUFFER_SIZE", "1")
    monkeypatch.setenv("TELETHON_REQUEST_TIMEOUT", "0.5")
    monkeypatch.setenv("TELETHON_POST_TO_TIMEOUT", "2.5")

    cfg = Config.from_env()

    assert cfg.bucket_send_per_min == 1
    assert cfg.throttle_jitter_ms == 0
    assert cfg.cache_ttl_seconds == 0
    assert cfg.flood_sleep_threshold == 0
    assert cfg.updates_buffer_size == 1
    assert cfg.request_timeout == 0.5
    assert cfg.post_to_timeout == 2.5


def test_the_defaults_still_build(base_env: None) -> None:
    cfg = Config.from_env()
    assert cfg.request_timeout == 60.0
    assert cfg.post_to_timeout == 10.0
    assert cfg.bucket_resolve_per_min == 5
    assert cfg.updates_buffer_size == 256


def test_a_bad_timeout_exits_with_a_config_error_not_a_traceback(
    base_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.__main__ import main

    monkeypatch.setattr(sys, "argv", ["app"])
    monkeypatch.setenv("TELETHON_REQUEST_TIMEOUT", "60s")

    with pytest.raises(SystemExit) as exited:
        main()

    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("config error: TELETHON_REQUEST_TIMEOUT must be a number")
    assert "Traceback" not in err


@pytest.mark.parametrize("value", ["verbose", "basic_format", "root"])
def test_an_unknown_log_level_stops_startup(
    base_env: None, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Regression: any attribute of the logging module was accepted as a
    level, and a typo quietly meant INFO."""
    from app.config import Config

    monkeypatch.setenv("TELETHON_LOG_LEVEL", value)
    with pytest.raises(RuntimeError, match="TELETHON_LOG_LEVEL must be one of"):
        Config.from_env()


@pytest.mark.parametrize(("value", "level"), [("debug", 10), (" Warning ", 30), ("CRITICAL", 50)])
def test_a_log_level_is_read_in_any_case(
    base_env: None, monkeypatch: pytest.MonkeyPatch, value: str, level: int
) -> None:
    from app.config import Config

    monkeypatch.setenv("TELETHON_LOG_LEVEL", value)
    assert Config.from_env().log_level == level
