"""Entry point.

Usage:
    python -m app          # start HTTP server
    python -m app login    # interactive login to generate TELETHON_SESSION
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import uvicorn
from hachoir.core.log import log as hachoir_log
from uvicorn.config import LOGGING_CONFIG

from app.config import Config, telethon_log_level
from app.login import run as run_login
from app.server import build_app

TEXT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
HACHOIR_LOGGER = logging.getLogger("hachoir")
# /ws/updates takes the auth key as ?token=, because browsers cannot set a
# header on a websocket, and uvicorn logs that path whole when it accepts or
# refuses the connection.
QUERY_TOKEN = re.compile(r"([?&]token=)[^&\s\"']*")
REDACTED = "[redacted]"
UVICORN_LOGGERS = ("uvicorn.error", "uvicorn.access")


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line: time (ISO 8601, UTC), level, logger, message,
    and exc_info as a string when the record carries an exception."""

    def format(self, record: logging.LogRecord) -> str:
        entry: Dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class RedactQueryToken(logging.Filter):
    """Replaces the value of a token= query parameter in the record's text."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = QUERY_TOKEN.sub(r"\g<1>" + REDACTED, message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        return True


def configure_logging(cfg: Config) -> None:
    handler = logging.StreamHandler()
    if cfg.log_json:
        handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(level=cfg.log_level, format=TEXT_LOG_FORMAT, handlers=[handler])
    logging.getLogger("telethon").setLevel(telethon_log_level(cfg.log_level))
    # On the loggers rather than a handler, so they hold in both log formats:
    # uvicorn's text config replaces the handlers but keeps logger filters.
    for name in UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactQueryToken) for f in logger.filters):
            logger.addFilter(RedactQueryToken())
    _route_hachoir_logs()


def _route_hachoir_logs() -> None:
    # hachoir, which Telethon uses to read media metadata, writes its parser
    # warnings straight to stderr, past the formatter, so TELETHON_LOG_JSON
    # output gets plain-text lines. They describe one file's quirks, not the
    # server, so they go to the "hachoir" logger at DEBUG.
    hachoir_log.use_print = False
    hachoir_log.on_new_message = _log_hachoir_message


def _log_hachoir_message(level: int, prefix: str, text: str, ctxt: Any) -> None:
    HACHOIR_LOGGER.debug("%s %s", prefix, text)


def _uvicorn_log_config(cfg: Config) -> Optional[Dict[str, Any]]:
    # uvicorn's own config gives its loggers plain-text handlers and stops
    # them propagating. Without it they reach the root handler, so the server
    # and access lines come out as JSON too.
    if cfg.log_json:
        return None
    return LOGGING_CONFIG


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "serve"

    if command == "login":
        try:
            cfg = Config.for_login()
        except RuntimeError as exc:
            print(f"config error: {exc}", file=sys.stderr)
            sys.exit(2)
        run_login(cfg)
        return

    if command != "serve":
        print(f"unknown command {command!r} — valid: serve, login", file=sys.stderr)
        sys.exit(2)

    try:
        cfg = Config.from_env()
    except RuntimeError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        sys.exit(2)

    configure_logging(cfg)

    app = build_app(cfg)

    uvicorn.run(
        app,
        host=cfg.listen_host,
        port=cfg.listen_port,
        log_config=_uvicorn_log_config(cfg),
        log_level=logging.getLevelName(cfg.log_level).lower(),
        access_log=cfg.log_level <= logging.INFO,
    )


if __name__ == "__main__":
    main()
