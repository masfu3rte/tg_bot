import logging

logger = logging.getLogger(__name__)
from access import secure_messages
from utils import escape_record
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from config import Config
from db import Database
from keyboards import Keyboards
from states import RequestCreate
from utils import escape_text
from access import secure_callbacks
from delivery import content, enqueue, queue_batch
from utils import build_direct_link, work_chat_user


def setup_requests_handlers(router: Router, db: Database, cfg: Config):
    message_start = len(router.message.handlers)
    callback_start = len(router.callback_query.handlers)

    async def submit_request(user, msg, bot, state, photo):
        if not await db.is_offer_accepted(user.id):
            await msg.answer("Сначала примите условия через /start.")
            return
        data = await state.get_data()
        if not all(
            data.get(key) for key in ("internal_title", "item_name", "description")
        ):
            await msg.answer("Форма устарела. Начните новый запрос.")
            return
        request_id = data.get("saved_request_id")
        if not request_id:
            request_id = await db.create_request(
                user.id,
                data["internal_title"],
                data["item_name"],
                data["description"],
                photo,
            )
            await state.update_data(saved_request_id=request_id)
        text = (
            f"Новый запрос #{request_id}\nОт: {work_chat_user(user.username,user.id)}\n\n"
            f"{escape_text(data['internal_title'])}\nЧто нужно: {escape_text(data['item_name'])}\n"
            f"Описание: {escape_text(data['description'])}"
        )
        jobs = content(
            cfg.MODERATION_CHAT_ID,
            text,
            photo=photo,
            message_thread_id=cfg.MODERATION_TOPIC_ID,
            reply_markup=Keyboards.moderation_request_kb(request_id),
        )
        delivered = await queue_batch(db, bot, f"new-request-{request_id}", jobs)
        await state.clear()
        await msg.answer(
            "Ваш запрос отправлен на модерацию."
            if delivered
            else "Запрос сохранён. Доставка модератору будет повторена автоматически."
        )

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
            except Exception as exc:
                logger.warning(
                    "Optional Telegram operation failed: %s", type(exc).__name__
                )
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
        if not await db.is_offer_accepted(msg.from_user.id):
            await msg.answer("Сначала примите условия через /start.")
            return
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
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        if not await db.is_offer_accepted(cq.from_user.id):
            await cq.answer("Сначала примите условия через /start.", show_alert=True)
            return
        if not await db.has_full_profile(cq.from_user.id):
            await cq.message.answer(
                "Перед созданием запроса заполните, пожалуйста, контактные данные CDEK "
                "и реквизиты в разделе «Мой профиль»."
            )
            await cq.answer()
            return
        await state.set_state(RequestCreate.waiting_for_internal_title)
        await cq.message.answer(
            "Укажите личное название запроса. Оно будет видно только вам."
        )
        await cq.answer()

    @router.callback_query(F.data == "requests:back")
    async def back_to_main_inline(cq: CallbackQuery):
        from keyboards import Keyboards as K

        try:
            await cq.message.delete()
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        await cq.message.answer("Главное меню:", reply_markup=K.bottom_menu())
        await cq.answer()

    @router.callback_query(F.data == "requests:menu")
    async def back_to_requests_menu_inline(cq: CallbackQuery):
        try:
            await cq.message.delete()
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        await send_my_requests_menu(cq.message.chat.id, cq.bot)
        await cq.answer()

    @router.message(RequestCreate.waiting_for_internal_title)
    async def req_internal_title(msg: Message, state: FSMContext):
        value = (msg.text or "").strip()
        if not value or len(value) > 100:
            await msg.answer("Отправьте текст от 1 до 100 символов.")
            return
        await state.update_data(internal_title=value)
        await state.set_state(RequestCreate.waiting_for_item_name)
        await msg.answer(
            "Укажите публичное название товара.",
            reply_markup=(
                Keyboards.new_request_skip_photo_kb()
                if "internal_title" == "description"
                else None
            ),
        )

    @router.message(RequestCreate.waiting_for_item_name)
    async def req_item_name(msg: Message, state: FSMContext):
        value = (msg.text or "").strip()
        if not value or len(value) > 150:
            await msg.answer("Отправьте текст от 1 до 150 символов.")
            return
        await state.update_data(item_name=value)
        await state.set_state(RequestCreate.waiting_for_description)
        await msg.answer(
            "Укажите детали, цвет, сроки и состояние товара.",
            reply_markup=(
                Keyboards.new_request_skip_photo_kb()
                if "item_name" == "description"
                else None
            ),
        )

    @router.message(RequestCreate.waiting_for_description)
    async def req_description(msg: Message, state: FSMContext):
        value = (msg.text or "").strip()
        if not value or len(value) > 1500:
            await msg.answer("Отправьте текст от 1 до 1500 символов.")
            return
        await state.update_data(description=value)
        await state.set_state(RequestCreate.waiting_for_photo)
        await msg.answer(
            "Пришлите одно фото товара или пропустите этот шаг.",
            reply_markup=(
                Keyboards.new_request_skip_photo_kb()
                if "description" == "description"
                else None
            ),
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
        if await state.get_state() != RequestCreate.waiting_for_photo.state:
            await cq.answer("Кнопка устарела. Откройте форму заново.", show_alert=True)
            return
        await submit_request(cq.from_user, cq.message, cq.bot, state, None)
        await cq.answer()

    @router.message(RequestCreate.waiting_for_photo, F.photo)
    async def req_photo(msg: Message, state: FSMContext):
        await submit_request(msg.from_user, msg, msg.bot, state, msg.photo[-1].file_id)

    # ===== модерация запросов =====

    @router.callback_query(F.data.startswith("request:approve:"))
    async def approve_request(cq: CallbackQuery):
        request_id = int(cq.data.split(":")[2])
        req = escape_record(await db.get_request(request_id))
        if not req or req["status"] != "pending":
            await cq.answer("Заявка уже обработана.", show_alert=True)
            return
        post_text = f"Заявка №{request_id}\n• Название: {escape_text(req['item_name'])}\n• Описание: {escape_text(req['description'])}"
        me = await cq.bot.get_me()
        jobs = content(
            cfg.REQUESTS_PUBLIC_CHANNEL_ID,
            post_text,
            photo=req["photo_file_id"],
            reply_markup=Keyboards.request_public_kb(
                f"https://t.me/{me.username}?start=req_{request_id}"
            ),
            effect={
                "kind": "publish_request",
                "id": request_id,
                "revision": req["revision"],
            },
        )
        if req.get("channel_message_id") and (
            not req["channel_is_photo"] or len(post_text) <= 950
        ):
            method = (
                "edit_message_caption"
                if req["channel_is_photo"]
                else "edit_message_text"
            )
            kwargs = dict(
                chat_id=cfg.REQUESTS_PUBLIC_CHANNEL_ID,
                message_id=req["channel_message_id"],
                reply_markup=Keyboards.request_public_kb(
                    f"https://t.me/{me.username}?start=req_{request_id}"
                ),
            )
            kwargs["caption" if req["channel_is_photo"] else "text"] = post_text
            jobs = [
                dict(
                    method=method,
                    kwargs=kwargs,
                    effect={
                        "kind": "publish_request",
                        "id": request_id,
                        "revision": req["revision"],
                    },
                )
            ]
        elif req.get("channel_message_id"):
            deletes = [
                dict(
                    method="delete_message",
                    kwargs=dict(chat_id=cfg.REQUESTS_PUBLIC_CHANNEL_ID, message_id=mid),
                )
                for mid in (
                    req.get("channel_message_id"),
                    req.get("channel_media_message_id"),
                )
                if mid
            ]
            jobs = deletes + jobs
        with db.conn:
            changed = db.conn.execute(
                "UPDATE requests SET status='publishing' WHERE id=? AND status='pending'",
                (request_id,),
            ).rowcount
            if not changed:
                await cq.answer("Публикация уже запущена.")
                return
            enqueue(db, f'publish-request-{request_id}-{req["revision"]}', jobs)
        delivered = await queue_batch(
            db, cq.bot, f'publish-request-{request_id}-{req["revision"]}', jobs
        )
        if delivered:
            req = escape_record(await db.get_request(request_id))
            link = build_direct_link(cfg, req["channel_message_id"])
            await queue_batch(
                db,
                cq.bot,
                f"request-approved-notify-{request_id}",
                content(
                    req["user_id"],
                    f'Ваш <a href="{link}">запрос №{request_id}</a> опубликован.',
                ),
            )
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.answer(
            "Запрос опубликован."
            if delivered
            else "Публикация сохранена в очереди. Проверяйте /delivery_status."
        )

    @router.callback_query(F.data.startswith("request:reject:"))
    async def reject_request(cq: CallbackQuery):
        request_id = int(cq.data.split(":")[2])
        req = escape_record(await db.get_request(request_id))
        with db.conn:
            changed = db.conn.execute(
                "UPDATE requests SET status='rejected' WHERE id=? AND status='pending'",
                (request_id,),
            ).rowcount
            if not changed:
                await cq.answer("Заявка уже обработана.")
                return
            jobs = content(
                req["user_id"], f"Ваш запрос №{request_id} отклонён модератором."
            )
            enqueue(db, f"request-rejected-{request_id}", jobs)
        await queue_batch(db, cq.bot, f"request-rejected-{request_id}", jobs)
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.answer("Запрос отклонён.")

    secure_callbacks(router, db, cfg, callback_start)

    secure_messages(router, db, cfg, message_start)
