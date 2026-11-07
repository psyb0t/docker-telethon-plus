"""Interactive login flow — run via `docker run psyb0t/telethon-plus login`."""

from __future__ import annotations

import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

from app.config import Config

# The session string logs in as the account, so only its owner may read it.
SESSION_FILE_MODE = 0o600


async def _run(cfg: Config) -> None:
    print("Telethon login — generates a TELETHON_SESSION string.")
    print("You'll be asked for your phone number and the login code.")
    print()

    client = TelegramClient(
        StringSession(cfg.session or None),
        cfg.api_id,
        cfg.api_hash,
        device_model=cfg.device_model,
        system_version=cfg.system_version,
        app_version=cfg.app_version,
        proxy=cfg.proxy,
    )
    await client.start()

    me = await client.get_me()
    session_string = client.session.save()
    await client.disconnect()

    print()
    print(f"Authorized as: id={me.id} username={me.username}")
    print()
    print("TELETHON_SESSION:")
    print()
    print(session_string)
    print()

    output_file = os.environ.get("TELETHON_SESSION_OUTPUT_FILE", "").strip()
    if output_file:
        _write_session_file(output_file, session_string)


def _write_session_file(path: str, session_string: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SESSION_FILE_MODE)
    with os.fdopen(fd, "w") as fh:
        # The mode in os.open only applies to a file it creates, and the
        # umask can only narrow it; a file that already existed keeps its
        # old mode until it is changed here, before the secret is written.
        try:
            os.fchmod(fh.fileno(), SESSION_FILE_MODE)
        except PermissionError as exc:
            # A file owned by someone else (a bind mount, the image's own
            # /session) can be written but not chmodded. The session is
            # already on stdout, so keep going and say so.
            print(
                f"warning: could not restrict {path} to mode " f"{SESSION_FILE_MODE:04o}: {exc}",
                file=sys.stderr,
            )
        fh.write(session_string)


def run(cfg: Config) -> None:
    try:
        asyncio.run(_run(cfg))
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
