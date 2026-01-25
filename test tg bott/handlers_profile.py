from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from config import Config
from db import Database
from keyboards import Keyboards
from states import ProfileEdit
from utils import safe_username


def setup_profile_handlers(router: Router, db: Database, cfg: Config):
    async def send_profile(msg: Message):
        user_id = msg.from_user.id
        profile = await db.get_profile_view(user_id)
        username = safe_username(profile["username"], profile["user_id"])

        text_lines = [
            "Ваш профиль:",
            f"{username}",
            f"Дата регистрации: {profile['created_date']}",
            "",
            f"Создано запросов: {profile['requests_count']}",
            f"Сделано откликов: {profile['responses_count']}",
            f"Оборот по сделкам: {profile['deals_sum']}",
            "",
            "Контактные данные CDEK:",
            f"ФИО: {profile['cdek'].get('fio') or '—'}",
            f"Телефон: {profile['cdek'].get('phone') or '—'}",
            f"Адрес ПВЗ: {profile['cdek'].get('pvz') or '—'}",
            "",
            "Реквизиты:",
            f"ФИО: {profile['req'].get('fio') or '—'}",
            f"Карта: {profile['req'].get('card') or '—'}",
            f"Банк: {profile['req'].get('bank') or '—'}",
        ]
        text = "\n".join(text_lines)

        # Одно сообщение: копируем баннер и в caption кладём текст + прикручиваем инлайн-кнопки
        if cfg.ASSETS_CHANNEL_ID and cfg.PROFILE_BANNER_MESSAGE_ID:
            try:
                await msg.bot.copy_message(
                    chat_id=msg.chat.id,
                    from_chat_id=cfg.ASSETS_CHANNEL_ID,
                    message_id=cfg.PROFILE_BANNER_MESSAGE_ID,
                    caption=text,
                    reply_markup=Keyboards.profile_menu_inline(),
                )
                return
            except Exception:
                pass

        # fallback: просто текст + кнопки
        await msg.answer(text, reply_markup=Keyboards.profile_menu_inline())

    @router.message(F.text == "👤 Мой профиль")
    async def my_profile(msg: Message):
        await send_profile(msg)

    @router.message(F.text == "🤝 Рефералы")
    async def my_referrals(msg: Message):
        user_id = msg.from_user.id
        code = await db.ensure_referral_code(user_id)
        count = await db.get_referral_count(user_id)

        bot = await msg.bot.get_me()
        bot_username = bot.username or ""
        link = f"https://t.me/{bot_username}?start=ref_{code}" if bot_username else code

        text_lines = [
            "Ваша реферальная статистика:",
            f"Приглашено пользователей: {count}",
            "",
            "Ваша реферальная ссылка:",
            link,
        ]
        await msg.answer("\n".join(text_lines))

    @router.callback_query(F.data == "profile:cdek")
    async def edit_cdek(cq: CallbackQuery, state: FSMContext):
        await state.set_state(ProfileEdit.waiting_for_cdek_form)
        await cq.message.answer(
            "Отправьте контактные данные CDEK тремя строками в формате:\n"
            "<b>ФИО\nтелефон\nадрес ПВЗ</b>"
        )
        await cq.answer()

    @router.callback_query(F.data == "profile:req")
    async def edit_req(cq: CallbackQuery, state: FSMContext):
        await state.set_state(ProfileEdit.waiting_for_req_form)
        await cq.message.answer(
            "Отправьте реквизиты тремя строками в формате:\n"
            "<b>ФИО\nномер карты\nбанк</b>"
        )
        await cq.answer()

    @router.message(ProfileEdit.waiting_for_cdek_form)
    async def save_cdek(msg: Message, state: FSMContext):
        parts = [p.strip() for p in (msg.text or "").splitlines() if p.strip()]
        if len(parts) != 3:
            await msg.answer("Нужно три строки: ФИО, телефон, адрес ПВЗ.")
            return
        fio, phone, pvz = parts
        await db.set_cdek_contacts(msg.from_user.id, fio, phone, pvz)
        await state.clear()
        await msg.answer("Контактные данные CDEK обновлены.")
        await send_profile(msg)

    @router.message(ProfileEdit.waiting_for_req_form)
    async def save_req(msg: Message, state: FSMContext):
        parts = [p.strip() for p in (msg.text or "").splitlines() if p.strip()]
        if len(parts) != 3:
            await msg.answer("Нужно три строки: ФИО, номер карты, банк.")
            return
        fio, card, bank = parts
        await db.set_requisites(msg.from_user.id, fio, card, bank)
        await state.clear()
        await msg.answer("Реквизиты обновлены.")
        await send_profile(msg)
