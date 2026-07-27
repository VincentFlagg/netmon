# Installing netmon on a UGREEN NAS with Dockge

A step-by-step guide to running **netmon** on a **UGREEN DXP2800** (UGOS Pro)
using **Dockge** (tested against v1.0.38). Everything is done from the Dockge
web UI — no SSH required.

The DXP2800 uses an Intel N100 CPU (`amd64`), which matches the pre-built
image published to the GitHub Container Registry (GHCR), so there's nothing to
compile — Dockge just pulls and runs it.

**End result:** netmon runs 24/7 on your NAS, sends status updates to Telegram
or Discord, and serves a dashboard at `http://<your-nas-ip>:8080`.

---

## Prerequisites

Before you start, make sure you have:

1. **A UGREEN NAS running UGOS Pro** with the **Docker** app installed and
   **Dockge** already set up and reachable in your browser.
2. **A notifier** — *one* of:
   - **Telegram:** a bot token (from [@BotFather](https://t.me/botfather)) and
     your numeric chat ID, **or**
   - **Discord:** a channel webhook URL
     (Server Settings → Integrations → Webhooks → New Webhook → Copy URL), **or**
   - **ntfy:** subscribe to a unique topic in the ntfy app and use its URL
     (e.g. `https://ntfy.sh/my-netmon-a8f3z2`) — no bot/webhook setup, and it
     self-hosts nicely alongside your NAS, **or**
   - **Nothing** — set `NOTIFIER: "none"` to skip push notifications entirely and
     just use the web dashboard (see
     [Don't want Telegram or Discord?](#dont-want-telegram-or-discord) below).
3. **An AI endpoint** *(optional)* — an API key for any OpenAI-compatible service
   (e.g. OpenAI `gpt-4o-mini`), *or* a local LLM server URL (Ollama, LM Studio).
   Leave the `AI_*` values empty to run without the sarcastic AI commentary.
   See the [main README](README.md#4-configure-env) for how to get each of these.

---

## Step 1 — Make the image pullable

The image lives at `ghcr.io/vincentflagg/netmon:latest`. GHCR packages are
**private by default**, so pick one:

**Option A — make it public (simplest):**

1. On GitHub, go to your profile → **Packages** → **netmon**
   (direct link: `https://github.com/users/VincentFlagg/packages/container/netmon/settings`).
2. Scroll to **Danger Zone** → **Change visibility** → **Public** → confirm.

Your NAS can now pull it with no login. Skip to Step 2.

**Option B — keep it private:** you'll need to log Docker in on the NAS with a
GitHub personal access token that has the `read:packages` scope. This requires
SSH; Option A is much easier for a home NAS.

---

## Step 2 — Have your secrets ready

Open a note and paste in the values you'll need in Step 4:

- `AI_API_KEY`, `AI_MODEL`, `AI_BASE_URL`
- For Telegram: `TG_BOT_TOKEN`, `TG_CHAT_ID`
- For Discord: `DISCORD_WEBHOOK_URL`

---

## Step 3 — Create the stack in Dockge

1. Open **Dockge** in your browser.
2. Click **+ Compose** (Create a new stack).
3. Set the **Stack Name** to `netmon`.
4. In the compose editor, **delete the placeholder content** and paste the
   file below exactly.

```yaml
services:
  netmon:
    image: ghcr.io/vincentflagg/netmon:latest
    container_name: netmon
    restart: unless-stopped

    # REQUIRED so the nmap LAN scan can see real devices on your network.
    network_mode: host

    cap_add:
      - NET_RAW
      - NET_ADMIN

    environment:
      # --- AI (any OpenAI-compatible endpoint) ---
      AI_API_KEY: "sk-REPLACE_ME"
      AI_MODEL: "gpt-4o-mini"
      AI_BASE_URL: "https://api.openai.com/v1"

      # --- Notifier: "telegram" (default) or "discord" ---
      NOTIFIER: "telegram"
      TG_BOT_TOKEN: "123456:AA_REPLACE_ME"
      TG_CHAT_ID: "123456789"
      # For Discord instead, set NOTIFIER: "discord" and fill this in:
      # DISCORD_WEBHOOK_URL: "https://discord.com/api/webhooks/XXX/YYY"

      # --- Storage & web dashboard ---
      DB_PATH: "/data/metrics.sql"
      WEB_HOST: "0.0.0.0"
      WEB_PORT: "8080"          # change if 8080 is already used on your NAS

    volumes:
      - ./data:/data
```

> This is the same content as [`dockge-stack.yml`](dockge-stack.yml) in the repo.

---

## Step 4 — Fill in your values

In the pasted YAML, replace the placeholders under `environment:`:

| Key | Set it to |
| :-- | :-- |
| `AI_API_KEY` | Your OpenAI-compatible API key |
| `AI_MODEL` | e.g. `gpt-4o-mini`, or your local model name |
| `AI_BASE_URL` | `https://api.openai.com/v1`, or your local server URL |
| `NOTIFIER` | `telegram`, `discord`, `ntfy`, or `none` |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | Your Telegram values (if using Telegram) |
| `DISCORD_WEBHOOK_URL` | Your webhook (if using Discord — and uncomment the line) |
| `NTFY_URL` | Your ntfy topic URL (if using ntfy — uncomment) |
| `NTFY_TOKEN` *or* `NTFY_USER`+`NTFY_PASSWORD` | Only for protected ntfy topics — a token, or username/password (public topics need neither) |
| `WEB_PORT` | Leave `8080`, or change it if that port is taken |

Leave `DB_PATH`, `WEB_HOST`, the `volumes:`, `network_mode:` and `cap_add:`
sections as they are.

### Don't want Telegram or Discord?

Set the notifier to `none` and delete the notifier/AI lines. netmon still runs
its speed tests and LAN scans and stores everything — you just read the results
on the dashboard instead of getting pushed messages. A minimal `environment:`
block for dashboard-only mode looks like this:

```yaml
    environment:
      NOTIFIER: "none"
      DB_PATH: "/data/metrics.sql"
      WEB_HOST: "0.0.0.0"
      WEB_PORT: "8080"
```

No `AI_*`, no `TG_*`, no `DISCORD_*` needed. (You can still add the `AI_*` values
if you want the AI report text on the graph you'd get every 4 hours — but with
`none` there's nowhere to send it, so most people leave them out.)

---

## Step 5 — Deploy

Click **Deploy** (or **Save** then **Start**) in Dockge.

Dockge pulls the image and starts the container. The first pull takes a minute
or two; after that you'll see the container go **green / running**.

---

## Step 6 — Open the dashboard

In a browser on the same network, go to:

```
http://<your-nas-ip>:8080
```

(Replace `<your-nas-ip>` with your NAS's LAN IP, and the port if you changed
`WEB_PORT`.) You should see the netmon dashboard. The first speed test runs
immediately; the history table and 24-hour graph fill in over time. Use
**Run speed test now** to trigger one on demand.

---

## Step 7 — Verify it's working

- **Dashboard:** the metric cards show real download/upload/ping numbers.
- **Notifier:** within a cycle you should get a "Network Status Update" message
  in Telegram/Discord.
- **Logs:** in Dockge, open the `netmon` stack and check the log panel. You want
  to see `The bot has been started.` and `Web dashboard listening on ...`, then
  `Speedtest has been added: ...` lines.

---

## Troubleshooting

**`denied` / `manifest unknown` when pulling the image**
The GHCR package is still private. Redo **Step 1, Option A** (make it public),
then redeploy.

**Dashboard shows "Devices online: 0" or the log shows a device-scan error**
The `nmap` scan couldn't find your LAN interface. This is almost always the
interface-detection step. Check the logs for `No active ethernet interface
found via nmap --iflist`. Make sure `network_mode: host` is present (it must be
— it's how the container sees your real LAN). If it's there and still failing,
your NAS's NIC may not be reported as a plain "ethernet ... up" interface;
open an issue with your `docker logs netmon` output.

**Can't reach `http://<nas-ip>:8080`**
- Confirm the container is running (green) in Dockge.
- Make sure nothing else on the NAS uses port 8080; if it does, change
  `WEB_PORT` and redeploy, then use the new port.
- You must be on the same LAN as the NAS.

**AI report says "AI commentary unavailable"**
Your `AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` are wrong or the endpoint is
unreachable. Speed tests and the graph still work; fix the AI values and
redeploy. (netmon is designed to keep reporting even when the AI backend is
down.)

**Telegram/Discord messages don't arrive**
Double-check `NOTIFIER` matches the credentials you filled in, and that the
token/webhook is valid. For Telegram, make sure you've messaged the bot at
least once so it can message you back.

---

## Updating netmon

When a new image is published:

1. In Dockge, open the `netmon` stack.
2. Click **Update** (Dockge pulls the newest `:latest` and recreates the
   container).

Your history is safe — the SQLite database lives in the bind-mounted `./data`
folder and survives updates.

---

## Data & backups

The database and generated graphs live in the stack's `data/` folder (the
`./data:/data` bind mount). Back that folder up and you keep your full metric
history. To move netmon to another host, copy `data/` along with the stack.
