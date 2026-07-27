import html
import logging
import re

import requests

from notifier import ChatAction

log = logging.getLogger("netmon")

# ntfy notifications are plain text, so strip the Telegram-HTML tags the reports
# are written in but keep their inner text (e.g. "<b>148 Mbps</b>" -> "148 Mbps").
_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text)).strip()


class Bot:
    """Publishes to an ntfy topic. `url` is the full topic URL, e.g.
    https://ntfy.sh/my-netmon-topic or https://ntfy.example.com/netmon.

    Auth for protected topics is optional. Provide either a bearer access
    `token`, or a `username`/`password` pair (HTTP Basic auth). If both are
    given, the token wins."""

    def __init__(self, url: str, token: str, username: str, password: str, timeout: int):
        self.url = url
        self.token = token
        self.username = username
        self.password = password
        self.timeout = timeout

    @classmethod
    def init(cls, url: str, token: str, username: str, password: str, timeout: int) -> "Bot":
        if url.strip() == "":
            raise ValueError("ntfy URL cannot be empty")
        return cls(url.strip(), token.strip(), username.strip(), password, timeout)

    @property
    def _basic_auth(self) -> tuple[str, str] | None:
        # Only used when no bearer token is set.
        if not self.token and self.username:
            return (self.username, self.password)
        return None

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"Title": "netmon"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    def send_message(self, message: str, parse_mode: str = "HTML") -> str:
        body = _html_to_text(message).encode("utf-8")
        response = requests.post(
            self.url, data=body, headers=self._headers(),
            auth=self._basic_auth, timeout=self.timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Failed to send message: {response.text}")
        return response.text

    def send_photo(self, photo: bytes, caption: str = "", parse_mode: str = "HTML") -> str:
        # ntfy can't carry a multi-line caption and a binary attachment in one
        # request (HTTP headers can't hold newlines), so send the report text
        # first, then upload the graph as an attachment.
        if caption.strip():
            self.send_message(caption)  # the important part; raises if it fails

        # The graph attachment is best-effort. Many self-hosted ntfy servers
        # disable attachments (they're off unless attachment-cache-dir is set),
        # so a rejection here must NOT fail the whole report — the text is
        # already delivered. Log a hint and carry on.
        try:
            response = requests.put(
                self.url,
                data=photo,
                headers=self._headers({"Filename": "graph.png", "Message": "24-hour network graph"}),
                auth=self._basic_auth,
                timeout=self.timeout,
            )
            if response.status_code != 200:
                log.warning(
                    "ntfy did not accept the graph attachment (%s: %s). The report text "
                    "was still delivered; enable attachments on your ntfy server "
                    "(attachment-cache-dir + base-url) to include the graph image.",
                    response.status_code, response.text.strip(),
                )
        except Exception as e:
            log.warning(f"ntfy graph attachment upload failed ({e}); report text was still delivered.")
        return ""

    def send_chat_action(self, action: ChatAction = ChatAction.TYPING) -> str:
        # ntfy has no typing-indicator concept — no-op.
        return ""
