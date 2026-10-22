from access import secure_messages
from access import secure_callbacks
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from config import Config
from db import Database
from reports import send_report_manual


def setup_reports_handlers(router: Router, db: Database, cfg: Config):
    message_start = len(router.message.handlers)
    callback_start = len(router.callback_query.handlers)

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

    @router.message(Command("delivery_status"))
    async def delivery_status(msg: Message):
        rows = db.conn.execute(
            "SELECT status,COUNT(*) FROM outbox GROUP BY status"
        ).fetchall()
        await msg.answer(
            "Доставка сообщений: " + ", ".join(f"{r[0]}: {r[1]}" for r in rows)
        )

    @router.message(Command("retry_delivery"))
    async def retry_delivery(msg: Message):
        from delivery import drain

        with db.conn:
            db.conn.execute(
                "UPDATE outbox SET status='pending',attempts=0,next_attempt=0 WHERE status IN ('blocked','pending')"
            )
        await drain(db, msg.bot)
        await msg.answer("Повтор выполнен. Результат: /delivery_status")

    @router.message(Command("resolve_cancel"))
    async def resolve_cancel(msg: Message):
        from delivery import content, queue_batch

        try:
            offer_id = int((msg.text or "").split()[1])
        except (ValueError, IndexError):
            await msg.answer(
                "После проверки расчётов используйте: /resolve_cancel ID_отклика"
            )
            return
        offer = await db.get_offer(offer_id)
        if (
            not offer
            or not offer["is_disputed"]
            or not await db.transition_offer(offer_id, "cancelled", ("dispute",))
        ):
            await msg.answer(
                "Этот отклик не ожидает решения по спору или уже завершён."
            )
            return
        req = await db.get_request(offer["request_id"])
        jobs = content(
            req["user_id"],
            f"Менеджер подтвердил отмену сделки №{req['id']} после проверки расчётов.",
        )
        jobs += content(
            offer["buyer_id"],
            f"Менеджер подтвердил отмену сделки №{req['id']} после проверки расчётов.",
        )
        delivered = await queue_batch(db, msg.bot, f"cancel-resolved-{offer_id}", jobs)
        await msg.answer(
            "Отмена сохранена."
            if delivered
            else "Отмена сохранена; уведомления ожидают доставки."
        )

    secure_callbacks(router, db, cfg, callback_start)

    secure_messages(router, db, cfg, message_start)
