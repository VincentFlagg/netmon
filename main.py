import signal
import sys
import logging
import config as cfg
import sqlite
import ai
import tg
import discord_hook
import time
import runner
import webapp
from service import Monitor
from notifier import Notifier

SLEEP_TIME = 1800

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
    else:
        t = tg.Bot.init(conf.tg_bot_token, conf.tg_chat_id, conf.request_timeout)
    r = runner.Runner()

    with (
        sqlite.DB.init(conf.db_path) as database,
        ai.Client.init(conf.ai_api_key, conf.model, conf.base_url) as netmon_ai,
    ):
        monitor = Monitor(database, t, netmon_ai, r)

        if conf.web_enabled:
            webapp.start_web_server(
                database, monitor, conf.web_host, conf.web_port, conf.notifier
            )

        log.info("The bot has been started.")
        while True:
            monitor.run_scheduled_cycle()
            time.sleep(SLEEP_TIME)


if __name__ == "__main__":
    main()
