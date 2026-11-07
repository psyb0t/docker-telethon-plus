# syntax=docker/dockerfile:1.7
FROM python:3.14.9-slim@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:0.11.15@sha256:e590846f4776907b254ac0f44b5b380347af5d90d668138ca7938d1b0c2f98d3 /uv /usr/local/bin/uv

WORKDIR /build

# --frozen fails the build when uv.lock no longer matches pyproject.toml, and
# uv checks every wheel against the hash in the lock.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

FROM python:3.14.9-slim@sha256:35caba83af2fe6da9a5d922f9b0156f21d1246a48278c58a08bd253303ba42e8

# MCP Registry ownership label
LABEL io.modelcontextprotocol.server.name="io.github.psyb0t/telethon-plus"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd -r -u 1000 -m -d /home/telethon-plus -s /bin/sh telethon-plus

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

COPY app/ /app/app/
COPY login.py /app/login.py
# GPL v2 requires a copy of the license with every distributed copy.
COPY LICENSE /app/LICENSE

RUN chown -R telethon-plus:telethon-plus /app && touch /session && chmod 666 /session \
    && mkdir -p /cache && chown telethon-plus:telethon-plus /cache && chmod 700 /cache

USER telethon-plus

VOLUME ["/cache"]

ENV PATH=/opt/venv/bin:$PATH
ENV TELETHON_HTTP_LISTEN_ADDRESS=0.0.0.0:8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,sys,urllib.request; port = os.environ.get('TELETHON_HTTP_LISTEN_ADDRESS', ':8080').rpartition(':')[2]; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=3).status == 200 else 1)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "python", "-m", "app"]
CMD []
