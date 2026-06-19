import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "Не найдена обязательная переменная окружения BOT_TOKEN. "
        "Создайте .env на основе .env.example и заполните значение."
    )

router = Router()



@router.message()
async def debug_ids(msg: Message):
    # Если сообщение переслано из канала/чата
    if msg.forward_from_chat:
        chat_id = msg.forward_from_chat.id
        message_id = msg.forward_from_message_id
        await msg.answer(
            f"channel_id = <code>{chat_id}</code>\n"
            f"message_id = <code>{message_id}</code>"
        )
    else:
        # Если это обычное сообщение (НЕ пересланное) — покажем просто текущий чат
        await msg.answer(
            f"chat_id = <code>{msg.chat.id}</code>\n"
            f"message_id = <code>{msg.message_id}</code>"
        )



async def main():
    logging.basicConfig(level=logging.INFO)

    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()
    dp.include_router(router)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
