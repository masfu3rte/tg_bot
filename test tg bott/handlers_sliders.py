from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from config import Config
from db import Database
from keyboards import Keyboards
from states import DealTrack
from utils import build_request_link, build_direct_link

from handlers_offers_deals import DEAL_STATUS_STEPS


def setup_sliders_handlers(router: Router, db: Database, cfg: Config):
    # ===== Слайдер "Мои запросы" =====
    async def ensure_moderation_thread_id(offer_id: int, bot) -> int | None:
        offer = await db.get_offer(offer_id)
        if not offer:
            return None
        if offer.get("moderation_thread_id"):
            return offer["moderation_thread_id"]
        try:
            topic = await bot.create_forum_topic(
                chat_id=cfg.MODERATION_CHAT_ID,
                name=f"Сделка №{offer_id}",
            )
            await db.set_offer_moderation_thread_id(offer_id, topic.message_thread_id)
            return topic.message_thread_id
        except Exception:
            return None

    async def send_request_card(msg_obj, user_id: int, req: dict):
        index, total = await db.get_active_request_index_and_total(user_id, req["id"])

        # Новый шаблон
        text_lines = [
            f"Заявка №{req['id']}",
            f"Личное название: {req['internal_title']}",
            f"Название предмета: {req['item_name']}",
            "",
            f"Описание:\n{req['description']}",
        ]

        deal = await db.get_accepted_offer_for_request(req["id"])
        if deal:
            base_price = deal["price_cents"] / 100.0
            text_lines.append("")
            text_lines.append("Одобренный отклик:")
            text_lines.append(f"• Цена: {base_price:.2f}₽")
            text_lines.append(f"• Срок доставки до менеджера: {deal['days']} дн.")

        text = "\n".join(text_lines)
        kb = Keyboards.user_requests_slider_kb(req["id"], index, total)

        if req.get("photo_file_id"):
            await msg_obj.answer_photo(photo=req["photo_file_id"], caption=text, reply_markup=kb)
        else:
            await msg_obj.answer(text, reply_markup=kb)

    @router.message(F.text == "Активные запросы")
    async def requests_slider_start(msg: Message):
        user_id = msg.from_user.id
        req = await db.get_first_active_request(user_id)
        if not req:
            await msg.answer("Активных запросов нет.")
            return
        await send_request_card(msg, user_id, req)

    @router.callback_query(F.data.startswith("ur:prev:"))
    async def req_prev(cq: CallbackQuery):
        req_id = int(cq.data.split(":")[-1])
        current = await db.get_request(req_id)
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
        current = await db.get_request(req_id)
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
        # Заготовка под редактирование (по желанию докрутим полноценный FSM)
        req_id = int(cq.data.split(":")[-1])
        req = await db.get_request(req_id)
        if not req:
            await cq.answer("Заявка не найдена.", show_alert=True)
            return
        if req["user_id"] != cq.from_user.id:
            await cq.answer("Это не ваша заявка.", show_alert=True)
            return
        await cq.message.answer("Редактирование пока в упрощённом виде. Скажи, какие поля меняем — добавлю FSM.")

        await cq.answer()

    @router.callback_query(F.data.startswith("ur:del:"))
    async def req_delete(cq: CallbackQuery):
        req_id = int(cq.data.split(":")[-1])
        req = await db.get_request(req_id)
        if not req:
            await cq.answer("Заявка не найдена.", show_alert=True)
            return
        if req["user_id"] != cq.from_user.id:
            await cq.answer("Это не ваша заявка.", show_alert=True)
            return
        if await db.has_deal_for_request(req_id):
            await cq.answer("Нельзя удалить заявку с активной сделкой.", show_alert=True)
            return

        await db.set_request_status(req_id, "deleted")

        # Удаляем пост из канала
        if req.get("channel_message_id"):
            try:
                await cq.bot.delete_message(
                    chat_id=cfg.REQUESTS_PUBLIC_CHANNEL_ID,
                    message_id=req["channel_message_id"],
                )
            except Exception:
                pass

        try:
            await cq.message.delete()
        except Exception:
            pass

        await cq.answer("Заявка удалена.")

    # ===== «Мои отклики» (с баннером id=5) =====

    @router.message(F.text == "📮 Мои отклики")
    async def my_offers_entry(msg: Message):
        text = "Раздел «Мои отклики»."
        if cfg.ASSETS_CHANNEL_ID and cfg.MY_OFFERS_BANNER_MESSAGE_ID:
            try:
                await msg.bot.copy_message(
                    chat_id=msg.chat.id,
                    from_chat_id=cfg.ASSETS_CHANNEL_ID,
                    message_id=cfg.MY_OFFERS_BANNER_MESSAGE_ID,
                    caption=text,
                    reply_markup=Keyboards.my_offers_menu(),
                )
                return
            except Exception:
                pass
        await msg.answer(text, reply_markup=Keyboards.my_offers_menu())

    # ===== Слайдер «Активные отклики» =====

    async def send_offer_card(msg_obj, user_id: int, offer: dict):
        index, total = await db.get_offer_index_and_total(user_id, offer["id"])
        req = await db.get_request(offer["request_id"]) or {}
        link = build_request_link(cfg, req) or ""
        deal_line = f'<a href="{link}">Сделка №{offer["id"]}</a>' if link else f"Сделка №{offer['id']}"

        base_price = offer["price_cents"] / 100.0
        status_idx = max(0, min(len(DEAL_STATUS_STEPS) - 1, offer["deal_status"]))
        status_name = DEAL_STATUS_STEPS[status_idx]

        text_lines = [
            deal_line,
            f"Заявка №{offer['request_id']}",
            "",
            f"Цена: {base_price:.2f} руб.",
            f"Срок доставки: {offer['days']} дн.",
            f"Состояние: {offer['condition']}/10",
            "",
            f"Статус сделки: {status_name}",
            f"Статус залога покупателя: {offer['buyer_deposit_status']}",
            f"Статус залога продавца: {offer['seller_deposit_status']}",
        ]
        if offer.get("track_number"):
            text_lines.append(f"Трек-номер: {offer['track_number']} ({offer['track_status']})")

        text = "\n".join(text_lines)
        kb = Keyboards.user_offers_slider_kb(offer["id"], index, total)

        if offer.get("photo_file_id"):
            await msg_obj.answer_photo(photo=offer["photo_file_id"], caption=text, reply_markup=kb)
        else:
            await msg_obj.answer(text, reply_markup=kb)

    @router.message(F.text == "Активные отклики")
    async def offers_slider_start(msg: Message):
        user_id = msg.from_user.id
        offer = await db.get_first_offer_for_user(user_id)
        if not offer:
            await msg.answer("Откликов нет.")
            return
        await send_offer_card(msg, user_id, offer)

    @router.callback_query(F.data.startswith("uo:prev:"))
    async def offer_prev(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
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
        offer = await db.get_offer(offer_id)
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

    @router.callback_query(F.data.startswith("deal:status_menu:"))
    async def deal_status_menu(cq: CallbackQuery, state: FSMContext):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return
        if offer["buyer_id"] != cq.from_user.id:
            await cq.answer("Менять статус может только продавец.", show_alert=True)
            return
        idx = max(1, offer["deal_status"] or 1)
        total = len(DEAL_STATUS_STEPS)
        status_name = DEAL_STATUS_STEPS[idx - 1]

        req = await db.get_request(offer["request_id"]) or {}
        link = build_request_link(cfg, req) or f"Сделка №{offer_id}"

        text = (
            f"{link}\n"
            f"Статус сделки №{offer_id}.\n"
            f"Выбранный статус: {status_name}\n\n"
            "Нажмите «Выбрать этот статус» для сохранения."
        )
        await cq.message.answer(
            text,
            reply_markup=Keyboards.deal_status_menu_kb(offer_id, idx, total),
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("deal:status_prev:"))
    async def deal_status_prev(cq: CallbackQuery):
        _, _, offer_s, idx_s = cq.data.split(":")
        offer_id = int(offer_s)
        idx = int(idx_s)
        idx = idx - 1 if idx > 1 else len(DEAL_STATUS_STEPS)
        total = len(DEAL_STATUS_STEPS)
        status_name = DEAL_STATUS_STEPS[idx - 1]
        text = (
            f"Статус сделки №{offer_id}.\n"
            f"Выбранный статус: {status_name}\n\n"
            "Нажмите «Выбрать этот статус» для сохранения."
        )
        await cq.message.edit_text(
            text,
            reply_markup=Keyboards.deal_status_menu_kb(offer_id, idx, total),
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("deal:status_next:"))
    async def deal_status_next(cq: CallbackQuery):
        _, _, offer_s, idx_s = cq.data.split(":")
        offer_id = int(offer_s)
        idx = int(idx_s)
        idx = idx + 1 if idx < len(DEAL_STATUS_STEPS) else 1
        total = len(DEAL_STATUS_STEPS)
        status_name = DEAL_STATUS_STEPS[idx - 1]
        text = (
            f"Статус сделки №{offer_id}.\n"
            f"Выбранный статус: {status_name}\n\n"
            "Нажмите «Выбрать этот статус» для сохранения."
        )
        await cq.message.edit_text(
            text,
            reply_markup=Keyboards.deal_status_menu_kb(offer_id, idx, total),
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("deal:status_set:"))
    async def deal_status_set(cq: CallbackQuery, state: FSMContext):
        _, _, offer_s, idx_s = cq.data.split(":")
        offer_id = int(offer_s)
        idx = int(idx_s)
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return
        if offer["buyer_id"] != cq.from_user.id:
            await cq.answer("Менять статус может только продавец.", show_alert=True)
            return

        await db.set_deal_status(offer_id, idx)
        status_name = DEAL_STATUS_STEPS[idx - 1]

        seller_id = offer["buyer_id"]
        req = await db.get_request(offer["request_id"]) or {}
        buyer_id = req.get("user_id")

        if idx == 5:
            try:
                await cq.bot.send_message(
                    chat_id=seller_id,
                    text=cfg.MANAGER_CDEK_CONTACT_TEXT,
                )
            except Exception:
                pass

        if idx == 6:
            await state.set_state(DealTrack.waiting_for_track)
            await state.update_data(offer_id=offer_id)
            try:
                await cq.bot.send_message(
                    chat_id=seller_id,
                    text=(
                        "Статус обновлён на «Товар в пути до менеджера».\n"
                        "Отправьте, пожалуйста, трек-номер заказа для менеджера "
                        "одним сообщением в ответ на это сообщение."
                    ),
                )
            except Exception:
                pass

            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=cfg.MODERATION_TOPIC_ID,
                text=f"Продавец поставил статус «Товар в пути до менеджера» по сделке №{offer_id}.",
            )
            moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
            if moderation_thread_id and moderation_thread_id != cfg.MODERATION_TOPIC_ID:
                try:
                    await cq.bot.send_message(
                        chat_id=cfg.MODERATION_CHAT_ID,
                        message_thread_id=moderation_thread_id,
                        text=(
                            "Продавец поставил статус «Товар в пути до менеджера» "
                            f"по сделке №{offer_id}."
                        ),
                    )
                except Exception:
                    pass

        if buyer_id:
            try:
                await cq.bot.send_message(
                    chat_id=buyer_id,
                    text=f"Статус вашей сделки №{offer_id} изменён на: {status_name}.",
                )
            except Exception:
                pass

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Статус обновлён.")
