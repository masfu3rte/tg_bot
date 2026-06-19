import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


@dataclass
class Config:
    BOT_TOKEN: str
    DB_PATH: str

    OFFER_URL: str
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
        raise RuntimeError(f"Переменная окружения {name} должна быть целым числом.") from exc


def _env_optional_int(name: str) -> Optional[int]:
    value = os.getenv(name)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"Переменная окружения {name} должна быть целым числом.") from exc


def load_config() -> Config:
    load_dotenv()

    return Config(
        BOT_TOKEN=_required_env("BOT_TOKEN"),
        DB_PATH=_env("DB_PATH", "bot.db"),

        OFFER_URL=_env("OFFER_URL", "https://t.me/your_offer_channel"),
        CHANNEL_URL=_env("CHANNEL_URL", "https://t.me/your_channel"),

        SUPPORT_URL=_env("SUPPORT_URL", "https://t.me/your_support"),
        REQUESTS_CHANNEL_URL=_env("REQUESTS_CHANNEL_URL", "https://t.me/your_requests_channel"),
        ADS_URL=_env("ADS_URL", "https://t.me/your_ads_channel"),

        # ворк-чат с модерацией
        MODERATION_CHAT_ID=_env_int("MODERATION_CHAT_ID"),
        MODERATION_TOPIC_ID=_env_optional_int("MODERATION_TOPIC_ID"),
        REPORTS_TOPIC_ID=_env_optional_int("REPORTS_TOPIC_ID"),

        # канал с заявками
        REQUESTS_PUBLIC_CHANNEL_ID=_env_int("REQUESTS_PUBLIC_CHANNEL_ID"),

        # канал с баннерами
        ASSETS_CHANNEL_ID=_env_optional_int("ASSETS_CHANNEL_ID"),
        PROFILE_BANNER_MESSAGE_ID=_env_optional_int("PROFILE_BANNER_MESSAGE_ID"),
        MY_REQUESTS_BANNER_MESSAGE_ID=_env_optional_int("MY_REQUESTS_BANNER_MESSAGE_ID"),
        REQUEST_SENT_BANNER_MESSAGE_ID=_env_optional_int("REQUEST_SENT_BANNER_MESSAGE_ID"),
        MY_OFFERS_BANNER_MESSAGE_ID=_env_optional_int("MY_OFFERS_BANNER_MESSAGE_ID"),
        START_BANNER_ID=_env_optional_int("START_BANNER_ID"),

        MANAGER_REQUISITES_TEXT=_env(
            "MANAGER_REQUISITES_TEXT",
            "Реквизиты менеджера для оплаты залога укажите в .env",
        ),

        MANAGER_CDEK_CONTACT_TEXT=_env(
            "MANAGER_CDEK_CONTACT_TEXT",
            "Контактные данные менеджера для CDEK укажите в .env",
        ),

        SUPPORT_USERNAME=_env("SUPPORT_USERNAME", "your_support_username"),
    )
