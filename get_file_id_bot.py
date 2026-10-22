"""Run separately with a test token; show forwarded message and photo identifiers."""

import asyncio
import logging
from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from config import load_config

router = Router()


@router.message()
async def debug_ids(msg: Message):
    origin = msg.forward_origin
    if origin and origin.type == "channel":
        text = f"channel_id = <code>{origin.chat.id}</code>\nmessage_id = <code>{origin.message_id}</code>"
    else:
        text = f"chat_id = <code>{msg.chat.id}</code>\nmessage_id = <code>{msg.message_id}</code>"
    if msg.photo:
        from utils import escape_text

        text += f"\nfile_id = <code>{escape_text(msg.photo[-1].file_id)}</code>"
    await msg.answer(text)


async def main():
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    bot = Bot(
        token=cfg.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()
    dp.include_router(router)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
