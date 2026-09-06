# netmon — self-hosted local network monitor
#
# Uses the official uv image so the container matches the project's uv-managed
# workflow (pinned Python + locked deps from uv.lock).
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# System binaries netmon shells out to:
#   nmap          — LAN ARP scan for device discovery
#   speedtest-cli — bandwidth/ping measurement (provides the `speedtest` command)
#   sudo          — runner.py runs `sudo -n nmap` for raw-socket ARP access;
#                   the container runs as root, so this succeeds with no extra config
#
# `apt-get upgrade` applies the latest Debian security patches available at
# build time to the base-image packages (openssl, gnutls, etc.). It doesn't
# fix CVEs Debian hasn't patched yet, so rebuild periodically (the publish
# workflow runs weekly) to pick up new fixes as they land.
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends nmap speedtest-cli sudo \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pin the interpreter to this image's Python 3.13 (matches requires-python).
# UV_PYTHON takes precedence over the repo's .python-version file, so the build
# is deterministic and doesn't try to fetch a different interpreter.
ENV UV_PYTHON=3.13 \
    UV_PYTHON_DOWNLOADS=never

# Install dependencies first (cached until the lockfile changes), then the app.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .
RUN uv sync --frozen

# Persist the SQLite database and generated graphs on a mounted volume.
ENV DB_PATH=/data/metrics.sql \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8080
VOLUME ["/data"]

# Web dashboard. NOTE: with `network_mode: host` (required for the LAN scan)
# this EXPOSE is informational — the server binds directly to the host's port.
EXPOSE 8080

CMD ["uv", "run", "main.py"]
