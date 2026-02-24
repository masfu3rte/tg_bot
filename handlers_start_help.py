from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

from config import Config
from db import Database
from keyboards import Keyboards
from states import OfferCreate
from utils import build_request_link

WELCOME_TEXT = (
    "<b>Приветствуем в нашем сервисе для оказания качественных и выгодных сделок!</b> \n\n"
    "В нижнем меню вы можете создать запрос на покупку, а так же настроить ваш профиль.\n\n"
    '<a href="https://t.me/durov">Гайд по пользованию для покупателей</a>.\n\n'
    '<a href="https://t.me/durov">Гайд по пользованию для продавцов</a>.\n\n'
    "Пусть каждая ваша сделка будет идеальной!"
)


def setup_start_help_handlers(router: Router, db: Database, cfg: Config):
    async def send_welcome_message(msg: Message):
        if cfg.ASSETS_CHANNEL_ID and cfg.START_BANNER_ID:
            try:
                await msg.bot.copy_message(
                    chat_id=msg.chat.id,
                    from_chat_id=cfg.ASSETS_CHANNEL_ID,
                    message_id=cfg.START_BANNER_ID,
                    caption=WELCOME_TEXT,
                    reply_markup=Keyboards.bottom_menu(),
                )
                return
            except Exception:
                pass
        await msg.answer(WELCOME_TEXT, reply_markup=Keyboards.bottom_menu())

    @router.message(CommandStart())
    async def cmd_start(msg: Message, state: FSMContext):
        user = msg.from_user
        text = msg.text or ""
        payload = None
        if " " in text:
            payload = text.split(" ", 1)[1].strip()

        is_new_user = await db.add_user(
            user_id=user.id,
            username=user.username,
            full_name=user.full_name or "",
        )
        await db.ensure_referral_code(user.id)

        accepted = await db.is_offer_accepted(user.id)

        # deep-link: /start req_<id> -> начать отклик
        if payload and payload.startswith("req_"):
            try:
                request_id = int(payload.split("_", 1)[1])
            except ValueError:
                request_id = None

            if request_id is None:
                await msg.answer("Заявка не найдена.")
                return

            req = await db.get_request(request_id)
            if not req or req["status"] == "deleted":
                await msg.answer("Заявка не найдена или была удалена.")
                return

            if not await db.has_full_profile(user.id):
                await msg.answer(
                    "Перед откликом заполните, пожалуйста, контактные данные CDEK "
                    "и реквизиты в разделе «Мой профиль»."
                )
                return

            if not accepted:
                await db.set_offer_accepted(user.id, True)

            await state.set_state(OfferCreate.waiting_for_price)
            await state.update_data(request_id=request_id)

            link = build_request_link(cfg, req)
            if link:
                caption = (
                    f'Вы откликаетесь на <a href="{link}">заявку №{request_id}</a>.\n'
                    "Укажите цену товара (в рублях, только число)."
                )
            else:
                caption = (
                    f"Вы откликаетесь на заявку №{request_id}.\n"
                    "Укажите цену товара (в рублях, только число)."
                )

            if req.get("photo_file_id"):
                await msg.answer_photo(photo=req["photo_file_id"], caption=caption)
            else:
                await msg.answer(caption)
            return

        if payload and payload.startswith("ref_") and is_new_user:
            ref_code = payload.split("_", 1)[1]
            referrer_id = await db.get_user_id_by_referral_code(ref_code)
            if referrer_id and referrer_id != user.id:
                existing_referrer = await db.get_referrer_id(user.id)
                if existing_referrer is None:
                    await db.set_referrer(user.id, referrer_id)

        # обычный /start
        if not accepted:
            caption = (
                "Перед началом использования сервиса просьба "
                "ознакомиться с нашей публичной офертой."
            )
            await msg.answer(
                caption,
                reply_markup=Keyboards.start_menu(
                    offer_url=cfg.OFFER_URL,
                    channel_url=cfg.CHANNEL_URL,
                ),
            )
            return

        await send_welcome_message(msg)

    @router.callback_query(F.data == "offer:accept")
    async def accept_offer(cq: CallbackQuery):
        user_id = cq.from_user.id
        if not await db.is_offer_accepted(user_id):
            await db.set_offer_accepted(user_id, True)
        await cq.message.edit_reply_markup(reply_markup=None)
        await send_welcome_message(cq.message)
        await cq.answer()

    @router.message(Command("help"))
    async def help_cmd(msg: Message):
        await msg.answer(
            "Выберите нужный раздел ниже:",
            reply_markup=Keyboards.help_menu(
                support_url=cfg.SUPPORT_URL,
                channel_url=cfg.CHANNEL_URL,
                requests_channel_url=cfg.REQUESTS_CHANNEL_URL,
                ads_url=cfg.ADS_URL,
            ),
        )
