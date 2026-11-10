# CI

- `.github/workflows/pipeline.yml` runs on every push (`on: [push]`), with `permissions: {}` at the top and the minimum per job. Keep it that way; do not restrict it to `main`.
- `code` runs `make lint`, `make test-unit` and `make sec` through `code-workflow.yml` on every branch push and never on tags. These targets run in the dev image, so the runner needs only Docker and Make.
- `docker` (`docker-image-workflow.yml`, `push_sha_tag: true`) builds `psyb0t/telethon-plus` for `linux/amd64,linux/arm64` after `code`; the called workflow builds only on `main`.
- A tag runs `wait` (`wait-for-default-branch.yml`), then `docker-promote` (`promote_on_tag: true`, `create_release: false`), then `release` (`release-workflow.yml`), and publishes the skill and plugin to ClawHub and `server.json` to the MCP Registry.
- `badges` runs after a passing `main` run or a successful `wait` on a tag.
- `collaborators-only.yml`, `issue-pull.yml` and `mirror-and-archive.yml` are shared housekeeping; leave them alone.
- Validate workflow changes with `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7`, and update the CI section of `docs/development.md` when the pipeline changes.
