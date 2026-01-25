from dataclasses import dataclass
from typing import Optional


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


def load_config() -> Config:
    return Config(
        BOT_TOKEN="8465643872:AAHqZXr_7_HKOL0uckoDjiFxtW3f0uG--Vw",
        DB_PATH="bot.db",

        OFFER_URL="https://t.me/makintoshit",
        CHANNEL_URL="https://t.me/goosebump3s",

        SUPPORT_URL="https://t.me/makintoshit",
        REQUESTS_CHANNEL_URL="https://t.me/goosebump3s",
        ADS_URL="https://t.me/makintoshit",

        # ворк-чат с модерацией
        MODERATION_CHAT_ID=-1003236074223,
        MODERATION_TOPIC_ID=11,

        # канал с заявками
        REQUESTS_PUBLIC_CHANNEL_ID=-1003026579376,

        # канал с баннерами
        ASSETS_CHANNEL_ID=-1003292119994,
        PROFILE_BANNER_MESSAGE_ID=4,
        MY_REQUESTS_BANNER_MESSAGE_ID=2,
        REQUEST_SENT_BANNER_MESSAGE_ID=3,
        MY_OFFERS_BANNER_MESSAGE_ID=5,  # <- по твоей просьбе
        START_BANNER_ID=6,

        MANAGER_REQUISITES_TEXT=(
            "Реквизиты менеджера для оплаты залога:\n"
            "ФИО: Широков Владислав Дмитриевич\n"
            "Номер карты: 0000 0000 0000 0000\n"
            "Банк: Название банка"
        ),

        MANAGER_CDEK_CONTACT_TEXT=(
            "Контактные данные для отправки товара менеджеру через CDEK:\n"
            "1. Номер: 79998623067\n"
            "2. Адрес отделения: Москва, улица Черняховского, 5, корп. 2.\n"
            "3. ФИО: Широков Владислав Дмитриевич\n\n"
            "Внимание! Оплатите доставку до менеджера самостоятельно, "
            "в ином случае посылка не будет принята."
        ),

        SUPPORT_USERNAME="userpodderzhki",
    )
