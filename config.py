import os
import argparse
from dotenv import load_dotenv
import logging

logger = logging.getLogger(__name__)

DEFAULT_REQUEST_TIMEOUT = 30

class Config:
    def __init__(
        self,
        ai_api_key: str,
        db_path: str,
        model: str,
        base_url: str,
        notifier: str,
        tg_bot_token: str = "",
        tg_chat_id: str = "",
        discord_webhook_url: str = "",
        request_timeout: int = DEFAULT_REQUEST_TIMEOUT,
        web_enabled: bool = True,
        web_host: str = "0.0.0.0",
        web_port: int = 8080,
        ai_enabled: bool = True,
    ):
        self.ai_api_key: str = ai_api_key
        self.db_path: str = db_path
        self.model: str = model
        self.base_url: str = base_url
        self.notifier: str = notifier
        self.tg_bot_token: str = tg_bot_token
        self.tg_chat_id: str = tg_chat_id
        self.discord_webhook_url: str = discord_webhook_url
        self.request_timeout: int = request_timeout
        self.web_enabled: bool = web_enabled
        self.web_host: str = web_host
        self.web_port: int = web_port
        self.ai_enabled: bool = ai_enabled

    @staticmethod
    def _parse_args():
        parser = argparse.ArgumentParser(description="App configuration")
        parser.add_argument(
            "--env",
            type=str,
            default=".env",
            help="Path to the .env file (default: .env)"
        )
        return parser.parse_args()

    @classmethod
    def init(cls):
        args = cls._parse_args()
        load_dotenv(args.env)

        ai_key = os.getenv("AI_API_KEY", "")
        db_path = os.getenv("DB_PATH", "")
        model = os.getenv("AI_MODEL", "")
        base_url = os.getenv("AI_BASE_URL", "")
        notifier = os.getenv("NOTIFIER", "telegram").strip().lower()

        if db_path.strip() == "":
            raise RuntimeError("DB_PATH not found or empty in environment")

        # AI is optional and all-or-nothing: provide all three AI_* values to
        # enable sarcastic 4-hour reports, or leave them all empty to run
        # without AI commentary (the dashboard and status updates still work).
        ai_fields = {"AI_API_KEY": ai_key, "AI_MODEL": model, "AI_BASE_URL": base_url}
        ai_set = [name for name, val in ai_fields.items() if val.strip() != ""]
        if ai_set and len(ai_set) != 3:
            missing = [name for name in ai_fields if name not in ai_set]
            raise RuntimeError(
                "AI is partially configured. Set all of AI_API_KEY, AI_MODEL and "
                f"AI_BASE_URL, or none of them. Missing: {', '.join(missing)}"
            )

        if notifier not in ("telegram", "discord", "none"):
            raise RuntimeError(f"NOTIFIER must be 'telegram', 'discord' or 'none', got: {notifier!r}")

        tg_bot_token = os.getenv("TG_BOT_TOKEN", "")
        tg_chat_id = os.getenv("TG_CHAT_ID", "")
        discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")

        request_timeout = int(os.getenv("REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT))
        if request_timeout <= 0:
            raise RuntimeError(f"REQUEST_TIMEOUT must be positive, got: {request_timeout}")

        web_enabled = os.getenv("WEB_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
        web_host = os.getenv("WEB_HOST", "0.0.0.0").strip() or "0.0.0.0"
        try:
            web_port = int(os.getenv("WEB_PORT", "8080"))
        except ValueError:
            raise RuntimeError("WEB_PORT must be an integer")
        if not (1 <= web_port <= 65535):
            raise RuntimeError(f"WEB_PORT must be between 1 and 65535, got: {web_port}")

        if notifier == "telegram":
            if tg_bot_token.strip() == "":
                raise RuntimeError("TG_BOT_TOKEN not found or empty in environment")
            if tg_chat_id.strip() == "":
                raise RuntimeError("TG_CHAT_ID not found or empty in environment")
        elif notifier == "discord":
            if discord_webhook_url.strip() == "":
                raise RuntimeError("DISCORD_WEBHOOK_URL not found or empty in environment")
        # notifier == "none": dashboard-only, no notifier credentials needed.

        if notifier == "none" and web_enabled is False:
            raise RuntimeError(
                "NOTIFIER=none with WEB_ENABLED=false leaves no way to see any "
                "output. Enable the web dashboard or pick a notifier."
            )

        return cls(
            ai_key, db_path, model, base_url, notifier,
            tg_bot_token, tg_chat_id, discord_webhook_url,
            request_timeout,
            web_enabled, web_host, web_port,
            ai_enabled=len(ai_set) == 3,
        )
