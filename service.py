import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import ai
import graphs
import models
import runner
import sqlite
from notifier import ChatAction, Notifier

log = logging.getLogger("netmon")

# How many scheduled cycles between full AI reports. With SLEEP_TIME=1800s
# (30 min) a report every 8th cycle lands roughly every 4 hours.
REPORT_EVERY = 8

REPORT_SYSTEM_PROMPT = """
You are a sarcastic, cynical network analyst bot. Your job is to output a short network speed test and 24-hour trend report in Telegram HTML format.
You will receive a list of speed tests from the last 24 hours in chronological order (the last line is the latest test).

You must write the report in ENGLISH.
You must follow the EXACT structure below. Do not deviate from this layout, header naming, or formatting.

EXPECTED STRUCTURE:
<b>Network Speed Test Report (24h Analysis)</b>

Client: <b>[Client ISP]</b>
Server: <b>[Server Name]</b>

<b>Latest Test Metrics</b>
<pre>
Download: [Download Speed] Mbps
Upload: [Upload Speed] Mbps
Ping: [Ping Latency] ms
Devices Online: [Device Count]
</pre>

<b>24-Hour Dynamics Analysis</b>
[Analyze the dynamics, drops, and load of the network over the last 24 hours. Note any major drops in download/upload speeds or ping spikes.
Also look at how the device count changed over the same period. ONLY claim a link between device count and speed/latency swings if the numbers actually move together (e.g. speed visibly drops in the same window device count rises). If device count swings around while speed/ping stay flat, say plainly that device count does NOT explain it this period, and point at the ISP/line instead. Never invent a correlation that isn't supported by the numbers.
If ping reads exactly 0.00 ms while download speed is very low (a few Mbps or less), do NOT describe that as a good/perfect ping. That reading means the real ping was too high to register and got floored to zero — call it a red flag, not a strength.
Use a sarcastic, informal tone when describing speed drops, latency spikes, or a sudden herd of new devices, blaming heavy users/leeches on the network or the ISP (e.g. "a bunch of idiots clogging the bandwidth", "ISP dropping the ball", "mice chewing the optic fiber cables", or "yet another gadget joining the freeloader party") — but only when the data actually supports that story.
CRITICAL: Do NOT blame server changes for fluctuations. Assume the server choice is optimal and fluctuations reflect real network load, device count, or ISP issues.
Wrap key numbers in <code> tags, e.g., <code>148.31 Mbps</code>, <code>15.18 ms</code>, or <code>7 devices</code>.]

<b>Data Transfer (Latest Test)</b>
<pre>
Downloaded: [Downloaded MB] MB
Uploaded: [Uploaded MB] MB
</pre>

<b>Conclusion</b>
[A sarcastic, witty 1 short sentence summary of the network's overall quality and reliability over the past day.]


TEMPLATE EXAMPLE OF THE OUTPUT:
<b>Network Speed Test Report (24h Analysis)</b>

Client: <b>nameserver</b>
Server: <b>New York</b>

<b>Latest Test Metrics</b>
<pre>
Download: 140.3 Mbps
Upload: 62.8 Mbps
Ping: 15.2 ms
Devices Online: 7
</pre>

<b>24-Hour Dynamics Analysis</b>
Over the last 24 hours, the download speed averaged <code>140 Mbps</code>, but we saw a massive drop to <code>20 Mbps</code> at 8:00 PM right as device count jumped from <code>4</code> to <code>11 devices</code>. Clearly, a bunch of idiots decided to stream 4K movies all at once, or the ISP's mice were busy chewing on the fiber line again. Latency remained stable except for a brief spike to <code>95 ms</code> during the speed dip.

<b>Data Transfer (Latest Test)</b>
<pre>
Downloaded: 160.0 MB
Uploaded: 70.0 MB
</pre>

<b>Conclusion</b>
Expect periodic speed deaths whenever the local leechers wake up or the ISP fails to maintain their potato infrastructure.


CRITICAL RULES:
1. Do NOT use <br> or <br/> tags. For line breaks, use normal newlines.
2. The entire report must be in English.
3. Keep the "24-Hour Dynamics Analysis" to exactly 2-3 short sentences.
4. Do NOT write any description text below the "Data Transfer (Latest Test)" pre-block.
5. Keep the "Conclusion" to exactly 1 short sentence.
6. Highlight all numeric metric values in the text using <code>[Value]</code>.
7. Do NOT output any markdown blocks like ```html. Output raw HTML tags directly.
8. Make sure all HTML tags are closed correctly.
9. Be sarcastic, informal, and funny when describing performance dips or network load.
10. The entire output MUST be under 800 characters to ensure it easily fits within Telegram limits.
"""

REPORT_USER_TEMPLATE = """
Network speed test results:
- Date: {timestamp}
- Download: {download:.2f} Mbps
- Upload: {upload:.2f} Mbps
- Ping: {ping:.2f} ms
- Client: {client}
- Server: {server}
- Downloaded: {download_mb} MB
- Uploaded: {upload_mb} MB
- Share Link: {share}
- Devices online: {device_count}
"""

MINI_REPORT_TEMPLATE = """<b>Network Status Update</b>
Here is the latest snapshot of your internet speed:

Time: <b>{timestamp}</b>
ISP: <b>{client}</b> | Server: <b>{server}</b>

Devices online: <b>{device_count}</b>

Download: <b>{download:.1f} Mbps</b>
Upload: <b>{upload:.1f} Mbps</b>
Latency: <b>{ping:.1f} ms</b>

Traffic used: <b>{download_mb:.1f} MB</b> down / <b>{upload_mb:.1f} MB</b> up

<b>Current status:</b> {status_text}"""


def status_text_for(download_bps: float, ping: float) -> str:
    dl_speed = download_bps / 10**6
    if dl_speed >= 150 and ping <= 20:
        return "Good speed and low latency"
    if dl_speed < 60 or ping > 40:
        return (
            "A bunch of idiots decided to stream 4K movies all at once, or the "
            "ISP's mice were busy chewing on the fiber line again, whatever"
        )
    return "At least it works, I guess"


@dataclass
class RunState:
    """Lightweight, thread-safe view of the monitor for the web dashboard."""
    running: bool = False
    last_run: datetime | None = None
    last_error: str | None = None
    next_report_in: int = REPORT_EVERY


class Monitor:
    """Owns one measurement cycle so the scheduler loop and the web
    "run now" button share exactly the same code path."""

    def __init__(
        self,
        db: sqlite.DB,
        notifier: Notifier,
        netmon_ai: ai.Client,
        r: runner.Runner,
    ):
        self.db = db
        self.notifier = notifier
        self.ai = netmon_ai
        self.runner = r

        self.counter = 0
        # Serialises whole cycles so a manual run can't overlap a scheduled one.
        self._run_lock = threading.Lock()
        # matplotlib's pyplot state is process-global and not thread-safe, so
        # every plot() call is serialised through this.
        self._graph_lock = threading.Lock()

        self.state = RunState()

    # ------------------------------------------------------------------ #
    # Measurement
    # ------------------------------------------------------------------ #
    def _measure_and_store(self) -> tuple[models.NetworkMetric, list[models.NetworkDevice]]:
        self.notifier.send_chat_action(ChatAction.TYPING)

        metric = self.runner.run_speedtest()
        all_devices = self.runner.run_devices_scan()

        with self.db.transaction():
            self.db.add_metric(metric)
            device_scan_id = self.db.add_devices(all_devices)
            speedtest = models.SpeedTest.create(metric.id, device_scan_id)
            self.db.add_speedtest(speedtest)

        log.info(f"Speedtest has been added: {speedtest}")
        self.state.last_run = datetime.now(timezone.utc)
        return metric, all_devices

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    def _send_mini_report(self, metric: models.NetworkMetric, devices: list[models.NetworkDevice]):
        msg = MINI_REPORT_TEMPLATE.format(
            timestamp=metric.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
            download=metric.download / 10**6,
            upload=metric.upload / 10**6,
            ping=metric.ping,
            device_count=len(devices),
            client=metric.client,
            server=metric.server,
            download_mb=metric.bytes_received / 10**6,
            upload_mb=metric.bytes_sent / 10**6,
            status_text=status_text_for(metric.download, metric.ping),
        )
        self.notifier.send_message(msg)
        log.info("Mini report has been sent.")

    def _build_report_user_message(self, metrics, device_counts) -> str:
        user_message = ""
        for m, device_count in zip(metrics, device_counts):
            user_message += REPORT_USER_TEMPLATE.format(
                timestamp=m.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                download=round(m.download / 10**6, 1),
                upload=round(m.upload / 10**6, 1),
                ping=m.ping,
                client=m.client,
                server=m.server,
                download_mb=round(m.bytes_received / 10**6, 1),
                upload_mb=round(m.bytes_sent / 10**6, 1),
                share=m.share,
                device_count=device_count,
            ) + "\n"
        return user_message

    def render_graph_png(self) -> bytes | None:
        """Render the current 24h graph to PNG bytes. Serialised because
        matplotlib is not thread-safe. Shared by the report and the web /graph.png."""
        metrics, device_counts = self.db.get_metrics_with_device_counts()
        if not metrics:
            return None
        with self._graph_lock:
            fname = graphs.NetmonGraph(metrics, device_counts).plot()
            with open(fname, "rb") as f:
                return f.read()

    def _send_detailed_report(self):
        metrics, device_counts = self.db.get_metrics_with_device_counts()
        user_message = self._build_report_user_message(metrics, device_counts)

        self.notifier.send_chat_action(ChatAction.TYPING)
        try:
            report = self.ai.send_message(user_message, REPORT_SYSTEM_PROMPT)
            report = report.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
        except Exception as e:
            # AI backend down/unreachable/misconfigured: don't lose the whole
            # report, just send the graph with a plain notice instead of a
            # sarcastic AI-written one.
            log.error(f"AI report generation failed, sending graph without commentary: {e}")
            report = (
                "<b>Network Speed Test Report (24h Analysis)</b>\n\n"
                "<i>AI commentary unavailable this cycle — the AI backend "
                "could not be reached. Raw graph data is attached below.</i>"
            )

        self.notifier.send_chat_action(ChatAction.UPLOAD_PHOTO)
        with self._graph_lock:
            fname = graphs.NetmonGraph(metrics, device_counts).plot()
            with open(fname, "rb") as f:
                photo = f.read()
        self.notifier.send_photo(photo, report)
        log.info("Detailed report has been sent.")

    # ------------------------------------------------------------------ #
    # Public cycle entry points
    # ------------------------------------------------------------------ #
    def run_scheduled_cycle(self):
        """One tick of the background scheduler. Sends a detailed AI report
        every REPORT_EVERY cycles, otherwise a mini status update."""
        with self._run_lock:
            self.state.running = True
            try:
                metric, devices = self._measure_and_store()
                if self.counter >= REPORT_EVERY:
                    self._send_detailed_report()
                    self.counter = 0
                else:
                    self._send_mini_report(metric, devices)
                self.counter += 1
                self.state.last_error = None
            except Exception as e:
                self.state.last_error = str(e)
                raise
            finally:
                self.state.running = False
                self.state.next_report_in = max(0, REPORT_EVERY - self.counter)

    def run_manual_cycle(self) -> bool:
        """Triggered from the web dashboard. Runs a measurement + mini report
        immediately without disturbing the scheduled detailed-report cadence.
        Returns False if a cycle is already in progress."""
        if not self._run_lock.acquire(blocking=False):
            log.info("Manual run requested but a cycle is already in progress.")
            return False
        try:
            self.state.running = True
            metric, devices = self._measure_and_store()
            self._send_mini_report(metric, devices)
            self.state.last_error = None
            return True
        except Exception as e:
            self.state.last_error = str(e)
            log.error(f"Manual run failed: {e}")
            raise
        finally:
            self.state.running = False
            self._run_lock.release()
