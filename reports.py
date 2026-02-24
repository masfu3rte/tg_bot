import asyncio
import os
import tempfile
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot
from aiogram.types import FSInputFile

from config import Config
from db import Database
from utils import write_simple_xlsx


def _format_period_label(start_dt: Optional[datetime], end_dt: Optional[datetime]) -> str:
    if not start_dt and not end_dt:
        return "За все время"
    if start_dt and end_dt:
        return f"{start_dt:%d.%m.%Y} - {end_dt:%d.%m.%Y}"
    if start_dt:
        return f"с {start_dt:%d.%m.%Y}"
    return f"до {end_dt:%d.%m.%Y}"


async def _send_report(
    bot: Bot,
    cfg: Config,
    db: Database,
    report_type: str,
    start_dt: Optional[datetime],
    end_dt: Optional[datetime],
    report_end_tag: Optional[datetime] = None,
    force: bool = False,
) -> None:
    period_start = start_dt.strftime("%Y-%m-%d") if start_dt else None
    end_tag = report_end_tag or end_dt
    period_end = end_tag.strftime("%Y-%m-%d") if end_tag else None

    if not force and await db.has_report_sent(report_type, period_start, period_end):
        return

    stats = await db.get_report_stats(start_dt, end_dt)
    active = await db.get_active_deals_summary()

    active_sum_rub = active["active_deals_sum_cents"] / 100.0
    period_label = _format_period_label(start_dt, end_dt)

    rows = [
        ("Период", period_label),
        ("Новых пользователей", stats["new_users"]),
        ("Новых сделок", stats["new_deals"]),
        ("Активных сделок", active["active_deals_count"]),
        ("Сумма активных сделок (руб.)", f"{active_sum_rub:.2f}"),
    ]

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        temp_path = tmp.name

    try:
        write_simple_xlsx(temp_path, "Отчет", [["Показатель", "Значение"], *rows])
        caption = f"Отчет: {report_type} ({period_label})"
        try:
            await bot.send_document(
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=cfg.REPORTS_TOPIC_ID,
                document=FSInputFile(temp_path),
                caption=caption,
            )
        except Exception:
            await bot.send_document(
                chat_id=cfg.MODERATION_CHAT_ID,
                document=FSInputFile(temp_path),
                caption=caption,
            )
        await db.mark_report_sent(report_type, period_start, period_end)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


async def send_reports_if_needed(bot: Bot, db: Database, cfg: Config) -> None:
    if not cfg.REPORTS_TOPIC_ID:
        return

    today = datetime.utcnow().date()
    period_end = datetime.combine(today, datetime.min.time())

    daily_start = period_end - timedelta(days=1)
    weekly_start = period_end - timedelta(days=7)
    monthly_start = period_end - timedelta(days=30)

    await _send_report(bot, cfg, db, "Дневной", daily_start, period_end)
    await _send_report(bot, cfg, db, "Недельный", weekly_start, period_end)
    await _send_report(bot, cfg, db, "Месячный", monthly_start, period_end)
    await _send_report(
        bot,
        cfg,
        db,
        "All time",
        None,
        None,
        report_end_tag=period_end,
    )


async def send_report_manual(bot: Bot, db: Database, cfg: Config, report_type: str) -> None:
    today = datetime.utcnow().date()
    period_end = datetime.combine(today, datetime.min.time())

    if report_type == "Дневной":
        start_dt = period_end - timedelta(days=1)
        end_dt = period_end
    elif report_type == "Недельный":
        start_dt = period_end - timedelta(days=7)
        end_dt = period_end
    elif report_type == "Месячный":
        start_dt = period_end - timedelta(days=30)
        end_dt = period_end
    elif report_type == "All time":
        start_dt = None
        end_dt = None
    else:
        return

    await _send_report(
        bot,
        cfg,
        db,
        report_type,
        start_dt,
        end_dt,
        report_end_tag=period_end,
        force=True,
    )


async def reports_scheduler(bot: Bot, db: Database, cfg: Config) -> None:
    while True:
        try:
            await send_reports_if_needed(bot, db, cfg)
        except Exception:
            pass

        now = datetime.utcnow()
        next_run = (now + timedelta(days=1)).replace(
            hour=0, minute=5, second=0, microsecond=0
        )
        sleep_seconds = max(60, int((next_run - now).total_seconds()))
        await asyncio.sleep(sleep_seconds)


def start_reports_task(bot: Bot, db: Database, cfg: Config) -> asyncio.Task:
    return asyncio.create_task(reports_scheduler(bot, db, cfg))
