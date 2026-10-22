from delivery import notify
from access import secure_messages
from utils import escape_record
from datetime import datetime, timedelta
import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from config import Config
from db import Database
from keyboards import Keyboards
from states import DealTrack, RequestEdit
from access import secure_callbacks
from delivery import answer_content, content, queue_batch
from utils import escape_text
from money import amounts
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import build_request_link

from handlers_offers_deals import DEAL_STATUS_STEPS

logger = logging.getLogger(__name__)
TRACK_STATUS_LABELS = {
    "none": "Не указан",
    "pending": "На проверке",
    "approved": "Подтвержден",
    "rejected": "Отклонен",
}


def deal_status_index(value: int | None) -> int:
    return max(0, min(len(DEAL_STATUS_STEPS), value or 0))


def deal_status_name(value: int | None) -> str:
    return (
        DEAL_STATUS_STEPS[deal_status_index(value) - 1]
        if deal_status_index(value)
        else "Ожидается внесение залогов"
    )


def setup_sliders_handlers(router: Router, db: Database, cfg: Config):
    message_start = len(router.message.handlers)
    callback_start = len(router.callback_query.handlers)

    # ===== Слайдер "Мои запросы" =====
    async def ensure_moderation_thread_id(offer_id: int, bot) -> int | None:
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            return None
        if offer.get("moderation_thread_id"):
            return offer["moderation_thread_id"]

        existing_thread_id = await db.get_request_moderation_thread_id(
            offer["request_id"]
        )
        if existing_thread_id:
            await db.set_offer_moderation_thread_id(offer_id, existing_thread_id)
            return existing_thread_id

        try:
            topic = await bot.create_forum_topic(
                chat_id=cfg.MODERATION_CHAT_ID,
                name=f"Сделка №{offer['request_id']}",
            )
            await db.set_offer_moderation_thread_id(offer_id, topic.message_thread_id)
            return topic.message_thread_id
        except Exception:
            return None

    async def send_request_card(msg_obj, user_id: int, req: dict):
        req = escape_record(req)
        index, total = await db.get_active_request_index_and_total(user_id, req["id"])

        deal = await db.get_accepted_offer_for_request(req["id"])
        title_prefix = "Сделка" if deal else "Заявка"
        text_lines = [
            f"{title_prefix} №{req['id']}",
            f"Личное название: {req.get('internal_title') or ''}",
            f"Название предмета: {req.get('item_name') or ''}",
            "",
            f"Описание:\n{req.get('description') or ''}",
        ]

        if deal:
            money = amounts(deal["price_cents"])
            buyer_total = money["total"] / 100
            buyer_deposit = money["buyer_deposit"] / 100
            status_name = deal_status_name(deal["deal_status"])
            if deal.get("final_payment_status") == "confirmed":
                remaining = 0
            elif deal.get("buyer_deposit_status") == "confirmed":
                remaining = max(buyer_total - buyer_deposit, 0)
            else:
                remaining = buyer_total
            text_lines.append("")
            text_lines.append(f"Статус сделки: {status_name}")
            text_lines.append(f"Осталось оплатить: {remaining:.2f}₽")

        text = "\n".join(text_lines)
        kb = Keyboards.user_requests_slider_kb(req["id"], index, total)

        await answer_content(
            msg_obj, text, photo=req.get("photo_file_id"), reply_markup=kb
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

    @router.message(F.text == "Активные запросы")
    async def requests_slider_start(msg: Message):
        user_id = msg.from_user.id
        req = escape_record(await db.get_first_active_request(user_id))
        if not req:
            await send_my_requests_menu(msg.chat.id, msg.bot)
            return
        await send_request_card(msg, user_id, req)

    @router.callback_query(F.data == "requests:active")
    async def requests_slider_start_inline(cq: CallbackQuery):
        user_id = cq.from_user.id
        req = escape_record(await db.get_first_active_request(user_id))
        try:
            await cq.message.delete()
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        if not req:
            await send_my_requests_menu(cq.message.chat.id, cq.bot)
            await cq.answer()
            return
        await send_request_card(cq.message, user_id, req)
        await cq.answer()

    @router.callback_query(F.data.startswith("ur:prev:"))
    async def req_prev(cq: CallbackQuery):
        req_id = int(cq.data.split(":")[-1])
        current = escape_record(await db.get_request(req_id))
        if not current:
            await cq.answer()
            return
        user_id = current["user_id"]
        prev_req = await db.get_adjacent_active_request(user_id, req_id, "prev")
        target = prev_req or current
        await cq.message.delete()
        await send_request_card(cq.message, user_id, target)
        await cq.answer()

    @router.callback_query(F.data.startswith("ur:next:"))
    async def req_next(cq: CallbackQuery):
        req_id = int(cq.data.split(":")[-1])
        current = escape_record(await db.get_request(req_id))
        if not current:
            await cq.answer()
            return
        user_id = current["user_id"]
        next_req = await db.get_adjacent_active_request(user_id, req_id, "next")
        target = next_req or current
        await cq.message.delete()
        await send_request_card(cq.message, user_id, target)
        await cq.answer()

    @router.callback_query(F.data.startswith("ur:edit:"))
    async def req_edit_start(cq: CallbackQuery, state: FSMContext):
        req_id = int(cq.data.split(":")[-1])
        if (await db.get_request(req_id))["status"] not in (
            "pending",
            "approved",
            "rejected",
        ) or await db.has_deal_for_request(req_id):
            await cq.answer("Заявку со сделкой нельзя редактировать.", show_alert=True)
            return
        await state.clear()
        await state.set_data({"editing_request_id": req_id})
        await state.set_state(RequestEdit.choosing_field)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=label, callback_data="request:edit_field:" + field
                    )
                ]
                for field, label in [
                    ("internal_title", "Личное название"),
                    ("item_name", "Название товара"),
                    ("description", "Описание"),
                ]
            ]
            + [
                [
                    InlineKeyboardButton(
                        text="Отмена", callback_data="request:edit_cancel"
                    )
                ]
            ]
        )
        await cq.message.answer("Выберите поле для изменения:", reply_markup=keyboard)
        await cq.answer()

    @router.callback_query(F.data.startswith("ur:del:"))
    async def req_delete(cq: CallbackQuery):
        req_id = int(cq.data.split(":")[-1])
        req = escape_record(await db.get_request(req_id))
        if not await db.delete_request(req_id, cq.from_user.id):
            await cq.answer(
                "Нельзя удалить заявку со сделкой или отправляемую заявку.",
                show_alert=True,
            )
            return
        jobs = [
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
        delivered = (
            await queue_batch(db, cq.bot, f"delete-request-{req_id}", jobs)
            if jobs
            else True
        )
        await cq.message.delete()
        await cq.answer(
            "Заявка удалена."
            if delivered
            else "Заявка закрыта; удаление поста ожидает доставки."
        )

    # ===== «Мои отклики» (с баннером id=5) =====

    async def send_my_offers_menu(chat_id: int, bot):
        text = "Раздел «Мои отклики»."
        if cfg.ASSETS_CHANNEL_ID and cfg.MY_OFFERS_BANNER_MESSAGE_ID:
            try:
                await bot.copy_message(
                    chat_id=chat_id,
                    from_chat_id=cfg.ASSETS_CHANNEL_ID,
                    message_id=cfg.MY_OFFERS_BANNER_MESSAGE_ID,
                    caption=text,
                    reply_markup=Keyboards.my_offers_menu(),
                )
                return
            except Exception as exc:
                logger.warning(
                    "Optional Telegram operation failed: %s", type(exc).__name__
                )
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=Keyboards.my_offers_menu(),
        )

    @router.message(F.text == "📮 Мои отклики")
    async def my_offers_entry(msg: Message):
        await send_my_offers_menu(msg.chat.id, msg.bot)

    # ===== Слайдер «Активные отклики» =====

    async def build_offer_card(user_id: int, offer: dict):
        offer = escape_record(offer)
        index, total = await db.get_offer_index_and_total(user_id, offer["id"])
        req = escape_record(await db.get_request(offer["request_id"])) or {}
        link = build_request_link(cfg, req) or ""
        deal_line = (
            f'<a href="{link}">Сделка №{offer["request_id"]}</a>'
            if link
            else f"Сделка №{offer['request_id']}"
        )

        base_price = offer["price_cents"] / 100.0
        created_at = offer.get("created_at")
        due_date = ""
        if created_at:
            try:
                parsed = datetime.fromisoformat(str(created_at).replace("Z", ""))
                days = offer.get("days") or 0
                due_date = (parsed + timedelta(days=days)).strftime("%d.%m.%Y")
            except Exception:
                due_date = ""

        text_lines = [
            deal_line,
            f"Заявка №{offer['request_id']}",
            "",
            f"Цена: {base_price:.2f} руб.",
            (
                f"Срок доставки: (до {due_date})"
                if due_date
                else "Срок доставки: (до мм.дд.гггг)"
            ),
            f"Состояние: {offer['condition']}/10",
            "",
            f"Статус сделки: {deal_status_name(offer['deal_status'])}",
        ]
        if offer.get("track_number"):
            track_status = TRACK_STATUS_LABELS.get(
                offer.get("track_status"), "Неизвестен"
            )
            text_lines.append(f"Трек-номер: {offer['track_number']} ({track_status})")

        text = "\n".join(text_lines)
        kb = Keyboards.user_offers_slider_kb(offer["id"], index, total)
        return text, kb

    async def send_offer_card(msg_obj, user_id: int, offer: dict):
        text, kb = await build_offer_card(user_id, offer)
        await answer_content(
            msg_obj, text, photo=offer.get("photo_file_id"), reply_markup=kb
        )

    @router.message(F.text == "Активные отклики")
    async def offers_slider_start(msg: Message):
        user_id = msg.from_user.id
        offer = escape_record(await db.get_first_offer_for_user(user_id))
        if not offer:
            await msg.answer("Откликов нет.")
            return
        await send_offer_card(msg, user_id, offer)

    @router.callback_query(F.data == "offers:active")
    async def offers_slider_start_inline(cq: CallbackQuery):
        user_id = cq.from_user.id
        offer = escape_record(await db.get_first_offer_for_user(user_id))
        try:
            await cq.message.delete()
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        if not offer:
            await cq.message.answer("Откликов нет.")
            await cq.answer()
            return
        await send_offer_card(cq.message, user_id, offer)
        await cq.answer()

    @router.callback_query(F.data == "offers:back")
    async def offers_back_inline(cq: CallbackQuery):
        from keyboards import Keyboards as K

        try:
            await cq.message.delete()
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        await cq.message.answer("Главное меню:", reply_markup=K.bottom_menu())
        await cq.answer()

    @router.callback_query(F.data.startswith("uo:prev:"))
    async def offer_prev(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer()
            return
        user_id = offer["buyer_id"]
        prev_offer = await db.get_adjacent_offer_for_user(user_id, offer_id, "prev")
        target = prev_offer or offer
        await cq.message.delete()
        await send_offer_card(cq.message, user_id, target)
        await cq.answer()

    @router.callback_query(F.data.startswith("uo:next:"))
    async def offer_next(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer()
            return
        user_id = offer["buyer_id"]
        next_offer = await db.get_adjacent_offer_for_user(user_id, offer_id, "next")
        target = next_offer or offer
        await cq.message.delete()
        await send_offer_card(cq.message, user_id, target)
        await cq.answer()

    # ===== изменение статуса (меню) =====

    async def refresh_status_card(
        cq: CallbackQuery, state: FSMContext, offer: dict
    ) -> bool:
        source = (await state.get_data()).get("deal_status_card")
        if not source or source["offer_id"] != offer["id"]:
            return False
        text, kb = await build_offer_card(offer["buyer_id"], offer)
        try:
            if source["is_photo"]:
                await cq.bot.edit_message_caption(
                    chat_id=source["chat_id"],
                    message_id=source["message_id"],
                    caption=text,
                    reply_markup=kb,
                )
            else:
                await cq.bot.edit_message_text(
                    chat_id=source["chat_id"],
                    message_id=source["message_id"],
                    text=text,
                    reply_markup=kb,
                )
        except Exception as exc:
            if "message is not modified" in str(exc).lower():
                return True
            logger.warning(
                "Не удалось обновить карточку сделки %s: %s",
                offer["id"],
                type(exc).__name__,
            )
            return False
        return True

    @router.callback_query(F.data.startswith("deal:status_menu:"))
    async def deal_status_menu(cq: CallbackQuery, state: FSMContext):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return
        if offer["buyer_id"] != cq.from_user.id:
            await cq.answer("Менять статус может только продавец.", show_alert=True)
            return
        idx = deal_status_index(offer["deal_status"])
        await state.update_data(
            deal_status_card={
                "offer_id": offer_id,
                "chat_id": cq.message.chat.id,
                "message_id": cq.message.message_id,
                "is_photo": bool(cq.message.photo),
            }
        )
        req = escape_record(await db.get_request(offer["request_id"])) or {}
        link = build_request_link(cfg, req) or f"Сделка №{offer['request_id']}"

        text = (
            f"{link}\n"
            f"Статус сделки №{offer['request_id']}.\n"
            "Выберите нужный статус из списка ниже."
        )
        await cq.message.answer(
            text,
            reply_markup=Keyboards.deal_status_menu_kb(
                offer_id, idx, DEAL_STATUS_STEPS
            ),
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("deal:status_pick:"))
    async def deal_status_set(cq: CallbackQuery, state: FSMContext):
        try:
            _, _, offer_s, idx_s = cq.data.split(":")
            offer_id = int(offer_s)
            idx = int(idx_s)
        except (ValueError, TypeError):
            await cq.answer("Неверный статус сделки.", show_alert=True)
            return
        if not 1 <= idx <= len(DEAL_STATUS_STEPS):
            await cq.answer("Неверный статус сделки.", show_alert=True)
            return
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return
        if offer["buyer_id"] != cq.from_user.id:
            await cq.answer("Менять статус может только продавец.", show_alert=True)
            return

        if not await db.change_stage(offer_id, cq.from_user.id, idx):
            await cq.answer(
                "Этот переход недоступен. Этапы нельзя менять назад.", show_alert=True
            )
            return
        status_name = deal_status_name(idx)

        seller_id = offer["buyer_id"]
        req = escape_record(await db.get_request(offer["request_id"])) or {}
        buyer_id = req.get("user_id")

        if idx == 5:
            try:
                await notify(
                    db,
                    cq.bot,
                    "send_message",
                    chat_id=seller_id,
                    text=cfg.MANAGER_CDEK_CONTACT_TEXT,
                )
            except Exception as exc:
                logger.warning(
                    "Optional Telegram operation failed: %s", type(exc).__name__
                )

        if idx == 6:
            await state.set_state(DealTrack.waiting_for_track)
            await state.update_data(offer_id=offer_id)
            try:
                await notify(
                    db,
                    cq.bot,
                    "send_message",
                    chat_id=seller_id,
                    text=(
                        "Статус обновлён на «Товар в пути до менеджера».\n"
                        "Отправьте, пожалуйста, трек-номер заказа для менеджера "
                        "одним сообщением в ответ на это сообщение."
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "Optional Telegram operation failed: %s", type(exc).__name__
                )

            try:
                await notify(
                    db,
                    cq.bot,
                    "send_message",
                    chat_id=cfg.MODERATION_CHAT_ID,
                    message_thread_id=cfg.MODERATION_TOPIC_ID,
                    text=f"Продавец поставил статус «Товар в пути до менеджера» по сделке №{offer['request_id']}.",
                )
            except Exception as exc:
                logger.warning(
                    "Не удалось уведомить модерацию о статусе сделки %s: %s",
                    offer_id,
                    type(exc).__name__,
                )
            moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
            if moderation_thread_id and moderation_thread_id != cfg.MODERATION_TOPIC_ID:
                try:
                    await notify(
                        db,
                        cq.bot,
                        "send_message",
                        chat_id=cfg.MODERATION_CHAT_ID,
                        message_thread_id=moderation_thread_id,
                        text=(
                            "Продавец поставил статус «Товар в пути до менеджера» "
                            f"по сделке №{offer['request_id']}."
                        ),
                    )
                except Exception as exc:
                    logger.warning(
                        "Optional Telegram operation failed: %s", type(exc).__name__
                    )

        if buyer_id:
            try:
                await notify(
                    db,
                    cq.bot,
                    "send_message",
                    chat_id=buyer_id,
                    text=f"Статус вашей сделки №{offer['request_id']} изменён на: {status_name}.",
                )
            except Exception as exc:
                logger.warning(
                    "Optional Telegram operation failed: %s", type(exc).__name__
                )

        updated_offer = escape_record(await db.get_offer(offer_id))
        if not await refresh_status_card(cq, state, updated_offer):
            await send_offer_card(cq.message, seller_id, updated_offer)

        try:
            await cq.message.edit_text(
                f"Статус сделки №{offer['request_id']}.\n"
                "Выберите нужный статус из списка ниже.",
                reply_markup=Keyboards.deal_status_menu_kb(
                    offer_id, idx, DEAL_STATUS_STEPS
                ),
            )
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        await cq.answer("Статус обновлён.")

    @router.callback_query(F.data.startswith("deal:status_back:"))
    async def deal_status_back(cq: CallbackQuery, state: FSMContext):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer or offer["buyer_id"] != cq.from_user.id:
            await cq.answer("Сделка не найдена или недоступна.", show_alert=True)
            return
        if not await refresh_status_card(cq, state, offer):
            await send_offer_card(cq.message, cq.from_user.id, offer)
        try:
            await cq.message.delete()
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        data = await state.get_data()
        data.pop("deal_status_card", None)
        await state.set_data(data)
        await cq.answer()

    @router.callback_query(F.data.startswith("request:edit_field:"))
    async def edit_request_field(cq: CallbackQuery, state: FSMContext):
        field = cq.data.split(":")[-1]
        if (
            field not in ("internal_title", "item_name", "description")
            or await state.get_state() != RequestEdit.choosing_field.state
        ):
            await cq.answer("Кнопка устарела.", show_alert=True)
            return
        await state.update_data(editing_field=field)
        await state.set_state(RequestEdit.waiting_for_value)
        await cq.message.answer("Пришлите новое значение текстом. Для отмены: /cancel.")
        await cq.answer()

    @router.callback_query(F.data == "request:edit_cancel")
    async def edit_request_cancel(cq: CallbackQuery, state: FSMContext):
        await state.clear()
        await cq.answer("Изменения отменены.")

    @router.message(RequestEdit.waiting_for_value)
    async def save_request_edit(msg: Message, state: FSMContext):
        data = await state.get_data()
        req_id = data.get("editing_request_id")
        field = data.get("editing_field")
        req = escape_record(await db.get_request(req_id)) if req_id else None
        limit = {"internal_title": 100, "item_name": 150, "description": 1500}.get(
            field, 0
        )
        value = (msg.text or "").strip()
        if (
            not req
            or req["status"] not in ("pending", "approved", "rejected")
            or req["user_id"] != msg.from_user.id
            or await db.has_deal_for_request(req_id)
        ):
            await msg.answer("Редактирование этой заявки недоступно.")
            return
        if not limit or not 1 <= len(value) <= limit:
            await msg.answer(f"Отправьте текст от 1 до {limit} символов.")
            return
        with db.conn:
            db.conn.execute(
                f"UPDATE requests SET {field}=?,revision=revision+CASE WHEN ?='internal_title' THEN 0 ELSE 1 END,status=CASE WHEN ?='internal_title' THEN status ELSE 'pending' END WHERE id=?",
                (value, field, field, req_id),
            )
        updated = escape_record(await db.get_request(req_id))
        if field != "internal_title":
            text = f"Повторная модерация заявки №{req_id}\nНазвание: {escape_text(updated['item_name'])}\nОписание: {escape_text(updated['description'])}"
            jobs = content(
                cfg.MODERATION_CHAT_ID,
                text,
                photo=updated["photo_file_id"],
                message_thread_id=cfg.MODERATION_TOPIC_ID,
                reply_markup=Keyboards.moderation_request_kb(
                    req_id, updated["revision"]
                ),
            )
            delivered = await queue_batch(
                db, msg.bot, f"edit-request-{req_id}-{updated['revision']}", jobs
            )
            await msg.answer(
                "Изменения сохранены и отправлены на повторную модерацию."
                if delivered
                else "Изменения сохранены; повторная модерация в очереди доставки."
            )
        else:
            await msg.answer("Личное название изменено.")
        await state.clear()

    @router.callback_query(
        F.data.startswith("ur:noop:") | F.data.startswith("uo:noop:")
    )
    async def noop_callback(cq: CallbackQuery):
        await cq.answer()

    @router.callback_query(F.data == "requests:archive")
    async def requests_archive(cq: CallbackQuery):
        rows = db.conn.execute(
            "SELECT id,item_name,status FROM requests WHERE user_id=? AND (status IN ('rejected','deleted') OR EXISTS (SELECT 1 FROM offers o WHERE o.request_id=requests.id AND o.final_payment_status='confirmed')) ORDER BY id DESC LIMIT 50",
            (cq.from_user.id,),
        ).fetchall()
        await answer_content(
            cq.message,
            (
                "Архив запросов:\n"
                + "\n".join(
                    f"№{r['id']} — {escape_text(r['item_name'] or '')}" for r in rows
                )
                if rows
                else "Архив запросов пуст."
            ),
        )
        await cq.answer()

    @router.callback_query(F.data == "offers:archive")
    async def offers_archive(cq: CallbackQuery):
        rows = db.conn.execute(
            "SELECT id,request_id,final_status FROM offers WHERE buyer_id=? AND (status IN ('rejected','buyer_rejected','closed_by_seller','cancelled','refunded') OR final_payment_status='confirmed') ORDER BY id DESC LIMIT 50",
            (cq.from_user.id,),
        ).fetchall()
        await answer_content(
            cq.message,
            (
                "Архив откликов:\n"
                + "\n".join(
                    f"Отклик №{r['id']}, заявка №{r['request_id']}" for r in rows
                )
                if rows
                else "Архив откликов пуст."
            ),
        )
        await cq.answer()

    secure_callbacks(router, db, cfg, callback_start)

    secure_messages(router, db, cfg, message_start)
