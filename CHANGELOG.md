# Changelog

All notable changes per release. Versions follow [semver](https://semver.org).

## v0.5.5 — 2026-07-27

- Added Claude Code (`.agents/.claude-plugin/plugin.json`) and Codex (`.agents/.codex-plugin/plugin.json`) plugin manifests so the existing skill installs natively in both clients.
- Added a "## Agent integrations" README section with copy-pasteable install commands for Claude Code, Codex, and OpenClaw (including the MCP-bridge plugin), and a matching Table of Contents entry.

## v0.5.4 — 2026-07-27

- Added a GitHub Actions CI status badge to the README.

## v0.5.3 — 2026-07-27

Add README status badges.

- Added self-hosted version and license badges (rendered as SVGs on the `badges` branch by the `create-badges` CI job, no third-party render service) plus a Docker Hub pulls badge. Wired a badges job into pipeline.yml.
