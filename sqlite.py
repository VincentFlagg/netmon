import json
import models
import threading
from contextlib import closing, contextmanager
from contextvars import ContextVar
import sqlite3
import uuid
from datetime import datetime
from uuid_extensions import uuid7str

_tx_depth: ContextVar[int] = ContextVar("tx_depth", default=0)


class DB:
    def __init__(self, conn: sqlite3.Connection):
        self.conn: sqlite3.Connection = conn
        # The scheduler loop and the web server run in separate threads and
        # share this single connection, so every access is serialised through
        # a reentrant lock. RLock (not Lock) because the write path nests
        # transaction() calls inside one another on the same thread.
        self._lock = threading.RLock()

    def __enter__(self) -> "DB":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @classmethod
    def init(cls, path: str) -> "DB":
        if not path.strip():
            raise ValueError("Database path cannot be empty")

        try:
            # check_same_thread=False: the connection is shared between the
            # scheduler and web-server threads, but all access is serialised by
            # self._lock, so this is safe. WAL mode lets the dashboard read
            # while a write is in flight instead of blocking on a locked file.
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")

            db = cls(conn)
            with db.transaction():
                db._create_schema()
                db._migrate()
            return db
        except sqlite3.Error as e:
            raise RuntimeError(f"Failed to open database connection: {e}")

    def _create_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id             TEXT PRIMARY KEY,
                download       REAL NOT NULL,
                upload         REAL NOT NULL,
                ping           REAL NOT NULL,
                share          TEXT,
                client         TEXT NOT NULL,
                server         TEXT NOT NULL,
                bytes_sent     INTEGER NOT NULL,
                bytes_received INTEGER NOT NULL,
                timestamp      DATETIME NOT NULL DEFAULT (datetime('now'))
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS device_scans (
                id             TEXT PRIMARY KEY,
                ips            TEXT NOT NULL,
                latencies      TEXT NOT NULL,
                macs           TEXT NOT NULL DEFAULT '[]',
                vendors        TEXT NOT NULL DEFAULT '[]',
                hostnames      TEXT NOT NULL DEFAULT '[]'
            );
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS speedtest (
                id              TEXT PRIMARY KEY,
                device_scans_id TEXT UNIQUE REFERENCES device_scans(id) ON DELETE CASCADE,
                metrics_id      TEXT UNIQUE REFERENCES metrics(id) ON DELETE CASCADE
            );
        """)

        # Runtime settings (admin page) and small bits of cached state, stored
        # as JSON strings keyed by name.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

    def _migrate(self):
        # Add device_scans columns to databases created before MAC/vendor
        # capture. Existing rows get '[]' so reads stay consistent.
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(device_scans);")}
        for col in ("macs", "vendors", "hostnames"):
            if col not in cols:
                self.conn.execute(
                    f"ALTER TABLE device_scans ADD COLUMN {col} TEXT NOT NULL DEFAULT '[]';"
                )

    @contextmanager
    def transaction(self):
        depth = _tx_depth.get()
        _tx_depth.set(depth + 1)

        try:
            if depth == 0:
                with self._lock, self.conn:
                    yield
            else:
                yield
        except sqlite3.Error as e:
            raise RuntimeError(f"Database transaction failed: {e}")
        finally:
            _tx_depth.set(depth)

    def add_metric(self, metric: models.NetworkMetric):
        with self.transaction():
            self.conn.execute("""
                INSERT INTO metrics (
                    id, download, upload, ping, timestamp, share, client, server,
                    bytes_sent, bytes_received
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(metric.id), metric.download, metric.upload,
                metric.ping, metric.timestamp, metric.share,
                metric.client, metric.server, metric.bytes_sent,
                metric.bytes_received
            ))

    def add_devices(self, devices: list[models.NetworkDevice]) -> uuid.UUID:
        scan_id   = uuid.UUID(uuid7str())
        ips       = json.dumps([d.ip         for d in devices])
        latencies = json.dumps([d.latency_ms for d in devices])
        macs      = json.dumps([d.mac        for d in devices])
        vendors   = json.dumps([d.vendor     for d in devices])
        hostnames = json.dumps([d.hostname   for d in devices])

        with self.transaction():
            self.conn.execute("""
                INSERT INTO device_scans (id, ips, latencies, macs, vendors, hostnames)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (str(scan_id), ips, latencies, macs, vendors, hostnames))
        return scan_id

    def add_speedtest(self, speedtest: models.SpeedTest):
        with self.transaction():
            self.conn.execute("""
                INSERT INTO speedtest (id, metrics_id, device_scans_id)
                VALUES (?, ?, ?)
            """, (str(speedtest.id), str(speedtest.metric_id), str(speedtest.device_scan_id)))

    def _fetchall(self, sql: str, params: tuple = ()) -> list:
        # Every read goes through here so it holds self._lock while touching the
        # shared connection — keeps the web thread's queries from racing the
        # scheduler thread's writes.
        with self._lock:
            try:
                with closing(self.conn.cursor()) as cursor:
                    cursor.execute(sql, params)
                    return cursor.fetchall()
            except sqlite3.Error as e:
                raise RuntimeError(f"Query failed: {e}")

    @staticmethod
    def _row_to_metric(row) -> models.NetworkMetric:
        return models.NetworkMetric(
            id=uuid.UUID(row[0]),
            download=row[1],
            upload=row[2],
            ping=row[3],
            timestamp=datetime.fromisoformat(row[4]),
            share=row[5],
            client=row[6],
            server=row[7],
            bytes_sent=row[8],
            bytes_received=row[9]
        )

    def get_metrics(self) -> list[models.NetworkMetric]:
        rows = self._fetchall("""
            SELECT * FROM (
                SELECT id, download, upload, ping, timestamp, share, client, server, bytes_sent, bytes_received
                FROM metrics
                WHERE timestamp > DATETIME('now', '-24 hours')
                ORDER BY timestamp DESC
                LIMIT 24
            ) ORDER BY timestamp ASC;
        """)
        return [self._row_to_metric(row) for row in rows]

    def get_metrics_with_device_counts(self) -> tuple[list[models.NetworkMetric], list[int]]:
        rows = self._fetchall("""
            SELECT * FROM (
                SELECT m.id, m.download, m.upload, m.ping, m.timestamp, m.share, m.client, m.server,
                       m.bytes_sent, m.bytes_received, ds.ips
                FROM metrics m
                JOIN speedtest st ON st.metrics_id = m.id
                JOIN device_scans ds ON ds.id = st.device_scans_id
                WHERE m.timestamp > DATETIME('now', '-24 hours')
                ORDER BY m.timestamp DESC
                LIMIT 24
            ) ORDER BY timestamp ASC;
        """)

        metrics: list[models.NetworkMetric] = []
        device_counts: list[int] = []
        for row in rows:
            metrics.append(self._row_to_metric(row))
            device_counts.append(len(json.loads(row[10])))

        return metrics, device_counts

    def get_latest_with_device_count(self) -> tuple[models.NetworkMetric, int] | None:
        # Most recent single reading, used by the web dashboard's status panel.
        rows = self._fetchall("""
            SELECT m.id, m.download, m.upload, m.ping, m.timestamp, m.share, m.client, m.server,
                   m.bytes_sent, m.bytes_received, ds.ips
            FROM metrics m
            JOIN speedtest st ON st.metrics_id = m.id
            JOIN device_scans ds ON ds.id = st.device_scans_id
            ORDER BY m.timestamp DESC
            LIMIT 1;
        """)
        if not rows:
            return None
        row = rows[0]
        return self._row_to_metric(row), len(json.loads(row[10]))

    def get_recent_with_device_counts(self, limit: int = 50) -> tuple[list[models.NetworkMetric], list[int]]:
        # Newest-first history for the dashboard table. Bounded so the web page
        # can't ask for an unbounded result set.
        limit = max(1, min(int(limit), 500))
        rows = self._fetchall("""
            SELECT m.id, m.download, m.upload, m.ping, m.timestamp, m.share, m.client, m.server,
                   m.bytes_sent, m.bytes_received, ds.ips
            FROM metrics m
            JOIN speedtest st ON st.metrics_id = m.id
            JOIN device_scans ds ON ds.id = st.device_scans_id
            ORDER BY m.timestamp DESC
            LIMIT ?;
        """, (limit,))

        metrics: list[models.NetworkMetric] = []
        device_counts: list[int] = []
        for row in rows:
            metrics.append(self._row_to_metric(row))
            device_counts.append(len(json.loads(row[10])))

        return metrics, device_counts

    def get_device_history(self, hours: int = 24) -> list[dict]:
        # Every device scan in the window, newest first, so the dashboard can
        # build a per-device "last seen" view. device_scans has no timestamp of
        # its own, so the scan time comes from the joined metric.
        hours = max(1, int(hours))
        rows = self._fetchall("""
            SELECT m.timestamp, ds.ips, ds.latencies, ds.macs, ds.vendors, ds.hostnames
            FROM device_scans ds
            JOIN speedtest st ON st.device_scans_id = ds.id
            JOIN metrics m ON m.id = st.metrics_id
            WHERE m.timestamp > DATETIME('now', ?)
            ORDER BY m.timestamp DESC;
        """, (f"-{hours} hours",))

        def _load(value):
            try:
                return json.loads(value) if value else []
            except (ValueError, TypeError):
                return []

        out: list[dict] = []
        for ts, ips, latencies, macs, vendors, hostnames in rows:
            out.append({
                "timestamp": datetime.fromisoformat(ts),
                "ips": _load(ips),
                "latencies": _load(latencies),
                "macs": _load(macs),
                "vendors": _load(vendors),
                "hostnames": _load(hostnames),
            })
        return out

    # ------------------------------------------------------------------ #
    # Settings & cached state (key/value JSON)
    # ------------------------------------------------------------------ #
    def get_all_settings(self) -> dict:
        rows = self._fetchall("SELECT key, value FROM settings;")
        out = {}
        for key, value in rows:
            try:
                out[key] = json.loads(value)
            except (ValueError, TypeError):
                pass
        return out

    def set_settings(self, items: dict):
        with self.transaction():
            for key, value in items.items():
                self.conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value;",
                    (key, json.dumps(value)),
                )

    def set_last_report(self, html: str, timestamp_iso: str):
        self.set_settings({"_last_report_html": html, "_last_report_time": timestamp_iso})

    def get_last_report(self) -> tuple[str, str] | None:
        rows = self._fetchall(
            "SELECT key, value FROM settings WHERE key IN ('_last_report_html', '_last_report_time');"
        )
        data = {k: json.loads(v) for k, v in rows}
        if "_last_report_html" not in data:
            return None
        return data["_last_report_html"], data.get("_last_report_time", "")

    def close(self):
        with self._lock:
            self.conn.close()