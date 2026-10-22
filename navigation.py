import logging
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery
from aiogram.exceptions import TelegramAPIError
from keyboards import Keyboards

log = logging.getLogger(__name__)
NAVIGATION = {
    "🧾 Мои запросы",
    "👤 Мой профиль",
    "📮 Мои отклики",
    "🤝 Реферальная система",
    "Вернуться",
    "Создать новый запрос",
    "Активные запросы",
    "Активные отклики",
}
CALLBACK_NAVIGATION = {
    "requests:new",
    "requests:back",
    "requests:menu",
    "requests:active",
    "requests:archive",
    "offers:back",
    "offers:active",
    "offers:archive",
    "profile:cdek",
    "profile:req",
    "profile:back",
}


class NavigationMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        text = getattr(event, "text", None) or ""
        callback = getattr(event, "data", None)
        if (
            text in NAVIGATION
            or text.split(" ", 1)[0] in {"/start", "/cancel"}
            or callback in CALLBACK_NAVIGATION
        ):
            await data["state"].clear()
            data["raw_state"] = None
        if text.split(" ", 1)[0] == "/cancel":
            await event.answer("Ввод отменён.", reply_markup=Keyboards.bottom_menu())
            return
        return await handler(event, data)


class ErrorMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except TelegramAPIError as exc:
            log.error("Telegram action failed: %s", type(exc).__name__)
            # Keep the form and buttons available for another attempt.
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer(
                        "Не удалось завершить действие. Повторите позже.",
                        show_alert=True,
                    )
                else:
                    await event.answer(
                        "Не удалось завершить действие. Данные формы сохранены; повторите позже."
                    )
            except TelegramAPIError:
                log.warning("Could not notify user about delivery failure")
