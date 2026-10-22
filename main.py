import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import load_config, validate_runtime_config
from storage import SQLiteStorage
from navigation import NavigationMiddleware, ErrorMiddleware
from aiogram.fsm.storage.memory import SimpleEventIsolation
from delivery import scheduler as delivery_scheduler
import os
from db import Database
from handlers_start_help import setup_start_help_handlers
from handlers_profile import setup_profile_handlers
from handlers_requests import setup_requests_handlers
from handlers_offers_deals import setup_offers_deals_handlers
from handlers_sliders import setup_sliders_handlers
from handlers_reports import setup_reports_handlers
from reports import start_reports_task
from backups import start_backup_task
from local_backups import start_local_backup_task


async def main():
    logging.basicConfig(level=logging.INFO)

    cfg = load_config()
    validate_runtime_config(cfg)
    os.umask(0o077)
    db = Database(cfg.DB_PATH)

    bot = Bot(
        token=cfg.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(
        storage=SQLiteStorage(cfg.FSM_PATH), events_isolation=SimpleEventIsolation()
    )
    dp.message.outer_middleware(ErrorMiddleware())
    dp.message.outer_middleware(NavigationMiddleware())
    dp.callback_query.outer_middleware(ErrorMiddleware())
    dp.callback_query.outer_middleware(NavigationMiddleware())

    setup_start_help_handlers(dp, db, cfg)
    setup_profile_handlers(dp, db, cfg)
    setup_requests_handlers(dp, db, cfg)
    setup_offers_deals_handlers(dp, db, cfg)
    setup_sliders_handlers(dp, db, cfg)
    setup_reports_handlers(dp, db, cfg)

    reports_task = delivery_task = backup_task = local_backup_task = None
    try:
        reports_task = start_reports_task(bot, db, cfg)
        delivery_task = asyncio.create_task(delivery_scheduler(db, bot))
        backup_task = start_backup_task()
        local_backup_task = start_local_backup_task(cfg.DB_PATH)
        await dp.start_polling(bot)
    finally:
        for task in (backup_task, local_backup_task, reports_task, delivery_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        await dp.storage.close()
        db.conn.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
