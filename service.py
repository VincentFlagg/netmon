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


def status_text_for(
    download_bps: float,
    ping: float,
    plan_download_mbps: int = 0,
    ping_good_ms: int = 20,
    ping_bad_ms: int = 40,
) -> str:
    dl_speed = download_bps / 10**6
    if plan_download_mbps and plan_download_mbps > 0:
        # Judge download as a fraction of the subscribed plan.
        pct = dl_speed / plan_download_mbps
        dl_good, dl_bad = pct >= 0.85, pct < 0.5
    else:
        # No plan set — fall back to absolute broadband thresholds.
        dl_good, dl_bad = dl_speed >= 150, dl_speed < 60

    if dl_good and ping <= ping_good_ms:
        return "Good speed and low latency"
    if dl_bad or ping > ping_bad_ms:
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
        r: runner.Runner,
        ai_api_key: str = "",
        ai_model: str = "",
        ai_base_url: str = "",
    ):
        self.db = db
        self.notifier = notifier
        self.runner = r
        # Runtime-editable settings (interval, templates, prompt, schedule, AI).
        self.settings = settings_mod.Settings(db)

        # AI connection from env vars; admin settings override these when set.
        # The client is built lazily and rebuilt when the effective config
        # changes, so AI can be configured/fixed from the admin page.
        self._env_ai = (ai_api_key, ai_model, ai_base_url)
        self._ai_client: ai.Client | None = None
        self._ai_cfg: tuple | None = None

        self.counter = 0
        # Serialises whole cycles so a manual run can't overlap a scheduled one.
        self._run_lock = threading.Lock()
        # matplotlib's pyplot state is process-global and not thread-safe, so
        # every plot() call is serialised through this.
        self._graph_lock = threading.Lock()

        self.state = RunState()

    # ------------------------------------------------------------------ #
    # AI client (effective config = admin settings override, else env)
    # ------------------------------------------------------------------ #
    def _effective_ai(self) -> tuple[str, str, str]:
        key = self.settings.ai_api_key or self._env_ai[0]
        model = self.settings.ai_model or self._env_ai[1]
        base = self.settings.ai_base_url or self._env_ai[2]
        return key, model, base

    @property
    def ai_configured(self) -> bool:
        return all(self._effective_ai())

    def test_ai(self) -> tuple[bool, str]:
        """Make a tiny live call to the configured AI endpoint. Returns
        (ok, error_message) — the real exception text on failure, so the admin
        page can show exactly what's wrong. Bounded to ~20s."""
        self.settings.reload()
        client = self._get_ai_client()
        if client is None:
            key, model, base = self._effective_ai()
            missing = [n for n, v in (("base URL", base), ("model", model), ("API key", key)) if not v]
            return False, "AI is not configured — missing: " + ", ".join(missing)

        result: dict = {}

        def _work():
            try:
                client.send_message("ping", "Reply with the single word: ok")
                result["ok"] = True
            except Exception as e:  # noqa: BLE001 - surface the real reason
                result["ok"] = False
                result["err"] = f"{type(e).__name__}: {e}"

        th = threading.Thread(target=_work, name="netmon-ai-test", daemon=True)
        th.start()
        th.join(20)
        if th.is_alive():
            return False, "Timed out after 20s contacting the AI endpoint — check the base URL and that the host is reachable from the container."
        if result.get("ok"):
            return True, ""
        return False, result.get("err", "unknown error")

    def _get_ai_client(self) -> ai.Client | None:
        key, model, base = self._effective_ai()
        if not (key and model and base):
            return None
        cfg = (key, model, base)
        if cfg != self._ai_cfg or self._ai_client is None:
            old = self._ai_client
            try:
                self._ai_client = ai.Client.init(key, model, base)
                self._ai_cfg = cfg
            except Exception as e:
                log.error(f"Failed to initialise AI client: {e}")
                self._ai_client, self._ai_cfg = None, None
                return None
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
        return self._ai_client

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
            status_text=status_text_for(
                metric.download, metric.ping,
                self.settings.plan_download_mbps,
                self.settings.ping_good_ms,
                self.settings.ping_bad_ms,
            ),
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

        # Tell the AI the subscribed plan so it judges speeds relative to it
        # (a 50 Mbps reading on a 50 Mbps plan is fine, not "slow").
        if self.settings.plan_download_mbps:
            plan = (
                f"The user's subscribed internet plan is about "
                f"{self.settings.plan_download_mbps} Mbps download"
            )
            if self.settings.plan_upload_mbps:
                plan += f" / {self.settings.plan_upload_mbps} Mbps upload"
            plan += (
                ". Judge the speeds RELATIVE TO THIS PLAN — hitting close to the "
                "plan is good, not slow. Note that this is a single-connection "
                "measurement, which normally reads a bit below the plan's rated speed.\n\n"
            )
            user_message = plan + user_message

        self.notifier.send_chat_action(ChatAction.TYPING)
        ai_client = self._get_ai_client()
        if ai_client is None:
            # AI not configured: send the graph with a plain header.
            report = (
                "<b>Network Speed Test Report (24h Analysis)</b>\n\n"
                "<i>AI commentary is disabled. Configure the AI connection on the "
                "admin page or via the AI_* env vars. Raw graph data is attached below.</i>"
            )
        else:
            try:
                report = ai_client.send_message(user_message, self.settings.report_system_prompt)
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
            self.settings.reload()  # pick up admin AI/prompt changes
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
