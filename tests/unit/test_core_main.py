"""TELETHON_LOG_JSON: the entry point logs one JSON object per line."""

from __future__ import annotations

import io
import json
import logging
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List

import pytest
from uvicorn.config import LOGGING_CONFIG

import app.__main__ as main_mod
from app.__main__ import JsonLogFormatter, RedactQueryToken


@pytest.fixture
def isolated_logger() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("test_core.json")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    yield logger, stream
    logger.handlers = []


def _lines(stream: io.StringIO) -> List[Dict[str, Any]]:
    raw = stream.getvalue().splitlines()
    return [json.loads(line) for line in raw]


def test_a_record_is_one_json_line_with_the_documented_fields(
    isolated_logger: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = isolated_logger
    logger.warning("rate %s is high\nsecond line", "limit")

    assert len(stream.getvalue().splitlines()) == 1
    (entry,) = _lines(stream)
    assert set(entry) == {"time", "level", "logger", "message"}
    assert entry["level"] == "WARNING"
    assert entry["logger"] == "test_core.json"
    assert entry["message"] == "rate limit is high\nsecond line"
    assert datetime.fromisoformat(entry["time"]).utcoffset() == timedelta(0)


def test_an_exception_is_carried_as_a_string(
    isolated_logger: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = isolated_logger
    try:
        raise ValueError("bad input")
    except ValueError:
        logger.exception("failed")

    (entry,) = _lines(stream)
    assert isinstance(entry["exc_info"], str)
    assert "ValueError: bad input" in entry["exc_info"]
    assert "Traceback" in entry["exc_info"]


@pytest.fixture
def served(base_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[Dict[str, Any]]:
    """main() with the server, the app and basicConfig replaced by recorders."""
    seen: Dict[str, Any] = {}
    telethon_logger = logging.getLogger("telethon")
    level_before = telethon_logger.level
    monkeypatch.setattr(main_mod.hachoir_log, "use_print", True)
    monkeypatch.setattr(main_mod.hachoir_log, "on_new_message", None)

    def basic_config(**kwargs: Any) -> None:
        seen["basic_config"] = kwargs

    monkeypatch.setattr(sys, "argv", ["app"])
    monkeypatch.setattr(main_mod, "build_app", lambda cfg: object())
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda app, **kwargs: seen.update(run=kwargs))
    monkeypatch.setattr(logging, "basicConfig", basic_config)
    yield seen
    telethon_logger.setLevel(level_before)
    for name in main_mod.UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        for redactor in [f for f in logger.filters if isinstance(f, RedactQueryToken)]:
            logger.removeFilter(redactor)


def test_log_json_installs_the_json_formatter_and_hands_uvicorn_to_it(
    served: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: TELETHON_LOG_JSON was parsed and never used."""
    monkeypatch.setenv("TELETHON_LOG_JSON", "true")

    main_mod.main()

    (handler,) = served["basic_config"]["handlers"]
    assert isinstance(handler.formatter, JsonLogFormatter)
    # No uvicorn log config, so its loggers reach the root handler.
    assert served["run"]["log_config"] is None


def test_without_log_json_the_text_format_and_uvicorns_config_stay(
    served: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELETHON_LOG_JSON", "false")

    main_mod.main()

    (handler,) = served["basic_config"]["handlers"]
    assert handler.formatter is None
    assert served["basic_config"]["format"] == main_mod.TEXT_LOG_FORMAT
    assert served["run"]["log_config"] is LOGGING_CONFIG


def test_telethons_logger_never_runs_below_warning(
    served: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELETHON_LOG_LEVEL", "DEBUG")
    main_mod.main()
    assert logging.getLogger("telethon").level == logging.WARNING

    monkeypatch.setenv("TELETHON_LOG_LEVEL", "ERROR")
    main_mod.main()
    assert logging.getLogger("telethon").level == logging.ERROR


def test_hachoir_warnings_go_through_logging_not_stderr(
    served: Dict[str, Any], capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    """Regression: hachoir printed parser warnings to stderr itself, so a
    TELETHON_LOG_JSON stream got plain-text lines whenever a file was sent."""
    main_mod.main()

    with caplog.at_level(logging.DEBUG, logger="hachoir"):
        main_mod.hachoir_log.warning("odd header")

    assert capsys.readouterr().err == ""
    (record,) = [r for r in caplog.records if r.name == "hachoir"]
    assert record.levelno == logging.DEBUG
    assert record.getMessage() == "[warn] odd header"


@pytest.mark.parametrize("log_json", ["true", "false"])
def test_the_websocket_token_never_reaches_the_log(
    served: Dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    log_json: str,
) -> None:
    """Regression: uvicorn logged the accepted /ws/updates path whole, so the
    auth key passed as ?token= was in every log."""
    monkeypatch.setenv("TELETHON_LOG_JSON", log_json)
    main_mod.main()
    main_mod.main()

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        logging.getLogger("uvicorn.error").info(
            '%s - "WebSocket %s" [accepted]', "172.19.0.9:60524", "/ws/updates?token=s3cret-key"
        )

    (record,) = [r for r in caplog.records if r.name == "uvicorn.error"]
    assert record.getMessage() == (
        '172.19.0.9:60524 - "WebSocket /ws/updates?token=[redacted]" [accepted]'
    )
    for name in main_mod.UVICORN_LOGGERS:
        assert sum(isinstance(f, RedactQueryToken) for f in logging.getLogger(name).filters) == 1


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "GET /api/messages?chat=x&token=abc&limit=5",
            "GET /api/messages?chat=x&token=[redacted]&limit=5",
        ),
        ("GET /api/messages?chat=x&limit=5", "GET /api/messages?chat=x&limit=5"),
        ("GET /api/messages?mytoken=abc", "GET /api/messages?mytoken=abc"),
    ],
)
def test_only_the_token_parameter_is_redacted(line: str, expected: str) -> None:
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s", (line,), None)
    assert RedactQueryToken().filter(record) is True
    assert record.getMessage() == expected
