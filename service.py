import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import ai
import graphs
import models
import runner
import sqlite
import settings as settings_mod
from templates import REPORT_USER_TEMPLATE, MINI_REPORT_TEMPLATE
from notifier import ChatAction, Notifier

log = logging.getLogger("netmon")


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
    next_report_in: int = settings_mod.DEFAULTS["report_every"]


class Monitor:
    """Owns one measurement cycle so the scheduler loop and the web
    "run now" button share exactly the same code path."""

    def __init__(
        self,
        db: sqlite.DB,
        notifier: Notifier,
        netmon_ai: ai.Client | None,
        r: runner.Runner,
    ):
        self.db = db
        self.notifier = notifier
        # None when AI is not configured — reports are then sent graph-only.
        self.ai = netmon_ai
        self.runner = r
        # Runtime-editable settings (interval, templates, prompt, schedule).
        self.settings = settings_mod.Settings(db)

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
        ctx = dict(
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
        template = self.settings.mini_report_template
        try:
            msg = template.format(**ctx)
        except (KeyError, IndexError, ValueError) as e:
            # A bad custom template must not stop notifications — fall back.
            log.error(f"Custom status template is invalid ({e}); using the default.")
            msg = MINI_REPORT_TEMPLATE.format(**ctx)
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
        if self.ai is None:
            # AI intentionally not configured: send the graph with a plain header.
            report = (
                "<b>Network Speed Test Report (24h Analysis)</b>\n\n"
                "<i>AI commentary is disabled. Raw graph data is attached below.</i>"
            )
        else:
            try:
                report = self.ai.send_message(user_message, self.settings.report_system_prompt)
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

        # Remember the latest report so the dashboard can show it.
        self.db.set_last_report(report, datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"))

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
        every ``report_every`` cycles, otherwise a mini status update. Skipped
        entirely when the current time is outside the configured active window.

        This never raises: a failure in one cycle (speed test, DB, or notifier)
        is logged and the loop simply tries again at the next interval. Crashing
        here would, under a Docker ``restart`` policy, restart the container and
        immediately run another speed test — turning a misconfigured notifier
        into a speed-test-every-few-seconds storm."""
        # Pick up any admin-page changes made since the last cycle.
        self.settings.reload()
        report_every = self.settings.report_every

        if not self.settings.within_active_window(datetime.now().astimezone()):
            log.info("Outside the configured active window — skipping this cycle.")
            self.state.next_report_in = max(0, report_every - self.counter)
            return

        with self._run_lock:
            self.state.running = True
            try:
                metric, devices = self._measure_and_store()

                # Advance the report cadence up front so that a notifier failure
                # can't wedge us into retrying the detailed report every cycle.
                send_detailed = self.counter >= report_every
                self.counter = 0 if send_detailed else self.counter + 1

                if send_detailed:
                    self._send_detailed_report()
                else:
                    self._send_mini_report(metric, devices)
                self.state.last_error = None
            except Exception as e:
                # Measurement is already stored if we got that far, so the
                # dashboard still updates; just record the error and move on.
                log.error(f"Scheduled cycle failed (will retry next interval): {e}")
                self.state.last_error = str(e)
            finally:
                self.state.running = False
                self.state.next_report_in = max(0, report_every - self.counter)

    def run_report_now(self) -> bool:
        """Generate and send a detailed AI report immediately (the dashboard's
        "Run AI report now" button). Does not touch the scheduled cadence.
        Returns False if a cycle is already in progress."""
        if not self._run_lock.acquire(blocking=False):
            log.info("AI report requested but a cycle is already in progress.")
            return False
        try:
            self.state.running = True
            self._send_detailed_report()
            self.state.last_error = None
            return True
        except Exception as e:
            self.state.last_error = str(e)
            log.error(f"On-demand report failed: {e}")
            raise
        finally:
            self.state.running = False
            self._run_lock.release()

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
