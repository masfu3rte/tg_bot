from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from config import Config


def safe_username(username: Optional[str], user_id: Optional[int] = None) -> str:
    if username:
        return f"@{username}" if not username.startswith("@") else username
    return f"id{user_id}" if user_id else "Пользователь"


def _internal_chat_id(chat_id: int) -> str:
    s = str(chat_id)
    if s.startswith("-100"):
        return s[4:]
    if s.startswith("-"):
        return s[1:]
    return s


def build_request_link(cfg: "Config", request: dict) -> Optional[str]:
    msg_id = request.get("channel_message_id")
    if not msg_id:
        return None
    internal = _internal_chat_id(cfg.REQUESTS_PUBLIC_CHANNEL_ID)
    return f"https://t.me/c/{internal}/{msg_id}"


def build_direct_link(cfg: "Config", message_id: int) -> str:
    internal = _internal_chat_id(cfg.REQUESTS_PUBLIC_CHANNEL_ID)
    return f"https://t.me/c/{internal}/{message_id}"
