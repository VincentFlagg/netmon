# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Docker images are published to `ghcr.io/vincentflagg/netmon` and tagged to match
these versions — e.g. `1.0.0` (exact), `1.0` (latest patch), `1` (latest minor),
plus `latest` (newest release). Pin a specific version in your compose file to
control when you upgrade; see the [README](README.md#docker).

## [Unreleased]

_Nothing yet._

## [1.1.0] - 2026-07-27

### Added
- **Redesigned dashboard** — a full-width ISP banner on top, the stat cards
  (download / upload / ping / devices) in a column beside the 24-hour graph.
- **Device list** — clicking the Devices card opens a modal listing the devices
  discovered over the last 24 hours: online/offline status, IP, best-effort
  hostname (reverse DNS), latency, and when each was last seen.
- **Full history page** — the dashboard table now shows the latest 10
  measurements with a "View all" link to a dedicated `/history` page
  (up to 500 rows).

## [1.0.0] - 2026-07-27

First tagged release: netmon can now run as a container with a web dashboard and
multiple notification backends, on top of the existing speed-test / LAN-scan /
report engine.

### Added
- **Docker packaging** — `Dockerfile` (bundles `nmap`, `speedtest-cli`, and the
  locked Python deps) and `docker-compose.yml`. Runs with host networking plus
  `NET_RAW`/`NET_ADMIN` so the `nmap` LAN scan works, and persists the database
  in a volume.
- **Web dashboard** — a standard-library web server (no new dependencies) on
  `WEB_PORT` (default `8080`) showing latest metrics, the live 24-hour graph, a
  history table, a "run speed test now" button, and CSV export. Configurable via
  `WEB_ENABLED`, `WEB_HOST`, `WEB_PORT`.
- **NAS deployment** — `docker-compose.nas.yml` (bind-mount + overridable port),
  a paste-ready `dockge-stack.yml` for Dockge/Portainer, and a step-by-step
  `INSTALL-NAS.md` guide for UGREEN + Dockge.
- **GHCR publishing workflow** — `.github/workflows/docker-publish.yml` builds
  and pushes the image on each push and on `v*` tags.
- **ntfy notifier** — `NOTIFIER=ntfy` with `NTFY_URL`, and optional auth via a
  bearer token (`NTFY_TOKEN`) or username/password (`NTFY_USER` / `NTFY_PASSWORD`,
  HTTP Basic auth). Reports are sent as plain text; the 24-hour graph is attached.
- **Dashboard-only mode** — `NOTIFIER=none` runs the monitor and dashboard
  without pushing to any service.
- **Optional AI** — the `AI_*` variables are now optional (all-or-nothing); leave
  them empty to run without the AI commentary.

### Changed
- Extracted the measurement cycle into a `Monitor` service shared by the
  scheduler loop and the web "run now" button; `main.py` is now a thin wiring
  layer.
- Made the SQLite layer thread-safe (shared connection guarded by a lock, WAL
  mode) so the web thread can read while the scheduler writes.
- Pinned the container to Python 3.13 for deterministic builds.

### Fixed
- The scheduler no longer crashes the whole process when a notification fails
  (e.g. an ntfy 403). It logs the error and retries next interval — previously,
  under a Docker restart policy, a bad notifier caused a speed-test-every-few-
  seconds restart storm.
- `.gitignore` no longer merges `pyrightconfig.json` and `graphs/*.png` onto one
  line, so generated graphs are correctly ignored.
- README clone URLs now point at this repository.

[Unreleased]: https://github.com/VincentFlagg/netmon/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/VincentFlagg/netmon/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/VincentFlagg/netmon/releases/tag/v1.0.0
