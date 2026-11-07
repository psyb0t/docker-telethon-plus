"""Interactive login flow — run via `docker run psyb0t/telethon-plus login`."""

from __future__ import annotations

import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

from app.config import Config


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
        with open(output_file, "w") as fh:
            fh.write(session_string)


def run(cfg: Config) -> None:
    try:
        asyncio.run(_run(cfg))
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
