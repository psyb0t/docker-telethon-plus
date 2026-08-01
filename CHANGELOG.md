# Changelog

All notable changes per release. Versions follow [semver](https://semver.org).

## v0.5.7 — 2026-08-01

Infrastructure only. No code in this repo changed — every commit since v0.5.6 touches `.github/workflows/`.

- The pipeline was split: building and publishing stay in `pipeline.yml`, and everything that leaves the host now lives beside it in `mirror-and-archive.yml`.
- The repo is mirrored to Codeberg as well as GitLab.
- It is archived to the Wayback Machine, Software Heritage and archive.org.
- Issues opened on either mirror are copied back to GitHub every six hours, and closed here when the original closes.
- Pull requests are switched off on the mirrors: they are force-pushed from GitHub, so anything merged there would be destroyed by the next sync. Issues and forking stay enabled.

## v0.5.6 — 2026-07-27

- Fixed the "## Agent integrations" README section: the Codex subsection was missing its install command. It now includes `codex plugin add telethon-plus@psyb0t` right after the marketplace-add step.
- Clarified that the skill's invocation form differs by source: installed via the marketplace it's `$telethon-plus:telethon-plus`, while Codex's automatic pickup from a repo's own `.agents/skills/` (no install needed) uses plain `$telethon-plus`.

## v0.5.5 — 2026-07-27

- Added Claude Code (`.agents/.claude-plugin/plugin.json`) and Codex (`.agents/.codex-plugin/plugin.json`) plugin manifests so the existing skill installs natively in both clients.
- Added a "## Agent integrations" README section with copy-pasteable install commands for Claude Code, Codex, and OpenClaw (including the MCP-bridge plugin), and a matching Table of Contents entry.

## v0.5.4 — 2026-07-27

- Added a GitHub Actions CI status badge to the README.

## v0.5.3 — 2026-07-27

Add README status badges.

- Added self-hosted version and license badges (rendered as SVGs on the `badges` branch by the `create-badges` CI job, no third-party render service) plus a Docker Hub pulls badge. Wired a badges job into pipeline.yml.
