# Docs and releases

- `docs/` is the documentation site (GitHub Pages from `main`, Jekyll, Just the Docs, configured in `docs/_config.yml`). Every page has front matter with a unique `title`, a hand-written `description` of 50 to 170 characters, and one `#` heading. Keep `docs/` flat.
- Link between pages with relative `.md` paths. Link files outside `docs/` with full `https://github.com/psyb0t/docker-telethon-plus/blob/main/...` URLs (`tree/main/...` for directories); a `../` link breaks on the site.
- Wrap code blocks containing `{{` or `{%` in `<!-- {% raw %} -->` and `<!-- {% endraw %} -->`.
- After a docs change run `make docs` and check the output in `.testing/docs-site` before pushing.
- `README.md` is the front door and is synced to Docker Hub, which rejects a description over 25,000 bytes. Reference material goes in `docs/`; README links to the site with full `https://psyb0t.github.io/docker-telethon-plus/...` URLs.
- Every change to behaviour, an environment variable, a route, a tool or a make target updates the matching docs page, `.env.example`, and the skill under `.agents/skills/telethon-plus/` when the agent-facing workflow changed.
- The release version is the git tag (`vX.Y.Z`). `pyproject.toml`, `server.json` and the plugin `package.json` keep `0.0.0`. The release notes come from the annotated tag; `CHANGELOG.md` gets one `## vX.Y.Z, YYYY-MM-DD` section per release.
- Release order: push to `main`, let the pipeline pass, then tag that commit. The tag run promotes the `sha-<commit>` image built on `main` and never rebuilds.
- Prose: no em dashes, "directory" not "folder", plain words. Never mention local scratch directories in committed text.
