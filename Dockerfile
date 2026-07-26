FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

# MCP Registry ownership label
LABEL io.modelcontextprotocol.server.name="io.github.psyb0t/telethon-plus"

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        tini \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN useradd -r -u 1000 -m -d /home/telethon-plus -s /bin/sh telethon-plus

COPY --from=builder /install /usr/local

WORKDIR /app

COPY app/ /app/app/
COPY login.py /app/login.py

RUN chown -R telethon-plus:telethon-plus /app && touch /session && chmod 666 /session \
    && mkdir -p /cache && chown telethon-plus:telethon-plus /cache && chmod 700 /cache

USER telethon-plus

VOLUME ["/cache"]

ENV TELETHON_HTTP_LISTEN_ADDRESS=0.0.0.0:8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

ENTRYPOINT ["/usr/bin/tini", "--", "python", "-m", "app"]
CMD []
