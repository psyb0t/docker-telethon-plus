"""Env-driven configuration."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} environment variable is required")
    return value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc


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
        raise RuntimeError(
            f"TELETHON_HTTP_LISTEN_ADDRESS must be host:port, got {addr!r}"
        )
    host, _, port = addr.rpartition(":")
    try:
        return host or "0.0.0.0", int(port)
    except ValueError as exc:
        raise RuntimeError(f"invalid port in {addr!r}") from exc


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
    proxy: str
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

        level_name = os.environ.get("TELETHON_LOG_LEVEL", "INFO").upper()
        log_level = getattr(logging, level_name, logging.INFO)

        session = (
            _required("TELETHON_SESSION") if require_session
            else os.environ.get("TELETHON_SESSION", "")
        )

        return cls(
            api_id=api_id,
            api_hash=_required("TELETHON_API_HASH"),
            session=session,
            listen_host=host,
            listen_port=port,
            log_level=log_level,
            request_timeout=float(
                os.environ.get("TELETHON_REQUEST_TIMEOUT", "60")
            ),
            flood_sleep_threshold=_int("TELETHON_FLOOD_SLEEP_THRESHOLD", 60),
            device_model=os.environ.get("TELETHON_DEVICE_MODEL", "docker-telethon-plus"),
            system_version=os.environ.get("TELETHON_SYSTEM_VERSION", "1.0"),
            app_version=os.environ.get("TELETHON_APP_VERSION", "1.0"),
            proxy=os.environ.get("TELETHON_PROXY", ""),
            download_dir=os.environ.get("TELETHON_DOWNLOAD_DIR", "/tmp/telethon-plus"),
            auth_key=os.environ.get("TELETHON_AUTH_KEY", "").strip(),
            throttle_enabled=_bool("TELETHON_THROTTLE_ENABLED", True),
            throttle_global_interval_ms=_int("TELETHON_THROTTLE_GLOBAL_INTERVAL_MS", 50),
            throttle_jitter_ms=_int("TELETHON_THROTTLE_JITTER_MS", 200),
            throttle_per_chat_interval_ms=_int("TELETHON_THROTTLE_PER_CHAT_INTERVAL_MS", 1100),
            throttle_per_chat_read_interval_ms=_int(
                "TELETHON_THROTTLE_PER_CHAT_READ_INTERVAL_MS", 250
            ),
            throttle_adaptive=_bool("TELETHON_THROTTLE_ADAPTIVE", True),
            bucket_resolve_per_min=_int("TELETHON_BUCKET_RESOLVE_PER_MIN", 5),
            bucket_get_full_per_min=_int("TELETHON_BUCKET_GET_FULL_PER_MIN", 10),
            bucket_join_per_hour=_int("TELETHON_BUCKET_JOIN_PER_HOUR", 5),
            bucket_create_per_hour=_int("TELETHON_BUCKET_CREATE_PER_HOUR", 5),
            bucket_send_per_min=_int("TELETHON_BUCKET_SEND_PER_MIN", 20),
            bucket_read_per_min=_int("TELETHON_BUCKET_READ_PER_MIN", 600),
            cache_enabled=_bool("TELETHON_CACHE_ENABLED", True),
            cache_path=os.environ.get("TELETHON_CACHE_PATH", "/cache/entities.json"),
            cache_ttl_seconds=_int("TELETHON_CACHE_TTL_SECONDS", 7 * 24 * 3600),
            read_only=_bool("TELETHON_READ_ONLY", False),
            dry_run=_bool("TELETHON_DRY_RUN", False),
            log_json=_bool("TELETHON_LOG_JSON", False),
            metrics_enabled=_bool("TELETHON_METRICS_ENABLED", True),
            post_to_url=os.environ.get("TELETHON_POST_TO_URL", "").strip(),
            post_to_timeout=float(os.environ.get("TELETHON_POST_TO_TIMEOUT", "10")),
            updates_enabled=_bool("TELETHON_UPDATES_ENABLED", True),
            updates_buffer_size=_int("TELETHON_UPDATES_BUFFER_SIZE", 256),
        )
