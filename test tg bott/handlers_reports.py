from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from config import Config
from db import Database
from reports import send_report_manual


def setup_reports_handlers(router: Router, db: Database, cfg: Config):
    def _is_moderation_chat(msg: Message) -> bool:
        return msg.chat.id == cfg.MODERATION_CHAT_ID

    @router.message(Command("report_daily"))
    async def report_daily(msg: Message):
        if not _is_moderation_chat(msg):
            return
        await send_report_manual(msg.bot, db, cfg, "Дневной")
        await msg.answer("Дневной отчет отправлен.")

    @router.message(Command("report_weekly"))
    async def report_weekly(msg: Message):
        if not _is_moderation_chat(msg):
            return
        await send_report_manual(msg.bot, db, cfg, "Недельный")
        await msg.answer("Недельный отчет отправлен.")

    @router.message(Command("report_monthly"))
    async def report_monthly(msg: Message):
        if not _is_moderation_chat(msg):
            return
        await send_report_manual(msg.bot, db, cfg, "Месячный")
        await msg.answer("Месячный отчет отправлен.")

    @router.message(Command("report_all"))
    async def report_all(msg: Message):
        if not _is_moderation_chat(msg):
            return
        await send_report_manual(msg.bot, db, cfg, "All time")
        await msg.answer("All time отчет отправлен.")

    @router.message(F.text == "Отчет за день")
    async def report_daily_text(msg: Message):
        if not _is_moderation_chat(msg):
            return
        await send_report_manual(msg.bot, db, cfg, "Дневной")
        await msg.answer("Дневной отчет отправлен.")

    @router.message(F.text == "Отчет за неделю")
    async def report_weekly_text(msg: Message):
        if not _is_moderation_chat(msg):
            return
        await send_report_manual(msg.bot, db, cfg, "Недельный")
        await msg.answer("Недельный отчет отправлен.")

    @router.message(F.text == "Отчет за месяц")
    async def report_monthly_text(msg: Message):
        if not _is_moderation_chat(msg):
            return
        await send_report_manual(msg.bot, db, cfg, "Месячный")
        await msg.answer("Месячный отчет отправлен.")

    @router.message(F.text == "Отчет за все время")
    async def report_all_text(msg: Message):
        if not _is_moderation_chat(msg):
            return
        await send_report_manual(msg.bot, db, cfg, "All time")
        await msg.answer("All time отчет отправлен.")
