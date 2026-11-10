# Testing

- `make test-unit` is the gate: the offline suite in `tests/unit/` with a mocked `TelegramClient`, run in the dev image with coverage of `app/`. It fails under `COVERAGE_FLOOR` in the `Makefile`.
- Shared unit fixtures are in `tests/unit/conftest.py`.
- `make test` is the integration suite in `tests/`. It builds the image and talks to a real Telegram account from `.env`, so it does not run in CI and is run only when the owner asks. It writes to `TEST_CHAT` and to groups it creates and deletes itself, named `telethon-plus-it-...`.
- Optional integration chats (`TEST_PUBLIC_CHANNEL`, `TEST_JOIN_CHAT`, `TEST_INVITE_LINK`, `TEST_BOT_TOKEN`, `TEST_PEER_USER`) come from `tests/.env`, and their tests skip while the setting is empty.
- Only one container may use a session at a time. Do not run the integration suite while another instance on the same session is up.
- Lint and unit tests need nothing but Docker and Make on the host. Do not add a step that needs credentials, network access to Telegram, or a host Python toolchain to `make lint` or `make test-unit`.
