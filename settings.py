"""Runtime-editable settings, persisted in the database and editable from the
admin page. Anything here can be changed without restarting the container."""

import logging

import templates

log = logging.getLogger("netmon")

# key -> default value. Only these keys are treated as settings; anything else
# in the settings table (e.g. cached last-report state) is ignored here.
DEFAULTS: dict = {
    "interval_seconds": 1800,          # seconds between scheduled cycles
    "report_every": 8,                 # detailed AI report every N cycles
    "active_hours_start": 0,           # 0-23; run only from this hour...
    "active_hours_end": 24,            # ...up to this hour (exclusive; 0-24)
    "active_days": [0, 1, 2, 3, 4, 5, 6],  # 0=Mon .. 6=Sun (datetime.weekday())
    "mini_report_template": templates.MINI_REPORT_TEMPLATE,
    "report_system_prompt": templates.REPORT_SYSTEM_PROMPT,
    # AI connection overrides (blank = fall back to the AI_* env vars).
    "ai_base_url": "",
    "ai_model": "",
    "ai_api_key": "",
    # Subscribed plan (Mbps). 0 = unset -> status uses absolute thresholds.
    # When set, the status line and AI judge speed relative to the plan.
    "plan_download_mbps": 0,
    "plan_upload_mbps": 0,
    # Ping thresholds for the status line (ms).
    "ping_good_ms": 20,
    "ping_bad_ms": 40,
}

_DUMMY_TEMPLATE_CTX = {
    "timestamp": "2026-01-01 00:00:00", "client": "ISP", "server": "Server",
    "device_count": 0, "download": 0.0, "upload": 0.0, "ping": 0.0,
    "download_mb": 0.0, "upload_mb": 0.0, "status_text": "ok",
}


def _hour_in_window(hour: int, start: int, end: int) -> bool:
    if start == end:
        return True  # all day
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # window wraps past midnight


def validate(updates: dict) -> tuple[dict, dict]:
    """Return (cleaned, errors). Only known keys are considered."""
    cleaned: dict = {}
    errors: dict = {}

    def as_int(key, lo, hi):
        try:
            v = int(updates[key])
        except (ValueError, TypeError):
            errors[key] = "must be a whole number"
            return
        if not (lo <= v <= hi):
            errors[key] = f"must be between {lo} and {hi}"
            return
        cleaned[key] = v

    if "interval_seconds" in updates:
        as_int("interval_seconds", 60, 86400)
    if "report_every" in updates:
        as_int("report_every", 1, 1000)
    if "plan_download_mbps" in updates:
        as_int("plan_download_mbps", 0, 100000)
    if "plan_upload_mbps" in updates:
        as_int("plan_upload_mbps", 0, 100000)
    if "ping_good_ms" in updates:
        as_int("ping_good_ms", 1, 100000)
    if "ping_bad_ms" in updates:
        as_int("ping_bad_ms", 1, 100000)
    if "ping_good_ms" in cleaned and "ping_bad_ms" in cleaned and cleaned["ping_bad_ms"] < cleaned["ping_good_ms"]:
        errors["ping_bad_ms"] = "'bad' ping must be greater than or equal to 'good' ping"
    if "active_hours_start" in updates:
        as_int("active_hours_start", 0, 24)
    if "active_hours_end" in updates:
        as_int("active_hours_end", 0, 24)

    if "active_days" in updates:
        days = updates["active_days"]
        try:
            days = [int(d) for d in days]
        except (ValueError, TypeError):
            errors["active_days"] = "must be a list of day numbers 0-6"
        else:
            if not days or any(d < 0 or d > 6 for d in days):
                errors["active_days"] = "pick at least one day (0=Mon .. 6=Sun)"
            else:
                cleaned["active_days"] = sorted(set(days))

    if "mini_report_template" in updates:
        tpl = str(updates["mini_report_template"])
        if not tpl.strip():
            errors["mini_report_template"] = "cannot be empty"
        else:
            try:
                tpl.format(**_DUMMY_TEMPLATE_CTX)
                cleaned["mini_report_template"] = tpl
            except (KeyError, IndexError, ValueError) as e:
                errors["mini_report_template"] = f"invalid placeholder: {e}"

    if "report_system_prompt" in updates:
        p = str(updates["report_system_prompt"])
        if not p.strip():
            errors["report_system_prompt"] = "cannot be empty"
        else:
            cleaned["report_system_prompt"] = p

    # AI connection overrides — all optional (blank falls back to env vars).
    for key in ("ai_base_url", "ai_model", "ai_api_key"):
        if key in updates:
            cleaned[key] = str(updates[key]).strip()

    return cleaned, errors


class Settings:
    """Live view of settings; reads defaults overlaid with DB overrides."""

    def __init__(self, db):
        self.db = db
        self._values: dict = dict(DEFAULTS)
        self.reload()

    def reload(self):
        stored = self.db.get_all_settings()
        values = dict(DEFAULTS)
        for key in DEFAULTS:
            if key in stored:
                values[key] = stored[key]
        self._values = values

    def as_dict(self) -> dict:
        return dict(self._values)

    def admin_dict(self) -> dict:
        """Like as_dict, but never exposes the stored API key — the admin page
        shows only whether one is set and can update it."""
        d = self.as_dict()
        d["ai_api_key_set"] = bool(d.get("ai_api_key", "").strip())
        d["ai_api_key"] = ""
        return d

    def save(self, updates: dict) -> dict:
        """Validate and persist. Returns {} on success or a dict of errors."""
        cleaned, errors = validate(updates)
        if errors:
            return errors
        if cleaned:
            self.db.set_settings(cleaned)
            self.reload()
        return {}

    # convenient typed accessors
    @property
    def interval_seconds(self) -> int:
        return int(self._values["interval_seconds"])

    @property
    def report_every(self) -> int:
        return int(self._values["report_every"])

    @property
    def mini_report_template(self) -> str:
        return self._values["mini_report_template"]

    @property
    def report_system_prompt(self) -> str:
        return self._values["report_system_prompt"]

    @property
    def ai_base_url(self) -> str:
        return self._values.get("ai_base_url", "")

    @property
    def ai_model(self) -> str:
        return self._values.get("ai_model", "")

    @property
    def ai_api_key(self) -> str:
        return self._values.get("ai_api_key", "")

    @property
    def plan_download_mbps(self) -> int:
        return int(self._values.get("plan_download_mbps", 0))

    @property
    def plan_upload_mbps(self) -> int:
        return int(self._values.get("plan_upload_mbps", 0))

    @property
    def ping_good_ms(self) -> int:
        return int(self._values.get("ping_good_ms", 20))

    @property
    def ping_bad_ms(self) -> int:
        return int(self._values.get("ping_bad_ms", 40))

    def within_active_window(self, now_local) -> bool:
        if now_local.weekday() not in self._values["active_days"]:
            return False
        return _hour_in_window(
            now_local.hour,
            int(self._values["active_hours_start"]),
            int(self._values["active_hours_end"]),
        )
