# Telegram calls and flood safety

- Every call to Telegram runs inside `async with holder.guard(bucket, chat_key) as client:`. The guard takes the throttle slot, then the holder lock, then the global gap, and feeds `FloodWaitError` into the adaptive backoff and the metrics. Never call `holder.client` outside it.
- Pick the bucket that matches the MTProto method: `resolve_username`, `get_full`, `join`, `create`, `send`, `read` or `other` (`_build_throttle_config` in `app/client.py`). Pass the chat key for chat-scoped calls, and `chat_kind="read"` for reads, so the per-chat interval applies.
- Resolve chats with `holder.resolve_entity()`, never `client.get_entity()` on a username. The resolver checks the entity cache first, so restarts do not cost `resolveUsername` calls, which are the ones most likely to bring long flood waits.
- When one Telethon call sends several requests internally (paging participants or dialogs), charge the extra requests to the bucket with `holder.throttle.record_extra(bucket, n)`, as `_get_participants` and `_get_dialogs` in `app/tools.py` do.
- Do not catch `FloodWaitError` inside a tool to retry it. Let it reach the route, which answers 429 with `Retry-After`, or MCP, which answers `flood_wait`.
- Keep default bucket sizes and intervals conservative. Changing a default in `app/config.py` means changing `docs/configuration.md`, `docs/throttling.md` and `.env.example` in the same change.
