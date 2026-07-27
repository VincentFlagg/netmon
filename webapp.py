import concurrent.futures
import csv
import io
import json
import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
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


# Reverse-DNS hostnames, cached (empty string cached too, so unresolved IPs
# aren't retried forever). Best-effort: many LAN devices have no PTR record.
_hostname_cache: dict[str, str] = {}


def _ptr(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def _resolve_hostnames(ips: list[str], budget: float = 2.0) -> dict[str, str]:
    todo = [ip for ip in ips if ip not in _hostname_cache]
    if todo:
        ex = ThreadPoolExecutor(max_workers=min(16, len(todo)))
        futs = {ex.submit(_ptr, ip): ip for ip in todo}
        done, notdone = concurrent.futures.wait(futs, timeout=budget)
        for f in done:
            _hostname_cache[futs[f]] = f.result()
        for f in notdone:
            _hostname_cache.setdefault(futs[f], "")  # give up for now, don't hang
        ex.shutdown(wait=False)
    return {ip: _hostname_cache.get(ip, "") for ip in ips}


def _devices_payload(db: sqlite.DB) -> dict:
    """Devices seen in the last 24h, newest scan first, with per-device
    last-seen and online (present in the most recent scan) status."""
    history = db.get_device_history(24)
    if not history:
        return {"scan_time": None, "online_count": 0, "devices": []}

    latest_ts, latest_ips, latest_lats = history[0]
    latest_set = set(latest_ips)
    latest_lat = dict(zip(latest_ips, latest_lats))

    last_seen: dict[str, "object"] = {}
    for ts, ips, _lats in history:  # newest first => first occurrence is latest
        for ip in ips:
            if ip not in last_seen:
                last_seen[ip] = ts

    names = _resolve_hostnames(list(last_seen.keys()))

    def ip_key(ip: str):
        try:
            return tuple(int(o) for o in ip.split("."))
        except ValueError:
            return (0,)

    devices = []
    for ip, ts in last_seen.items():
        online = ip in latest_set
        devices.append({
            "ip": ip,
            "hostname": names.get(ip, ""),
            "latency_ms": round(latest_lat.get(ip, 0.0), 2) if online else None,
            "last_seen": ts.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            "online": online,
        })
    devices.sort(key=lambda d: (not d["online"], ip_key(d["ip"])))

    return {
        "scan_time": latest_ts.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "online_count": len(latest_ips),
        "devices": devices,
    }


_STYLE = """
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0b0d13; color: #e6e8ee; line-height: 1.5;
  }
  a { color: #6ea8fe; }
  header {
    display: flex; align-items: center; justify-content: space-between;
    flex-wrap: wrap; gap: 12px;
    padding: 18px 24px; border-bottom: 1px solid #1e2230; background: #10131d;
  }
  header h1 { margin: 0; font-size: 20px; letter-spacing: .5px; }
  header h1 span { color: #6ea8fe; }
  .meta { font-size: 13px; color: #8b93a7; }
  main { max-width: 1100px; margin: 0 auto; padding: 22px; }

  .isp {
    display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
    padding: 18px 22px; margin-bottom: 18px; border-radius: 12px;
    background: linear-gradient(120deg, #182a4d 0%, #141826 60%);
    border: 1px solid #24314f;
  }
  .isp .label { font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #93b4f0; }
  .isp .name { font-size: 24px; font-weight: 700; }

  .layout { display: grid; grid-template-columns: 260px 1fr; gap: 18px; align-items: start; }
  @media (max-width: 760px) { .layout { grid-template-columns: 1fr; } }

  .stats { display: flex; flex-direction: column; gap: 12px; }
  .card {
    background: #141826; border: 1px solid #232a3d; border-radius: 12px; padding: 15px 16px;
  }
  .card .label { font-size: 12px; text-transform: uppercase; letter-spacing: .5px; color: #8b93a7; }
  .card .value { font-size: 26px; font-weight: 600; margin-top: 2px; }
  .card .unit { font-size: 14px; color: #8b93a7; font-weight: 400; }
  .card.clickable { cursor: pointer; transition: border-color .15s, background .15s; }
  .card.clickable:hover { border-color: #3a5bbf; background: #171d2e; }
  .card.clickable .hint { font-size: 11px; color: #6ea8fe; margin-top: 4px; }

  .graph-card { background: #141826; border: 1px solid #232a3d; border-radius: 12px; padding: 12px; }
  img#graph { width: 100%; border-radius: 8px; background: #fff; display: block; }

  .toolbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: 20px 0 10px; }
  button, a.btn {
    background: #2451c9; color: #fff; border: none; border-radius: 8px;
    padding: 9px 16px; font-size: 14px; cursor: pointer; text-decoration: none; display: inline-block;
  }
  button:hover, a.btn:hover { background: #2f5fe0; }
  button:disabled { background: #313749; cursor: not-allowed; }
  a.btn.secondary { background: #232a3d; }
  .status-pill { font-size: 13px; color: #8b93a7; }
  .status-pill.busy { color: #f0c24b; }
  .status-pill.err { color: #f0685f; }

  section h2 { font-size: 15px; color: #b7bed0; margin: 26px 0 12px; font-weight: 600;
    display: flex; align-items: baseline; justify-content: space-between; }
  section h2 a { font-size: 13px; font-weight: 500; }
  .table-wrap { overflow-x: auto; border: 1px solid #232a3d; border-radius: 12px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  th, td { padding: 9px 12px; text-align: right; white-space: nowrap; border-bottom: 1px solid #1c2233; }
  th { background: #141826; color: #8b93a7; font-weight: 600; position: sticky; top: 0; }
  td:first-child, th:first-child { text-align: left; }
  tr:last-child td { border-bottom: none; }
  .empty { color: #8b93a7; padding: 20px; text-align: center; }
  footer { text-align: center; color: #5b6274; font-size: 12px; padding: 24px; }

  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; }
  .dot.on { background: #3ecf8e; } .dot.off { background: #5b6274; }

  .modal-back {
    position: fixed; inset: 0; background: rgba(4,6,12,.72); display: none;
    align-items: flex-start; justify-content: center; padding: 40px 16px; z-index: 50;
  }
  .modal-back.open { display: flex; }
  .modal {
    background: #10131d; border: 1px solid #24314f; border-radius: 14px;
    width: 100%; max-width: 720px; max-height: 80vh; overflow: auto;
  }
  .modal-head { display: flex; align-items: center; justify-content: space-between;
    padding: 16px 20px; border-bottom: 1px solid #1e2230; position: sticky; top: 0; background: #10131d; }
  .modal-head h3 { margin: 0; font-size: 16px; }
  .modal-head .close { background: none; color: #8b93a7; font-size: 22px; padding: 0 6px; }
  .modal .sub { padding: 6px 20px 0; color: #8b93a7; font-size: 12px; }
"""


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>netmon</title>
<style>__STYLE__</style>
</head>
<body>
<header>
  <h1>net<span>mon</span></h1>
  <div class="meta" id="meta">loading…</div>
</header>
<main>
  <div class="isp">
    <span class="label">ISP</span>
    <span class="name" id="isp">—</span>
  </div>

  <div class="layout">
    <div class="stats" id="stats"></div>
    <div class="graph-card">
      <img id="graph" alt="24-hour network graph" src="/graph.png">
    </div>
  </div>

  <div class="toolbar">
    <button id="runBtn" onclick="runNow()">Run speed test now</button>
    <a class="btn secondary" href="/export.csv">Download CSV</a>
    <span class="status-pill" id="statusPill"></span>
  </div>

  <section>
    <h2>Recent history <a href="/history">View all →</a></h2>
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

<div class="modal-back" id="devModal" onclick="if(event.target===this)closeDevices()">
  <div class="modal">
    <div class="modal-head">
      <h3>Discovered devices</h3>
      <button class="close" onclick="closeDevices()">&times;</button>
    </div>
    <div class="sub" id="devSub"></div>
    <div class="table-wrap" style="border:none">
      <table>
        <thead><tr><th></th><th>Name</th><th>IP</th><th>Latency (ms)</th><th>Last seen</th></tr></thead>
        <tbody id="devBody"><tr><td colspan="5" class="empty">Loading…</td></tr></tbody>
      </table>
    </div>
  </div>
</div>

<script>
function card(label, value, unit, opts) {
  opts = opts || {};
  const cls = opts.click ? 'card clickable' : 'card';
  const on = opts.click ? ` onclick="${opts.click}"` : '';
  const hint = opts.hint ? `<div class="hint">${opts.hint}</div>` : '';
  return `<div class="${cls}"${on}><div class="label">${label}</div>` +
         `<div class="value">${value}<span class="unit"> ${unit||''}</span></div>${hint}</div>`;
}

async function refresh() {
  try {
    const [status, latest, hist] = await Promise.all([
      fetch('/api/status').then(r=>r.json()),
      fetch('/api/latest').then(r=>r.json()),
      fetch('/api/history?limit=10').then(r=>r.json()),
    ]);

    document.getElementById('meta').textContent =
      `notifier: ${status.notifier} · next full report in ${status.next_report_in} cycle(s)`;

    document.getElementById('isp').textContent =
      (latest && latest.client) ? latest.client : 'No measurements yet';

    const stats = document.getElementById('stats');
    if (latest && latest.download_mbps !== undefined) {
      stats.innerHTML =
        card('Download', latest.download_mbps, 'Mbps') +
        card('Upload', latest.upload_mbps, 'Mbps') +
        card('Ping', latest.ping_ms, 'ms') +
        card('Devices', latest.devices, '', {click:'openDevices()', hint:'click for list'});
    } else {
      stats.innerHTML = card('Status', 'No data', '');
    }

    const tbody = document.getElementById('history');
    if (!hist.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty">No measurements yet</td></tr>';
    } else {
      tbody.innerHTML = hist.map(r =>
        `<tr><td>${r.timestamp}</td><td>${r.download_mbps}</td><td>${r.upload_mbps}</td>` +
        `<td>${r.ping_ms}</td><td>${r.devices}</td><td>${r.downloaded_mb}</td>` +
        `<td>${r.uploaded_mb}</td><td>${r.server}</td></tr>`).join('');
    }

    updateStatus(status);
    document.getElementById('graph').src = '/graph.png?t=' + Date.now();
  } catch (e) { console.error(e); }
}

function updateStatus(status) {
  const pill = document.getElementById('statusPill');
  const btn = document.getElementById('runBtn');
  if (status.running) {
    pill.textContent = 'Measurement in progress…'; pill.className = 'status-pill busy'; btn.disabled = true;
  } else if (status.last_error) {
    pill.textContent = 'Last run error: ' + status.last_error; pill.className = 'status-pill err'; btn.disabled = false;
  } else {
    pill.textContent = status.last_run ? ('Last run: ' + status.last_run) : 'Idle';
    pill.className = 'status-pill'; btn.disabled = false;
  }
}

async function runNow() {
  const btn = document.getElementById('runBtn'); btn.disabled = true;
  try { await fetch('/api/run', { method: 'POST' }); } catch (e) { console.error(e); }
  const fast = setInterval(async () => {
    const s = await (await fetch('/api/status')).json();
    updateStatus(s);
    if (!s.running) { clearInterval(fast); refresh(); }
  }, 2000);
}

async function openDevices() {
  document.getElementById('devModal').classList.add('open');
  document.getElementById('devBody').innerHTML = '<tr><td colspan="5" class="empty">Loading…</td></tr>';
  try {
    const d = await (await fetch('/api/devices')).json();
    document.getElementById('devSub').textContent =
      d.scan_time ? `${d.online_count} online · last scan ${d.scan_time}` : '';
    const body = document.getElementById('devBody');
    if (!d.devices.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty">No devices found</td></tr>';
    } else {
      body.innerHTML = d.devices.map(x =>
        `<tr><td><span class="dot ${x.online?'on':'off'}" title="${x.online?'online':'offline'}"></span></td>` +
        `<td>${x.hostname || '—'}</td><td>${x.ip}</td>` +
        `<td>${x.latency_ms == null ? '—' : x.latency_ms}</td><td>${x.last_seen}</td></tr>`).join('');
    }
  } catch (e) { console.error(e); }
}
function closeDevices() { document.getElementById('devModal').classList.remove('open'); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDevices(); });

refresh();
setInterval(refresh, 30000);
</script>
</body>
</html>""".replace("__STYLE__", _STYLE)


HISTORY_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>netmon · history</title>
<style>__STYLE__</style>
</head>
<body>
<header>
  <h1>net<span>mon</span> · history</h1>
  <div class="meta"><a href="/">← back to dashboard</a> · <a href="/export.csv">Download CSV</a></div>
</header>
<main>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Time</th><th>Down (Mbps)</th><th>Up (Mbps)</th><th>Ping (ms)</th>
          <th>Devices</th><th>Down (MB)</th><th>Up (MB)</th><th>ISP</th><th>Server</th>
        </tr>
      </thead>
      <tbody id="rows"><tr><td colspan="9" class="empty">Loading…</td></tr></tbody>
    </table>
  </div>
</main>
<footer>netmon · showing up to the last 500 measurements</footer>
<script>
(async () => {
  const rows = await (await fetch('/api/history?limit=500')).json();
  const body = document.getElementById('rows');
  if (!rows.length) { body.innerHTML = '<tr><td colspan="9" class="empty">No measurements yet</td></tr>'; return; }
  body.innerHTML = rows.map(r =>
    `<tr><td>${r.timestamp}</td><td>${r.download_mbps}</td><td>${r.upload_mbps}</td>` +
    `<td>${r.ping_ms}</td><td>${r.devices}</td><td>${r.downloaded_mb}</td>` +
    `<td>${r.uploaded_mb}</td><td>${r.client}</td><td>${r.server}</td></tr>`).join('');
})();
</script>
</body>
</html>""".replace("__STYLE__", _STYLE)


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

            if route in ("/", "/index.html"):
                self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")

            elif route == "/history":
                self._send(200, HISTORY_HTML.encode("utf-8"), "text/html; charset=utf-8")

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

            elif route == "/api/devices":
                self._json(_devices_payload(db))

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
