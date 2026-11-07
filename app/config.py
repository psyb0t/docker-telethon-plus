"""Env-driven configuration."""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlsplit

LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# TELETHON_PROXY schemes Telethon can connect through (with python-socks).
PROXY_SCHEMES = ("socks5", "socks4", "http")

# A bucket admits `limit` requests per window, so a limit below 1 can never
# admit one.
MIN_BUCKET_LIMIT = 1
# asyncio.Queue treats a maxsize of 0 or less as unbounded, which would turn
# the slow-subscriber drop into unbounded memory growth.
MIN_UPDATES_BUFFER_SIZE = 1
# Intervals, jitter, the cache TTL and the flood sleep threshold use 0 for
# "off"; a negative value has no meaning.
MIN_NON_NEGATIVE = 0
# Timeouts must be positive: 0 would expire every request at once.
TIMEOUT_EXCLUSIVE_MINIMUM = 0.0


def telethon_log_level(log_level: int) -> int:
    """Level of the "telethon" logger for the app's own log level.

    Telethon logs routine chatter at INFO (flood sleeps, reconnects), so the
    app level may raise its level but never lower it below WARNING.
    """
    return max(log_level, logging.WARNING)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def _int(name: str, default: int, *, minimum: Optional[int] = None) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}, got {value}")
    return value


def _float(name: str, default: float, *, greater_than: Optional[float] = None) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc
    # float() accepts "nan" and "inf"; neither is a usable duration.
    if not math.isfinite(value):
        raise RuntimeError(f"{name} must be a finite number, got {raw!r}")
    if greater_than is not None and value <= greater_than:
        raise RuntimeError(f"{name} must be > {greater_than:g}, got {value:g}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise RuntimeError(f"{name} must be boolean-ish, got {raw!r}")


def _split_host_port(addr: str) -> tuple[str, int]:
    if ":" not in addr:
        raise RuntimeError(f"TELETHON_HTTP_LISTEN_ADDRESS must be host:port, got {addr!r}")
    host, _, port = addr.rpartition(":")
    try:
        # All interfaces when no host is given: the server runs in a container
        # and is reached through its published port.
        return host or "0.0.0.0", int(port)  # nosec B104
    except ValueError as exc:
        raise RuntimeError(f"invalid port in {addr!r}") from exc


def parse_proxy(raw: str) -> Optional[Dict[str, Any]]:
    """TELETHON_PROXY as the dict TelegramClient(proxy=...) takes, or None
    when it is empty.

    The value is scheme://[user:password@]host:port with scheme socks5,
    socks4 or http; user and password may be percent-encoded. rdns is on, so
    the proxy resolves Telegram's hostnames.
    """
    value = raw.strip()
    if not value:
        return None
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme not in PROXY_SCHEMES:
        raise RuntimeError(
            f"TELETHON_PROXY must start with {', '.join(s + '://' for s in PROXY_SCHEMES)}"
        )
    try:
        port = parts.port
    except ValueError as exc:
        raise RuntimeError("TELETHON_PROXY has an invalid port") from exc
    if not parts.hostname or port is None:
        raise RuntimeError("TELETHON_PROXY must be scheme://[user:password@]host:port")
    return {
        "proxy_type": scheme,
        "addr": parts.hostname,
        "port": port,
        "username": unquote(parts.username) if parts.username else None,
        "password": unquote(parts.password) if parts.password else None,
        "rdns": True,
    }


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    session: str
    listen_host: str
    listen_port: int
    log_level: int
    request_timeout: float
    flood_sleep_threshold: int
    device_model: str
    system_version: str
    app_version: str
    proxy: Optional[Dict[str, Any]]
    download_dir: str
    auth_key: str
    # Throttling
    throttle_enabled: bool
    throttle_global_interval_ms: int
    throttle_jitter_ms: int
    throttle_per_chat_interval_ms: int
    throttle_per_chat_read_interval_ms: int
    throttle_adaptive: bool
    bucket_resolve_per_min: int
    bucket_get_full_per_min: int
    bucket_join_per_hour: int
    bucket_create_per_hour: int
    bucket_send_per_min: int
    bucket_read_per_min: int
    # Entity cache
    cache_enabled: bool
    cache_path: str
    cache_ttl_seconds: int
    # Safety + observability
    read_only: bool
    dry_run: bool
    log_json: bool
    metrics_enabled: bool
    post_to_url: str
    post_to_timeout: float
    updates_enabled: bool
    updates_buffer_size: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls._build(require_session=True)

    @classmethod
    def for_login(cls) -> "Config":
        """Minimal config for the interactive login flow — no session required."""
        return cls._build(require_session=False)

    @classmethod
    def _build(cls, *, require_session: bool) -> "Config":
        api_id = _int("TELETHON_API_ID", 0)
        if api_id == 0:
            raise RuntimeError("TELETHON_API_ID environment variable is required")

        host, port = _split_host_port(
            os.environ.get("TELETHON_HTTP_LISTEN_ADDRESS", "0.0.0.0:8080")
        )

        level_name = os.environ.get("TELETHON_LOG_LEVEL", "INFO").strip().upper()
        # getattr(logging, name) accepted any attribute of the module, such as
        # BASIC_FORMAT (a string) or ROOT (a logger), and broke startup; a typo
        # silently meant INFO.
        if level_name not in LOG_LEVELS:
            raise RuntimeError(
                f"TELETHON_LOG_LEVEL must be one of {', '.join(LOG_LEVELS)}, got {level_name!r}"
            )
        log_level = LOG_LEVELS[level_name]

        session = (
            _required("TELETHON_SESSION")
            if require_session
            else os.environ.get("TELETHON_SESSION", "")
        )

        return cls(
            api_id=api_id,
            api_hash=_required("TELETHON_API_HASH"),
            session=session,
            listen_host=host,
            listen_port=port,
            log_level=log_level,
            request_timeout=_float(
                "TELETHON_REQUEST_TIMEOUT", 60.0, greater_than=TIMEOUT_EXCLUSIVE_MINIMUM
            ),
            flood_sleep_threshold=_int(
                "TELETHON_FLOOD_SLEEP_THRESHOLD", 60, minimum=MIN_NON_NEGATIVE
            ),
            device_model=os.environ.get("TELETHON_DEVICE_MODEL", "docker-telethon-plus"),
            system_version=os.environ.get("TELETHON_SYSTEM_VERSION", "1.0"),
            app_version=os.environ.get("TELETHON_APP_VERSION", "1.0"),
            proxy=parse_proxy(os.environ.get("TELETHON_PROXY", "")),
            # Files in it are created with mkstemp (tools._send_file).
            download_dir=os.environ.get(
                "TELETHON_DOWNLOAD_DIR", "/tmp/telethon-plus"  # nosec B108
            ),
            auth_key=os.environ.get("TELETHON_AUTH_KEY", "").strip(),
            throttle_enabled=_bool("TELETHON_THROTTLE_ENABLED", True),
            throttle_global_interval_ms=_int(
                "TELETHON_THROTTLE_GLOBAL_INTERVAL_MS", 50, minimum=MIN_NON_NEGATIVE
            ),
            throttle_jitter_ms=_int("TELETHON_THROTTLE_JITTER_MS", 200, minimum=MIN_NON_NEGATIVE),
            throttle_per_chat_interval_ms=_int(
                "TELETHON_THROTTLE_PER_CHAT_INTERVAL_MS", 1100, minimum=MIN_NON_NEGATIVE
            ),
            throttle_per_chat_read_interval_ms=_int(
                "TELETHON_THROTTLE_PER_CHAT_READ_INTERVAL_MS", 250, minimum=MIN_NON_NEGATIVE
            ),
            throttle_adaptive=_bool("TELETHON_THROTTLE_ADAPTIVE", True),
            bucket_resolve_per_min=_int(
                "TELETHON_BUCKET_RESOLVE_PER_MIN", 5, minimum=MIN_BUCKET_LIMIT
            ),
            bucket_get_full_per_min=_int(
                "TELETHON_BUCKET_GET_FULL_PER_MIN", 10, minimum=MIN_BUCKET_LIMIT
            ),
            bucket_join_per_hour=_int("TELETHON_BUCKET_JOIN_PER_HOUR", 5, minimum=MIN_BUCKET_LIMIT),
            bucket_create_per_hour=_int(
                "TELETHON_BUCKET_CREATE_PER_HOUR", 5, minimum=MIN_BUCKET_LIMIT
            ),
            bucket_send_per_min=_int("TELETHON_BUCKET_SEND_PER_MIN", 20, minimum=MIN_BUCKET_LIMIT),
            bucket_read_per_min=_int("TELETHON_BUCKET_READ_PER_MIN", 600, minimum=MIN_BUCKET_LIMIT),
            cache_enabled=_bool("TELETHON_CACHE_ENABLED", True),
            cache_path=os.environ.get("TELETHON_CACHE_PATH", "/cache/entities.json"),
            cache_ttl_seconds=_int(
                "TELETHON_CACHE_TTL_SECONDS", 7 * 24 * 3600, minimum=MIN_NON_NEGATIVE
            ),
            read_only=_bool("TELETHON_READ_ONLY", False),
            dry_run=_bool("TELETHON_DRY_RUN", False),
            log_json=_bool("TELETHON_LOG_JSON", False),
            metrics_enabled=_bool("TELETHON_METRICS_ENABLED", True),
            post_to_url=os.environ.get("TELETHON_POST_TO_URL", "").strip(),
            post_to_timeout=_float(
                "TELETHON_POST_TO_TIMEOUT", 10.0, greater_than=TIMEOUT_EXCLUSIVE_MINIMUM
            ),
            updates_enabled=_bool("TELETHON_UPDATES_ENABLED", True),
            updates_buffer_size=_int(
                "TELETHON_UPDATES_BUFFER_SIZE", 256, minimum=MIN_UPDATES_BUFFER_SIZE
            ),
        )
