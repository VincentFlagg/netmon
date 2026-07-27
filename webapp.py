import csv
import io
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import sqlite
from service import Monitor

log = logging.getLogger("netmon")


def _metric_to_dict(m, device_count: int) -> dict:
    return {
        "id": str(m.id),
        "timestamp": m.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp_iso": m.timestamp.astimezone().isoformat(),
        "download_mbps": round(m.download / 10**6, 2),
        "upload_mbps": round(m.upload / 10**6, 2),
        "ping_ms": round(m.ping, 2),
        "client": m.client,
        "server": m.server,
        "downloaded_mb": round(m.bytes_received / 10**6, 1),
        "uploaded_mb": round(m.bytes_sent / 10**6, 1),
        "share": m.share,
        "devices": device_count,
    }


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>netmon</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f1117; color: #e6e8ee; line-height: 1.5;
  }
  header {
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 12px;
    padding: 20px 24px; border-bottom: 1px solid #232733; background: #151824;
  }
  header h1 { margin: 0; font-size: 20px; letter-spacing: .5px; }
  header h1 span { color: #6ea8fe; }
  .meta { font-size: 13px; color: #8b93a7; }
  main { max-width: 1000px; margin: 0 auto; padding: 24px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; }
  .card {
    background: #151824; border: 1px solid #232733; border-radius: 10px; padding: 16px;
  }
  .card .label { font-size: 12px; text-transform: uppercase; letter-spacing: .5px; color: #8b93a7; }
  .card .value { font-size: 26px; font-weight: 600; margin-top: 4px; }
  .card .unit { font-size: 14px; color: #8b93a7; font-weight: 400; }
  .toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 24px 0 12px; }
  button, a.btn {
    background: #2451c9; color: #fff; border: none; border-radius: 8px;
    padding: 9px 16px; font-size: 14px; cursor: pointer; text-decoration: none; display: inline-block;
  }
  button:hover, a.btn:hover { background: #2f5fe0; }
  button:disabled { background: #313749; cursor: not-allowed; }
  a.btn.secondary { background: #232733; }
  .status-pill { font-size: 13px; color: #8b93a7; }
  .status-pill.busy { color: #f0c24b; }
  .status-pill.err { color: #f0685f; }
  section h2 { font-size: 15px; color: #b7bed0; margin: 28px 0 12px; font-weight: 600; }
  img#graph { width: 100%; border: 1px solid #232733; border-radius: 10px; background: #fff; }
  .table-wrap { overflow-x: auto; border: 1px solid #232733; border-radius: 10px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { padding: 9px 12px; text-align: right; white-space: nowrap; border-bottom: 1px solid #1e2230; }
  th { background: #151824; color: #8b93a7; font-weight: 600; position: sticky; top: 0; }
  td:first-child, th:first-child { text-align: left; }
  tr:last-child td { border-bottom: none; }
  .empty { color: #8b93a7; padding: 20px; text-align: center; }
  footer { text-align: center; color: #5b6274; font-size: 12px; padding: 24px; }
</style>
</head>
<body>
<header>
  <h1>net<span>mon</span></h1>
  <div class="meta" id="meta">notifier: … · loading…</div>
</header>
<main>
  <div class="cards" id="cards"></div>

  <div class="toolbar">
    <button id="runBtn" onclick="runNow()">Run speed test now</button>
    <a class="btn secondary" href="/export.csv">Download CSV</a>
    <span class="status-pill" id="statusPill"></span>
  </div>

  <section>
    <h2>Last 24 hours</h2>
    <img id="graph" alt="24-hour network graph" src="/graph.png">
  </section>

  <section>
    <h2>History</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th><th>Down (Mbps)</th><th>Up (Mbps)</th><th>Ping (ms)</th>
            <th>Devices</th><th>Down (MB)</th><th>Up (MB)</th><th>Server</th>
          </tr>
        </thead>
        <tbody id="history"><tr><td colspan="8" class="empty">Loading…</td></tr></tbody>
      </table>
    </div>
  </section>
</main>
<footer>netmon · self-hosted local network monitor</footer>

<script>
function card(label, value, unit) {
  return `<div class="card"><div class="label">${label}</div>` +
         `<div class="value">${value}<span class="unit"> ${unit||''}</span></div></div>`;
}

async function refresh() {
  try {
    const [statusR, latestR, histR] = await Promise.all([
      fetch('/api/status'), fetch('/api/latest'), fetch('/api/history?limit=50')
    ]);
    const status = await statusR.json();
    const latest = await latestR.json();
    const hist = await histR.json();

    document.getElementById('meta').textContent =
      `notifier: ${status.notifier} · next full report in ${status.next_report_in} cycle(s)`;

    const cards = document.getElementById('cards');
    if (latest && latest.download_mbps !== undefined) {
      cards.innerHTML =
        card('Download', latest.download_mbps, 'Mbps') +
        card('Upload', latest.upload_mbps, 'Mbps') +
        card('Ping', latest.ping_ms, 'ms') +
        card('Devices', latest.devices, '') +
        card('ISP', latest.client, '');
    } else {
      cards.innerHTML = '<div class="card"><div class="label">Status</div><div class="value" style="font-size:16px">No measurements yet</div></div>';
    }

    const tbody = document.getElementById('history');
    if (hist.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty">No measurements yet</td></tr>';
    } else {
      tbody.innerHTML = hist.map(r =>
        `<tr><td>${r.timestamp}</td><td>${r.download_mbps}</td><td>${r.upload_mbps}</td>` +
        `<td>${r.ping_ms}</td><td>${r.devices}</td><td>${r.downloaded_mb}</td>` +
        `<td>${r.uploaded_mb}</td><td>${r.server}</td></tr>`).join('');
    }

    updateStatus(status);
    // bust the browser cache so the graph refreshes
    document.getElementById('graph').src = '/graph.png?t=' + Date.now();
  } catch (e) {
    console.error(e);
  }
}

function updateStatus(status) {
  const pill = document.getElementById('statusPill');
  const btn = document.getElementById('runBtn');
  if (status.running) {
    pill.textContent = 'Measurement in progress…';
    pill.className = 'status-pill busy';
    btn.disabled = true;
  } else if (status.last_error) {
    pill.textContent = 'Last run error: ' + status.last_error;
    pill.className = 'status-pill err';
    btn.disabled = false;
  } else {
    pill.textContent = status.last_run ? ('Last run: ' + status.last_run) : 'Idle';
    pill.className = 'status-pill';
    btn.disabled = false;
  }
}

async function runNow() {
  const btn = document.getElementById('runBtn');
  btn.disabled = true;
  try {
    await fetch('/api/run', { method: 'POST' });
  } catch (e) { console.error(e); }
  // poll a bit faster until the run finishes
  const fast = setInterval(async () => {
    const s = await (await fetch('/api/status')).json();
    updateStatus(s);
    if (!s.running) { clearInterval(fast); refresh(); }
  }, 2000);
}

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>"""


def make_handler(db: sqlite.DB, monitor: Monitor, notifier_name: str):

    class Handler(BaseHTTPRequestHandler):
        server_version = "netmon/1.0"

        def log_message(self, fmt, *args):
            log.debug("web: " + fmt, *args)

        def _send(self, code: int, body: bytes, content_type: str, extra_headers: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code: int = 200):
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            path = urlparse(self.path)
            route = path.path

            if route == "/" or route == "/index.html":
                self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")

            elif route == "/api/status":
                st = monitor.state
                self._json({
                    "notifier": notifier_name,
                    "running": st.running,
                    "last_run": st.last_run.astimezone().strftime("%Y-%m-%d %H:%M:%S") if st.last_run else None,
                    "last_error": st.last_error,
                    "next_report_in": st.next_report_in,
                })

            elif route == "/api/latest":
                latest = db.get_latest_with_device_count()
                self._json(_metric_to_dict(*latest) if latest else {})

            elif route == "/api/history":
                qs = parse_qs(path.query)
                try:
                    limit = int(qs.get("limit", ["50"])[0])
                except ValueError:
                    limit = 50
                metrics, counts = db.get_recent_with_device_counts(limit)
                self._json([_metric_to_dict(m, c) for m, c in zip(metrics, counts)])

            elif route == "/graph.png":
                try:
                    png = monitor.render_graph_png()
                except Exception as e:
                    log.error(f"Graph render failed: {e}")
                    png = None
                if png is None:
                    self._send(404, b"no data", "text/plain; charset=utf-8")
                else:
                    self._send(200, png, "image/png", {"Cache-Control": "no-store"})

            elif route == "/export.csv":
                metrics, counts = db.get_recent_with_device_counts(500)
                buf = io.StringIO()
                writer = csv.writer(buf)
                writer.writerow([
                    "timestamp", "download_mbps", "upload_mbps", "ping_ms", "devices",
                    "downloaded_mb", "uploaded_mb", "client", "server",
                ])
                for m, c in zip(metrics, counts):
                    d = _metric_to_dict(m, c)
                    writer.writerow([
                        d["timestamp"], d["download_mbps"], d["upload_mbps"], d["ping_ms"],
                        d["devices"], d["downloaded_mb"], d["uploaded_mb"], d["client"], d["server"],
                    ])
                self._send(
                    200, buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8",
                    {"Content-Disposition": "attachment; filename=netmon_metrics.csv"},
                )

            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")

        do_HEAD = do_GET

        def do_POST(self):
            route = urlparse(self.path).path
            if route == "/api/run":
                if monitor.state.running:
                    self._json({"started": False, "reason": "already running"}, code=409)
                    return
                # Speed tests take ~30s; run off the request thread and let the
                # dashboard poll /api/status for completion.
                threading.Thread(
                    target=self._safe_manual_run, name="netmon-manual-run", daemon=True
                ).start()
                self._json({"started": True})
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")

        @staticmethod
        def _safe_manual_run():
            try:
                monitor.run_manual_cycle()
            except Exception as e:
                log.error(f"Manual run thread failed: {e}")

    return Handler


def start_web_server(db: sqlite.DB, monitor: Monitor, host: str, port: int, notifier_name: str) -> ThreadingHTTPServer:
    """Start the dashboard HTTP server on a daemon thread and return it."""
    handler = make_handler(db, monitor, notifier_name)
    httpd = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=httpd.serve_forever, name="netmon-web", daemon=True)
    thread.start()
    log.info(f"Web dashboard listening on http://{host}:{port}")
    return httpd
