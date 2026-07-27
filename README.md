<p align="center">
  <img src="assets/logo.png" alt="netmon logo" width="180" />
</p>

<h1 align="center">netmon</h1>

<p align="center">
  <b>Self-hosted local network monitor with 24-hour speed charts & sarcastic AI commentary delivered straight to Telegram or Discord.</b>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-8bc34a?style=for-the-badge" alt="License MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.13+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/uv-managed-DE5FE9?style=for-the-badge&logo=uv&logoColor=white" alt="uv">
  <img src="https://img.shields.io/badge/Telegram-Bot_API-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram">
  <img src="https://img.shields.io/badge/Discord-Webhook-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord">
  <img src="https://img.shields.io/badge/SQLite-Storage-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite">
  <img src="https://img.shields.io/badge/Matplotlib-Graphs-11557c?style=for-the-badge" alt="Matplotlib">
</p>

---

A lightweight local bot that runs a speed test on your network every 30 minutes, scans active devices on your LAN using `nmap`, and logs everything to a local SQLite database.

Every 4 hours, it delivers a **detailed report** complete with a 24-hour trend graph and a sarcastic, LLM-generated commentary on your network's behavior (*"someone's hogging the bandwidth again"*).

It also serves a **local web dashboard** (latest metrics, the 24-hour graph, a history table, an on-demand "run speed test now" button, and CSV export) that runs alongside Telegram/Discord — and ships with a **Dockerfile + Compose file** for one-command deployment.

> [!NOTE]
> **100% Private & Self-Hosted:** No external metric servers involved — everything runs locally on your machine or Raspberry Pi. Only text reports and graph images are dispatched to your chosen notifier (Telegram or Discord).

---

## Features & Workflow

Every 30 minutes (`SLEEP_TIME` in `main.py`, default 1800 seconds):

1. **Speed Test:** Measures download/upload speeds, ping latency, ISP, and test server details using `speedtest-cli` (see [the note on measurement mode](#a-note-on-measurement-mode)).
2. **LAN Scan:** Scans the local subnet using `nmap` ARP scan to count active connected devices.
3. **Local Storage:** Saves metrics & device tallies directly to a local `metrics.sql` SQLite database.
4. **Status Alert:** Sends a concise status update to your chosen notifier (*"all good"* or *"line is dying"*).
5. **24h AI Report:** Every 8th cycle (every 4h), generates a **24-hour trend graph** via `matplotlib` alongside a sarcastic LLM analysis of network load and speed fluctuations.

---

## Tech Stack

| Technology | Purpose |
| :--- | :--- |
| **Python 3.13+** (via `uv`) | Core runtime |
| **SQLite** | Local metrics persistence (`metrics.sql`) |
| **`speedtest-cli`** | Network bandwidth and ping measurements |
| **`nmap`** | Subnet ARP scanning for device discovery |
| **`matplotlib`** | 24-hour metrics visualization |
| **OpenAI-compatible API** | Sarcastic report & trend analysis (cloud OpenAI or a local LLM) |
| **Telegram API / Discord Webhooks** | Alert and graph report delivery |

---

## Requirements

* **OS:** macOS or Linux (`nmap --iflist` required; Windows not supported out of the box).
* **[uv](https://docs.astral.sh/uv/)** — manages the Python version, virtualenv, and locked dependencies for you. No manual `python3`/`venv`/`pip` juggling.
* **System Binaries:** `nmap` and `speedtest-cli` installed system-wide.
* **Passwordless `sudo` for `nmap`** — device counting needs a real ARP scan (raw sockets), which requires root; see one-time setup below.
* **Tokens:** either a Telegram Bot Token + Chat ID, *or* a Discord Webhook URL (see [Notifications](#notifications-telegram-or-discord)), plus an API key for your OpenAI-compatible provider (not needed if you point `AI_BASE_URL` at a local LLM server).

---

## Quick Start

### 1. System Dependencies

**macOS (Homebrew):**
```bash
brew install nmap speedtest-cli
```

**Linux (Debian/Ubuntu):**
```bash
sudo apt update && sudo apt install -y nmap speedtest-cli
```

### 2. Allow Passwordless `nmap` (one-time)

Device counting runs `nmap` as root for a real ARP scan — without it, host discovery silently falls back to ordinary TCP probing and undercounts devices that don't answer on common ports. Since the bot runs unattended, `sudo` needs to work without a password prompt on every cycle:

```bash
echo "$(whoami) ALL=(root) NOPASSWD: $(command -v nmap)" | sudo tee /etc/sudoers.d/netmon-nmap
sudo chmod 440 /etc/sudoers.d/netmon-nmap
```

This grants passwordless `sudo` only for the `nmap` binary — not your whole account.

### 3. Clone & Setup Environment

Install [`uv`](https://docs.astral.sh/uv/) if you don't have it yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then:

```bash
git clone https://github.com/VincentFlagg/netmon.git
cd netmon
uv sync
```

`uv sync` downloads the pinned Python version (see `.python-version`) if you don't already have it, creates `.venv`, and installs the exact locked dependency versions from `uv.lock`. No system `python3`, no manual venv activation.

### 4. Configure `.env`

Copy the template file and fill in your secrets:

```bash
cp .env.example .env
```

`.env` variables:

| Variable | Description |
| :--- | :--- |
| `AI_API_KEY` | *Optional (all-or-nothing).* Your LLM provider API key (any string works for most local servers) |
| `AI_MODEL` | *Optional.* Model name (e.g. `gpt-4o-mini`, or a local model name — see below) |
| `AI_BASE_URL` | *Optional.* Base API URL (e.g., `https://api.openai.com/v1`, or your local server's URL) |
| `NOTIFIER` | `telegram` (default), `discord`, `ntfy`, or `none` (dashboard-only — see below) |
| `TG_BOT_TOKEN` | Telegram bot token from `@BotFather` — required if `NOTIFIER=telegram` |
| `TG_CHAT_ID` | Your Telegram Chat ID — required if `NOTIFIER=telegram` |
| `DISCORD_WEBHOOK_URL` | Discord channel webhook URL — required if `NOTIFIER=discord` |
| `NTFY_URL` | Full ntfy topic URL (e.g. `https://ntfy.sh/my-topic`) — required if `NOTIFIER=ntfy` |
| `NTFY_TOKEN` | *Optional.* ntfy bearer access token for protected topics |
| `NTFY_USER` / `NTFY_PASSWORD` | *Optional.* ntfy username + password (HTTP Basic auth) — an alternative to `NTFY_TOKEN` |
| `DB_PATH` | **Required.** SQLite database file path (e.g. `metrics.sql`) |
| `REQUEST_TIMEOUT` | *Optional.* HTTP timeout in seconds for Telegram/Discord requests (positive integer, default `30`) |
| `WEB_ENABLED` | *Optional.* Serve the web dashboard (`true`/`false`, default `true`) |
| `WEB_HOST` | *Optional.* Dashboard bind address (default `0.0.0.0`; use `127.0.0.1` for localhost-only) |
| `WEB_PORT` | *Optional.* Dashboard port (default `8080`) |

> [!TIP]
> **You're not locked into OpenAI.** `ai.py` talks to any OpenAI-compatible endpoint, so a local inference server (e.g. [Ollama](https://ollama.com), LM Studio) works too — just point `AI_BASE_URL` at it. For report quality that holds up, use a model with **at least ~7B parameters**; a solid local pick is **Gemma 4 12B at 4-bit (QAT) quantization** (`gemma4:12b-it-qat` via Ollama), which fits comfortably on 16GB of RAM.

### 5. Run the Bot

```bash
uv run main.py
```

`uv run` always uses this project's own `.venv` and pinned Python version, so it can't accidentally run against your system `python3`.

> [!TIP]
> Run the bot inside `tmux`/`screen` or set it up as a system service (`systemd`/`launchd`) to keep it running 24/7 in the background.

---

## Web Dashboard

Alongside the Telegram/Discord alerts, netmon serves a lightweight **local web dashboard** — no extra dependencies, it's built on Python's standard library. Once the bot is running, open:

```
http://<host>:8080
```

The dashboard shows:

* **Latest metrics** — download, upload, ping, device count, and ISP at a glance.
* **24-hour graph** — the same `matplotlib` chart that goes to your notifier, refreshed automatically.
* **History table** — the most recent measurements.
* **Run speed test now** — a button that triggers an immediate measurement on demand (it stores the result and sends a mini report, without disturbing the scheduled 4-hour detailed-report cadence).
* **Download CSV** — export the recent metrics as a spreadsheet-friendly file.

The page auto-refreshes every 30 seconds. Configure it with `WEB_ENABLED`, `WEB_HOST`, and `WEB_PORT` (see the `.env` table above), or set `WEB_ENABLED=false` to turn it off entirely.

> [!WARNING]
> The dashboard has **no authentication** and exposes a button that triggers speed tests plus a data export. Keep it on a trusted LAN. To restrict it to the local machine, set `WEB_HOST=127.0.0.1`; to expose it more widely, put it behind a reverse proxy that adds authentication.

---

## Docker

netmon ships with a `Dockerfile` and `docker-compose.yml`. The image bundles `nmap`, `speedtest-cli`, and the locked Python dependencies, so you don't install anything on the host except Docker itself.

```bash
cp .env.example .env    # fill in your tokens / API key
docker compose up -d --build
```

Then open `http://localhost:8080`. The SQLite database is persisted in a named volume (`netmon-data`), so your history survives rebuilds.

### NAS deployment (Dockge / Portainer / Container Manager)

For a NAS, use the ready-made `docker-compose.nas.yml` variant instead — it swaps the named volume for a bind mount (`./data`, so the database is visible on your NAS shares and easy to back up) and makes the dashboard port overridable via `WEB_PORT`:

```bash
# in your stack manager's stacks dir, e.g. /opt/stacks or /volume1/docker/stacks
git clone https://github.com/VincentFlagg/netmon.git netmon
cd netmon && cp .env.example .env   # then edit .env
docker compose -f docker-compose.nas.yml up -d --build
```

In Dockge/Portainer, point the stack at `docker-compose.nas.yml` and deploy from the UI. Set `WEB_PORT` in `.env` (e.g. `WEB_PORT=8081`) if `8080` clashes with your NAS UI. Host networking still applies — see the note above.

#### Paste-and-go stack (no source checkout)

> **UGREEN NAS + Dockge users:** there's a full click-by-click walkthrough in **[INSTALL-NAS.md](INSTALL-NAS.md)**.

If you'd rather create a stack in the Dockge/Portainer UI and just paste a compose file, use `dockge-stack.yml`. It pulls a **pre-built image from GHCR** instead of building from source, and carries its config inline under `environment:` so there's no separate `.env` to manage — paste it, edit the placeholder values, deploy.

This requires the image to be published first. The included GitHub Action (`.github/workflows/docker-publish.yml`) builds and pushes it to `ghcr.io/<owner>/netmon` on every push (and on `v*` tags). After the first run, set the GHCR package to **Public** so your NAS can pull it without logging in.

> [!IMPORTANT]
> **Host networking is required.** The Compose file uses `network_mode: host` plus the `NET_RAW`/`NET_ADMIN` capabilities so the `nmap` ARP scan can see real devices on your LAN. A container on Docker's default bridge network is behind NAT and can only scan the bridge subnet, which would make device counts meaningless.
>
> Host networking is **Linux-only** — it does not work on Docker Desktop for macOS/Windows. On those platforms, run netmon directly with `uv run main.py` instead (the LAN scan needs to be on the same network as your devices anyway).

Because the container already runs as root, the passwordless-`sudo` setup from the [Quick Start](#2-allow-passwordless-nmap-one-time) is **not** needed inside Docker.

---

## Notifications: Telegram, Discord, ntfy, or none

netmon supports three notification backends, selected via the `NOTIFIER` variable in `.env`. Only one is needed — or you can turn notifications off entirely.

### No notifier (dashboard-only)

Don't want any push service? Set `NOTIFIER=none`. netmon still runs its speed tests and LAN scans on schedule and stores everything, but pushes nothing out — you read the results on the [web dashboard](#web-dashboard) instead. In this mode you don't need any notifier values, and the AI variables are optional too (leave all three `AI_*` empty to skip the AI commentary). `NOTIFIER=none` requires `WEB_ENABLED=true` (otherwise there'd be no output at all).

### ntfy

[ntfy](https://ntfy.sh) is a dead-simple pub-sub push service — great for a NAS, and self-hostable. There's no bot or webhook setup: just pick a hard-to-guess topic name.

1. Install the ntfy app (Android/iOS) or open the web app, and **subscribe to a topic** — any unique name, e.g. `my-netmon-a8f3z2`.
2. In `.env`:
   ```
   NOTIFIER=ntfy
   NTFY_URL=https://ntfy.sh/my-netmon-a8f3z2
   ```

**Protected topics** (a reserved topic on ntfy.sh, or a server with auth) need credentials — use *one* of:
```
NTFY_TOKEN=tk_...            # a bearer access token, OR
NTFY_USER=myuser             # username + password (HTTP Basic auth)
NTFY_PASSWORD=mypassword
```
A public/unreserved topic needs no auth at all. Point `NTFY_URL` at your own server instead (e.g. `https://ntfy.example.com/netmon`) if you self-host. Reports are sent as plain text (the HTML formatting is stripped), and the 4-hour graph arrives as an image attachment.

### Telegram (default)

1. Message [`@BotFather`](https://t.me/botfather) on Telegram and send `/newbot`, following the prompts to get a **bot token**.
2. Get your **Chat ID** — the simplest way is to message your new bot, then visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and read the `chat.id` field from the JSON response.

### Telegram (default)

1. Message [`@BotFather`](https://t.me/botfather) on Telegram and send `/newbot`, following the prompts to get a **bot token**.
2. Get your **Chat ID** — the simplest way is to message your new bot, then visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and read the `chat.id` field from the JSON response.
3. In `.env`:
   ```
   NOTIFIER=telegram
   TG_BOT_TOKEN=123456789:AAHfoo...
   TG_CHAT_ID=987654321
   ```

If `NOTIFIER` is left unset, netmon defaults to Telegram, so existing setups keep working with no changes.

### Discord

1. In your target Discord channel: **Server Settings → Integrations → Webhooks → New Webhook**, then copy the webhook URL. No bot invite or permissions setup needed.
2. In `.env`:
   ```
   NOTIFIER=discord
   DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxxx/yyyy
   ```

Discord delivery reuses the same report content as Telegram — the existing HTML formatting (`<b>`, `<code>`, `<pre>`) is automatically converted to Discord markdown, so reports render correctly in either service without any changes to the AI prompt.

> [!WARNING]
> Treat both the Telegram bot token and the Discord webhook URL as secrets — anyone with either can post messages through your bot/webhook. Don't commit them to version control (`.env` is already git-ignored).

---

## A Note on Measurement Mode

netmon runs `speedtest --secure --single --json` (see `runner.py`) — the `--single` flag means the test uses **one TCP connection**. This is deliberate: a single stream approximates what one real application on your network would actually get, since it is subject to the same window-size and packet-loss limits any ordinary download faces.

Multi-threaded speed tests (including Ookla's official CLI, and the speedtest.net web UI) open many parallel connections instead. That measures something different — the practical ceiling of your line — and will report noticeably higher numbers on fast connections. Neither figure is "wrong"; they answer different questions.

Two consequences worth knowing:

* **Don't compare netmon's numbers directly against speedtest.net in a browser.** The browser test is multi-threaded and will read higher. That gap is methodology, not a fault in your line.
* **On very fast links (roughly 500 Mbps+), expect single-stream figures to sit well below your subscribed speed.** Beyond the methodology gap, `speedtest-cli` is pure Python, so at gigabit speeds its own CPU overhead starts contributing too.

Since netmon exists to track *trends*, consistency matters more than peak numbers: keep one measurement method for the lifetime of your database. Swapping the backend mid-history puts a step change in your 24-hour graph that the AI commentary will faithfully report as a real speed jump.

---

## Example Output

### Hourly Short Status Update

```text
Network Status Update
Time: 2026-07-21 14:00:00
ISP: MyISP | Server: New York

Devices online: 7
Download: 145.2 Mbps
Upload: 62.1 Mbps
Latency: 14.8 ms

Traffic used: 160.0 MB down / 70.0 MB up

Current status: Good speed and low latency
```

### 4-Hour Detailed Report (With Graph & AI Analysis)

Every 4 hours, the bot sends a **24-hour matplotlib graph** accompanied by a sarcastic LLM-generated report:

<p align="center">
  <img src="assets/example_graph.png" alt="24h Network Speed Test Graph" width="650" />
</p>

```html
<b>Network Speed Test Report (24h Analysis)</b>

Client: <b>MyISP</b>
Server: <b>New York</b>

<b>Latest Test Metrics</b>
<pre>
Download: 178.5 Mbps
Upload: 45.2 Mbps
Ping: 23.1 ms
Devices Online: 9
</pre>

<b>24-Hour Dynamics Analysis</b>
Over the last 24 hours, the download speed averaged <code>140 Mbps</code>, but we saw a massive drop to <code>20 Mbps</code> at 8:00 PM right as device count jumped from <code>4</code> to <code>11 devices</code>. Clearly, someone's hogging the bandwidth or the ISP's mice were busy chewing on the fiber line again. Latency remained stable except for a brief spike during peak hours.

<b>Data Transfer (Latest Test)</b>
<pre>
Downloaded: 160.0 MB
Uploaded: 70.0 MB
</pre>

<b>Conclusion</b>
Expect periodic speed drops whenever local freeloaders stream 4K movies or the ISP potato infrastructure struggles.
```

---

## Project Structure

```text
netmon/
├── assets/                        # Logo & documentation media assets
├── graphs/                        # Generated 24h matplotlib graph images
├── main.py                        # Entry point: wires everything & runs the scheduler loop
├── service.py                     # Monitor: one measurement cycle, shared by loop & web
├── webapp.py                      # Standard-library web dashboard (HTTP server + UI)
├── runner.py                      # Speedtest-cli and nmap scan execution & parsing
├── sqlite.py                      # SQLite database operations & schema management
├── models.py                      # Domain data models (NetworkMetric, SpeedTest)
├── graphs.py                      # Matplotlib graph rendering engine
├── ai.py                          # OpenAI API client & sarcastic text generator
├── tg.py                          # Telegram bot dispatch helper
├── discord_hook.py                # Discord webhook dispatch helper
├── ntfy_hook.py                   # ntfy topic dispatch helper
├── config.py                      # Environment variable validation & config
├── notifier.py                    # Notifier protocol & shared chat-action enum
├── Dockerfile                     # Container image (bundles nmap + speedtest-cli)
├── docker-compose.yml             # One-command deploy with host networking & volume
├── pyproject.toml                 # Project metadata & dependencies
├── uv.lock                        # Locked, reproducible dependency versions
└── LICENSE                        # MIT License file
```

---

## License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for more details.
