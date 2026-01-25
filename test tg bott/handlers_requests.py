from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from config import Config
from db import Database
from keyboards import Keyboards
from states import RequestCreate
from utils import safe_username, build_direct_link


def setup_requests_handlers(router: Router, db: Database, cfg: Config):
    async def send_my_requests_menu(chat_id: int, bot):
        caption = (
            "Снизу вы можете управлять и следить за вашими созданными запросами, а так же "
            "создать новый запрос на поиск нужной вам вещи по лучшей цене."
        )
        if cfg.ASSETS_CHANNEL_ID and cfg.MY_REQUESTS_BANNER_MESSAGE_ID:
            try:
                await bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=cfg.ASSETS_CHANNEL_ID,
                    message_id=cfg.MY_REQUESTS_BANNER_MESSAGE_ID,
                    caption=caption,
                    reply_markup=Keyboards.my_requests_menu(),
                )
                return
            except Exception:
                pass
        await bot.send_message(
            chat_id=chat_id,
            text=caption,
            reply_markup=Keyboards.my_requests_menu(),
        )

    @router.message(F.text == "🧾 Мои запросы")
    async def my_requests_entry(msg: Message):
        await send_my_requests_menu(msg.chat.id, msg.bot)

    @router.message(F.text == "Вернуться")
    async def back_to_main(msg: Message):
        from keyboards import Keyboards as K
        await msg.answer("Главное меню:", reply_markup=K.bottom_menu())

    @router.message(F.text == "Создать новый запрос")
    async def new_request_start(msg: Message, state: FSMContext):
        if not await db.has_full_profile(msg.from_user.id):
            await msg.answer(
                "Перед созданием запроса заполните, пожалуйста, контактные данные CDEK "
                "и реквизиты в разделе «Мой профиль»."
            )
            return
        await state.set_state(RequestCreate.waiting_for_internal_title)
        await msg.answer("Укажите личное название запроса. Оно будет видно только вам.")

    @router.callback_query(F.data == "requests:new")
    async def new_request_start_inline(cq: CallbackQuery, state: FSMContext):
        try:
            await cq.message.delete()
        except Exception:
            pass
        if not await db.has_full_profile(cq.from_user.id):
            await cq.message.answer(
                "Перед созданием запроса заполните, пожалуйста, контактные данные CDEK "
                "и реквизиты в разделе «Мой профиль»."
            )
            await cq.answer()
            return
        await state.set_state(RequestCreate.waiting_for_internal_title)
        await cq.message.answer("Укажите личное название запроса. Оно будет видно только вам.")
        await cq.answer()

    @router.callback_query(F.data == "requests:back")
    async def back_to_main_inline(cq: CallbackQuery):
        from keyboards import Keyboards as K
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.message.answer("Главное меню:", reply_markup=K.bottom_menu())
        await cq.answer()

    @router.message(RequestCreate.waiting_for_internal_title)
    async def req_internal_title(msg: Message, state: FSMContext):
        await state.update_data(internal_title=msg.text.strip())
        await state.set_state(RequestCreate.waiting_for_item_name)
        await msg.answer("Укажите публичное название товара по которому продавец сможет его найти.")

    @router.message(RequestCreate.waiting_for_item_name)
    async def req_item_name(msg: Message, state: FSMContext):
        await state.update_data(item_name=msg.text.strip())
        await state.set_state(RequestCreate.waiting_for_description)
        await msg.answer("Укажите детали, необходимый цвет, предпочтительные сроки доставки и состояние товара.")

    @router.message(RequestCreate.waiting_for_description)
    async def req_description(msg: Message, state: FSMContext):
        await state.update_data(description=msg.text.strip())
        await state.set_state(RequestCreate.waiting_for_photo)
        await msg.answer(
            "Отправьте фотографию товара/примера для успешного поиска продавцом. (Только одно фото)",
            reply_markup=Keyboards.new_request_skip_photo_kb(),
        )

    @router.message(RequestCreate.waiting_for_photo, ~F.photo)
    async def req_photo_nonphoto(msg: Message, state: FSMContext):
        # если прилетит не фото — повторяем просьбу
        await msg.answer(
            "Отправьте фотографию товара/примера для успешного поиска продавцом. (Только одно фото)",
            reply_markup=Keyboards.new_request_skip_photo_kb(),
        )

    @router.callback_query(F.data == "request:skip_photo")
    async def req_skip_photo(cq: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        await state.clear()
        user = cq.from_user

        request_id = await db.create_request(
            user_id=user.id,
            internal_title=data["internal_title"],
            item_name=data["item_name"],
            description=data["description"],
            photo_file_id=None,
        )

        text = (
            f"Новый запрос #{request_id}\n"
            f"От: {safe_username(user.username, user.id)} (id {user.id})\n\n"
            f"{data['internal_title']}\n\n"
            f"Что нужно: {data['item_name']}\n\n"
            f"Описание:\n{data['description']}"
        )
        await cq.bot.send_message(
            chat_id=cfg.MODERATION_CHAT_ID,
            message_thread_id=cfg.MODERATION_TOPIC_ID,
            text=text,
            reply_markup=Keyboards.moderation_request_kb(request_id),
        )

        confirmation_text = (
            "Ваш запрос отправлен на модерацию. После проверки он будет опубликован в канале."
        )

        if cfg.ASSETS_CHANNEL_ID and cfg.REQUEST_SENT_BANNER_MESSAGE_ID:
            try:
                await cq.bot.copy_message(
                    chat_id=user.id,
                    from_chat_id=cfg.ASSETS_CHANNEL_ID,
                    message_id=cfg.REQUEST_SENT_BANNER_MESSAGE_ID,
                    caption=confirmation_text,
                )
            except Exception:
                await cq.bot.send_message(chat_id=user.id, text=confirmation_text)
        else:
            await cq.bot.send_message(chat_id=user.id, text=confirmation_text)
        await cq.answer()

    @router.message(RequestCreate.waiting_for_photo, F.photo)
    async def req_photo(msg: Message, state: FSMContext):
        data = await state.get_data()
        await state.clear()
        user = msg.from_user
        photo = msg.photo[-1]

        request_id = await db.create_request(
            user_id=user.id,
            internal_title=data["internal_title"],
            item_name=data["item_name"],
            description=data["description"],
            photo_file_id=photo.file_id,
        )

        caption = (
            f"Новый запрос #{request_id}\n"
            f"От: {safe_username(user.username, user.id)} (id {user.id})\n\n"
            f"{data['internal_title']}\n\n"
            f"Что нужно: {data['item_name']}\n\n"
            f"Описание:\n{data['description']}"
        )

        await msg.bot.send_photo(
            chat_id=cfg.MODERATION_CHAT_ID,
            message_thread_id=cfg.MODERATION_TOPIC_ID,
            photo=photo.file_id,
            caption=caption,
            reply_markup=Keyboards.moderation_request_kb(request_id),
        )

        confirmation_text = (
            "Ваш запрос отправлен на модерацию. После проверки он будет опубликован в канале."
        )

        if cfg.ASSETS_CHANNEL_ID and cfg.REQUEST_SENT_BANNER_MESSAGE_ID:
            try:
                await msg.bot.copy_message(
                    chat_id=user.id,
                    from_chat_id=cfg.ASSETS_CHANNEL_ID,
                    message_id=cfg.REQUEST_SENT_BANNER_MESSAGE_ID,
                    caption=confirmation_text,
                )
            except Exception:
                await msg.answer(confirmation_text)
        else:
            await msg.answer(confirmation_text)

    # ===== модерация запросов =====

    @router.callback_query(F.data.startswith("request:approve:"))
    async def approve_request(cq: CallbackQuery):
        request_id = int(cq.data.split(":")[-1])
        req = await db.get_request(request_id)
        if not req:
            await cq.answer("Запрос не найден.", show_alert=True)
            return

        await db.set_request_status(request_id, "approved")

        # пост в канал (как на скрине)
        post_text = (
            f"Заявка №{request_id}\n"
            f"• Название: {req['item_name']}\n"
            f"• Описание: {req['description']}"
        )

        bot_me = await cq.bot.get_me()
        bot_link = f"https://t.me/{bot_me.username}?start=req_{request_id}"

        if req["photo_file_id"]:
            sent = await cq.bot.send_photo(
                chat_id=cfg.REQUESTS_PUBLIC_CHANNEL_ID,
                photo=req["photo_file_id"],
                caption=post_text,
                reply_markup=Keyboards.request_public_kb(bot_link),
            )
            is_photo = True
        else:
            sent = await cq.bot.send_message(
                chat_id=cfg.REQUESTS_PUBLIC_CHANNEL_ID,
                text=post_text,
                reply_markup=Keyboards.request_public_kb(bot_link),
            )
            is_photo = False

        # сохранить message_id поста
        await db.save_request_channel_message(
            request_id=request_id, message_id=sent.message_id, is_photo=is_photo
        )

        # ссылка именно на пост
        post_link = build_direct_link(cfg, sent.message_id)
        owner_id = req["user_id"]
        try:
            await cq.bot.send_message(
                chat_id=owner_id,
                text=(
                    f"Ваш <a href='{post_link}'>запрос №{request_id}</a> одобрен и опубликован в канале.\n"
                    f"Вам придет уведомление если кто-то откликнется на ваш запрос."
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

        # убрать кнопки модерации под исходным сообщением
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Запрос одобрен.")

    @router.callback_query(F.data.startswith("request:reject:"))
    async def reject_request(cq: CallbackQuery):
        request_id = int(cq.data.split(":")[-1])
        req = await db.get_request(request_id)
        if not req:
            await cq.answer("Запрос не найден.", show_alert=True)
            return

        await db.set_request_status(request_id, "rejected")
        owner_id = req["user_id"]

        try:
            await cq.bot.send_message(
                chat_id=owner_id,
                text=f"Ваш запрос №{request_id} отклонён модератором.",
            )
        except Exception:
            pass

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Запрос отклонён.")
