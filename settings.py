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

    def within_active_window(self, now_local) -> bool:
        if now_local.weekday() not in self._values["active_days"]:
            return False
        return _hour_in_window(
            now_local.hour,
            int(self._values["active_hours_start"]),
            int(self._values["active_hours_end"]),
        )
