from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from config import Config
from db import Database
from keyboards import Keyboards
from states import ProfileEdit
from utils import safe_username


def setup_profile_handlers(router: Router, db: Database, cfg: Config):
    async def build_referral_text(user_id: int, bot) -> str:
        code = await db.ensure_referral_code(user_id)
        count = await db.get_referral_count(user_id)
        balance_cents = await db.get_referral_balance_cents(user_id)
        balance = balance_cents / 100.0

        bot_info = await bot.get_me()
        bot_username = bot_info.username or ""
        link = f"https://t.me/{bot_username}?start=ref_{code}" if bot_username else code

        text_lines = [
            "Ваша реферальная статистика:",
            f"Приглашено пользователей: {count}",
            f"Доход с комиссии: {balance:.2f} руб.",
            "",
            "Краткое руководство по реферальной программе:",
            "1) Передайте вашу реферальную ссылку новым пользователям.",
            "2) Пользователь переходит по ссылке и проходит регистрацию.",
            "3) Вам начисляется 10% от комиссии с его сделок, сумма",
            "   накапливается на реферальном балансе.",
            "",
            "Ваша реферальная ссылка:",
            link,
        ]
        return "\n".join(text_lines)

    async def send_profile_to(user_id: int, chat_id: int, bot):
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
                await bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=cfg.ASSETS_CHANNEL_ID,
                    message_id=cfg.PROFILE_BANNER_MESSAGE_ID,
                    caption=text,
                    reply_markup=Keyboards.profile_menu_inline(),
                )
                return
            except Exception:
                pass

        # fallback: просто текст + кнопки
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=Keyboards.profile_menu_inline())

    async def send_profile(msg: Message):
        await send_profile_to(msg.from_user.id, msg.chat.id, msg.bot)

    @router.message(F.text == "👤 Мой профиль")
    async def my_profile(msg: Message):
        await send_profile(msg)

    @router.message(F.text == "🤝 Рефералы")
    async def my_referrals(msg: Message):
        user_id = msg.from_user.id
        text = await build_referral_text(user_id, msg.bot)
        await msg.answer(text, reply_markup=Keyboards.referral_withdraw_kb())

    @router.callback_query(F.data == "profile:referrals")
    async def my_referrals_inline(cq: CallbackQuery):
        user_id = cq.from_user.id
        text = await build_referral_text(user_id, cq.bot)
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer(text, reply_markup=Keyboards.referral_withdraw_back_kb())
        await cq.answer()

    @router.callback_query(F.data == "referral:withdraw")
    async def referral_withdraw(cq: CallbackQuery):
        user_id = cq.from_user.id
        balance_cents = await db.get_referral_balance_cents(user_id)
        if balance_cents <= 0:
            await cq.answer("Нет доступных средств для вывода.", show_alert=True)
            return

        if not await db.request_referral_withdrawal(user_id, balance_cents):
            await cq.answer("Не удалось оформить вывод, попробуйте позже.", show_alert=True)
            return

        amount = balance_cents / 100.0
        req = await db.get_requisites(user_id) or {}
        req_text = (
            f"ФИО: {req.get('fio') or '—'}\n"
            f"Карта: {req.get('card') or '—'}\n"
            f"Банк: {req.get('bank') or '—'}"
        )
        request_text = (
            "Запрос на вывод реферальных средств.\n"
            f"Пользователь: {safe_username(cq.from_user.username, user_id)} (id {user_id})\n"
            f"Сумма: {amount:.2f} руб.\n\n"
            "Реквизиты:\n"
            f"{req_text}"
        )
        try:
            topic = await cq.bot.create_forum_topic(
                chat_id=cfg.MODERATION_CHAT_ID,
                name=f"Реферальный вывод #{user_id}",
            )
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=topic.message_thread_id,
                text=request_text,
            )
        except Exception:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик.\n\n{request_text}",
            )

        text = await build_referral_text(user_id, cq.bot)
        try:
            await cq.message.edit_text(text, reply_markup=Keyboards.referral_withdraw_back_kb())
        except Exception:
            await cq.message.answer(text, reply_markup=Keyboards.referral_withdraw_back_kb())
        await cq.answer("Запрос на вывод отправлен модератору.")

    @router.callback_query(F.data == "profile:cdek")
    async def edit_cdek(cq: CallbackQuery, state: FSMContext):
        await state.set_state(ProfileEdit.waiting_for_cdek_form)
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer(
            "Отправьте контактные данные CDEK тремя строками в формате:\n"
            "<b>ФИО\nтелефон\nадрес ПВЗ</b>",
            reply_markup=Keyboards.profile_back_inline(),
        )
        await cq.answer()

    @router.callback_query(F.data == "profile:req")
    async def edit_req(cq: CallbackQuery, state: FSMContext):
        await state.set_state(ProfileEdit.waiting_for_req_form)
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer(
            "Отправьте реквизиты тремя строками в формате:\n"
            "<b>ФИО\nномер карты\nбанк</b>",
            reply_markup=Keyboards.profile_back_inline(),
        )
        await cq.answer()

    @router.callback_query(F.data == "profile:back")
    async def profile_back(cq: CallbackQuery):
        try:
            await cq.message.delete()
        except Exception:
            pass
        await send_profile_to(cq.from_user.id, cq.message.chat.id, cq.bot)
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
