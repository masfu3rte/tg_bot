import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import load_config
from db import Database
from handlers_start_help import setup_start_help_handlers
from handlers_profile import setup_profile_handlers
from handlers_requests import setup_requests_handlers
from handlers_offers_deals import setup_offers_deals_handlers
from handlers_sliders import setup_sliders_handlers


async def main():
    logging.basicConfig(level=logging.INFO)

    cfg = load_config()
    db = Database(cfg.DB_PATH)

    bot = Bot(
        token=cfg.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    setup_start_help_handlers(dp, db, cfg)
    setup_profile_handlers(dp, db, cfg)
    setup_requests_handlers(dp, db, cfg)
    setup_offers_deals_handlers(dp, db, cfg)
    setup_sliders_handlers(dp, db, cfg)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
