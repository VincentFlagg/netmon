from enum import StrEnum
from typing import Protocol


class ChatAction(StrEnum):
    TYPING = "typing"
    UPLOAD_PHOTO = "upload_photo"


class Notifier(Protocol):
    def send_message(self, message: str, parse_mode: str = "HTML") -> str: ...

    def send_photo(self, photo: bytes, caption: str = "", parse_mode: str = "HTML") -> str: ...

    def send_chat_action(self, action: ChatAction = ChatAction.TYPING) -> str: ...


class NullNotifier:
    """No-op notifier for dashboard-only mode (NOTIFIER=none).

    Measurements are still taken and stored, so the web dashboard works, but
    nothing is pushed to Telegram or Discord."""

    def send_message(self, message: str, parse_mode: str = "HTML") -> str:
        return ""

    def send_photo(self, photo: bytes, caption: str = "", parse_mode: str = "HTML") -> str:
        return ""

    def send_chat_action(self, action: ChatAction = ChatAction.TYPING) -> str:
        return ""
