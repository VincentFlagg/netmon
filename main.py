import signal
import sys
import logging
import config as cfg
import sqlite
import ai
import tg
import discord_hook
import ntfy_hook
import time
import runner
import webapp
from contextlib import ExitStack
from service import Monitor
from notifier import Notifier, NullNotifier

log = logging.getLogger("netmon")


def sigterm_handler(signum, frame):
    log.info(f"Received termination signal: {signum}. Exiting gracefully.")
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, sigterm_handler)
    signal.signal(signal.SIGINT, sigterm_handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    conf = cfg.Config.init()
    t: Notifier
    if conf.notifier == "discord":
        t = discord_hook.Bot.init(conf.discord_webhook_url, conf.request_timeout)
    elif conf.notifier == "ntfy":
        t = ntfy_hook.Bot.init(
            conf.ntfy_url, conf.ntfy_token,
            conf.ntfy_user, conf.ntfy_password, conf.request_timeout,
        )
    elif conf.notifier == "telegram":
        t = tg.Bot.init(conf.tg_bot_token, conf.tg_chat_id, conf.request_timeout)
    else:  # "none" — dashboard-only, nothing is pushed out
        t = NullNotifier()
    r = runner.Runner()

    with ExitStack() as stack:
        database = stack.enter_context(sqlite.DB.init(conf.db_path))

        netmon_ai = None
        if conf.ai_enabled:
            netmon_ai = stack.enter_context(
                ai.Client.init(conf.ai_api_key, conf.model, conf.base_url)
            )
        else:
            log.info("AI is not configured — reports will be sent without commentary.")

        monitor = Monitor(database, t, netmon_ai, r)

        if conf.web_enabled:
            webapp.start_web_server(
                database, monitor, conf.web_host, conf.web_port, conf.notifier,
                admin_user=conf.admin_user, admin_password=conf.admin_password,
                ai_enabled=conf.ai_enabled,
            )

        log.info("The bot has been started.")
        while True:
            monitor.run_scheduled_cycle()
            # Interval is admin-editable, so read it fresh each loop.
            time.sleep(monitor.settings.interval_seconds)


if __name__ == "__main__":
    main()
