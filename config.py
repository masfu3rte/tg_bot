import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from dotenv.parser import parse_stream
from pathlib import Path
from urllib.parse import urlparse

BUYER_GUIDE_URL = "https://t.me/someout/446"
SELLER_GUIDE_URL = "https://t.me/someout/445"
BACKUP_DRIVE_FOLDER_ID = "1vr5mU4Ph3dIUHB0vvlmwjqPpnoID0HtJ"


@dataclass(frozen=True)
class BackupConfig:
    enabled: bool
    db_path: str
    remote: str
    recipient: str
    interval_seconds: int
    rclone_binary: str = "rclone"
    folder_id: str = BACKUP_DRIVE_FOLDER_ID


@dataclass
class Config:
    BOT_TOKEN: str
    DB_PATH: str

    OFFER_URL: str
    PRIVACY_POLICY_URL: str
    CHANNEL_URL: str

    SUPPORT_URL: str
    REQUESTS_CHANNEL_URL: str
    ADS_URL: str

    MODERATION_CHAT_ID: int
    MODERATION_TOPIC_ID: Optional[int]
    REPORTS_TOPIC_ID: Optional[int]
    REQUESTS_PUBLIC_CHANNEL_ID: int

    ASSETS_CHANNEL_ID: Optional[int]
    PROFILE_BANNER_MESSAGE_ID: Optional[int]
    MY_REQUESTS_BANNER_MESSAGE_ID: Optional[int]
    REQUEST_SENT_BANNER_MESSAGE_ID: Optional[int]
    MY_OFFERS_BANNER_MESSAGE_ID: Optional[int]
    START_BANNER_ID: Optional[int]

    MANAGER_REQUISITES_TEXT: str
    MANAGER_CDEK_CONTACT_TEXT: str

    SUPPORT_USERNAME: str  # для «Оспорить»
    REFERRALS_VISIBLE: bool = False
    MODERATOR_IDS: tuple[int, ...] = ()
    FSM_PATH: str = "state.sqlite"


def load_environment():
    path = Path(os.getenv("ENV_FILE", str(Path(__file__).resolve().parent / ".env")))
    if path.exists():
        with path.open() as stream:
            errors = [
                str(item.original.line) for item in parse_stream(stream) if item.error
            ]
        if errors:
            raise RuntimeError(
                "Ошибки .env в строках "
                + ", ".join(errors)
                + ". Проверьте кавычки многострочных значений."
            )
        load_dotenv(path)


def validate_runtime_config(cfg):
    for key in ("MODERATION_CHAT_ID", "REQUESTS_PUBLIC_CHANNEL_ID"):
        if not getattr(cfg, key):
            raise RuntimeError(f"Заполните {key} перед запуском.")
    for key in (
        "OFFER_URL",
        "PRIVACY_POLICY_URL",
        "CHANNEL_URL",
        "SUPPORT_URL",
        "REQUESTS_CHANNEL_URL",
        "ADS_URL",
    ):
        value = getattr(cfg, key)
        if not value and key in ("ADS_URL", "REQUESTS_CHANNEL_URL"):
            continue
        if (
            urlparse(value).scheme != "https"
            or not urlparse(value).netloc
            or "your_" in value
        ):
            raise RuntimeError(f"В {key} нужна действительная HTTPS-ссылка.")
    for key in ("MANAGER_REQUISITES_TEXT", "MANAGER_CDEK_CONTACT_TEXT"):
        if (
            len(getattr(cfg, key).strip().splitlines()) < 3
            or len(getattr(cfg, key)) > 3000
        ):
            raise RuntimeError(
                f"{key}: ожидаются полные многострочные данные менеджера."
            )


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Не найдена обязательная переменная окружения {name}. "
            "Создайте .env на основе .env.example и заполните значение."
        )
    return value


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(
            f"Переменная окружения {name} должна быть целым числом."
        ) from exc


def _env_optional_int(name: str) -> Optional[int]:
    value = os.getenv(name)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(
            f"Переменная окружения {name} должна быть целым числом."
        ) from exc


def _manager_text(name: str, default: str) -> str:
    """Read manager details, accepting legacy escaped and mistyped newlines."""
    text = _env(name, default)
    text = text.replace(r"\r\n", "\n").replace(r"\n", "\n").replace("/n", "\n")
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if name == "MANAGER_REQUISITES_TEXT":
        text = text.replace("\n‼️", "\n\n‼️")
    return text


def load_config() -> Config:
    load_environment()

    return Config(
        BOT_TOKEN=_required_env("BOT_TOKEN"),
        DB_PATH=_env("DB_PATH", "bot.db"),
        OFFER_URL="https://docs.google.com/document/d/19f2BYa5_jKiOyk_7ZrsSLME_aJMMKlgDeLisjI2fEvk/edit?usp=sharing",
        PRIVACY_POLICY_URL="https://telegra.ph/POLITIKA-OBRABOTKI-PERSONALNYH-DANNYH-SOMEOUT-10-17",
        CHANNEL_URL="https://t.me/someout",
        SUPPORT_URL=_env("SUPPORT_URL")
        or "https://t.me/" + _env("SUPPORT_USERNAME", "someoutmanager").lstrip("@"),
        REQUESTS_CHANNEL_URL=_env("REQUESTS_CHANNEL_URL"),
        ADS_URL=_env("ADS_URL"),
        # ворк-чат с модерацией
        MODERATION_CHAT_ID=_env_int("MODERATION_CHAT_ID"),
        MODERATION_TOPIC_ID=_env_optional_int("MODERATION_TOPIC_ID"),
        REPORTS_TOPIC_ID=_env_optional_int("REPORTS_TOPIC_ID"),
        # канал с заявками
        REQUESTS_PUBLIC_CHANNEL_ID=_env_int("REQUESTS_PUBLIC_CHANNEL_ID"),
        # канал с баннерами
        ASSETS_CHANNEL_ID=_env_optional_int("ASSETS_CHANNEL_ID"),
        PROFILE_BANNER_MESSAGE_ID=_env_optional_int("PROFILE_BANNER_MESSAGE_ID"),
        MY_REQUESTS_BANNER_MESSAGE_ID=_env_optional_int(
            "MY_REQUESTS_BANNER_MESSAGE_ID"
        ),
        REQUEST_SENT_BANNER_MESSAGE_ID=_env_optional_int(
            "REQUEST_SENT_BANNER_MESSAGE_ID"
        ),
        MY_OFFERS_BANNER_MESSAGE_ID=_env_optional_int("MY_OFFERS_BANNER_MESSAGE_ID"),
        START_BANNER_ID=_env_optional_int("START_BANNER_ID"),
        MANAGER_REQUISITES_TEXT=_manager_text(
            "MANAGER_REQUISITES_TEXT",
            "Реквизиты менеджера для оплаты залога укажите в .env",
        ),
        MANAGER_CDEK_CONTACT_TEXT=_manager_text(
            "MANAGER_CDEK_CONTACT_TEXT",
            "Контактные данные менеджера для CDEK укажите в .env",
        ),
        SUPPORT_USERNAME=_env("SUPPORT_USERNAME", "someoutmanager").lstrip("@"),
        REFERRALS_VISIBLE=_env_int("REFERRALS_VISIBLE", 0) == 1,
        MODERATOR_IDS=tuple(
            int(value.strip())
            for value in _env("MODERATOR_IDS").split(",")
            if value.strip()
        ),
        FSM_PATH=_env("FSM_PATH", "state.sqlite"),
    )


def load_backup_config() -> BackupConfig:
    load_environment()
    enabled = _env_int("BACKUP_ENABLED", 0)
    if enabled not in (0, 1):
        raise RuntimeError("BACKUP_ENABLED должна быть 0 или 1.")
    return BackupConfig(
        enabled=bool(enabled),
        db_path=_env("DB_PATH", "bot.db"),
        remote=_env("BACKUP_RCLONE_REMOTE", "gdrive"),
        recipient=_env("BACKUP_AGE_RECIPIENT"),
        interval_seconds=_env_int("BACKUP_INTERVAL_SECONDS", 3600),
        rclone_binary=_env("BACKUP_RCLONE_BINARY", "rclone"),
    )
