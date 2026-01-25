from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from config import Config
from db import Database
from keyboards import Keyboards
from states import OfferCreate, DealTrack, DealArrivalPhoto, DealManagerTrack
from utils import safe_username, build_request_link


DEAL_STATUS_STEPS = [
    "Внесены залоги",
    "Товар выкуплен",
    "Товар на складе в другой стране",
    "Товар отправлен в Россию",
    "Товар у продавца",
    "Товар в пути до менеджера",
]


def setup_offers_deals_handlers(router: Router, db: Database, cfg: Config):
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
    # ===== FSM отклика =====

    @router.message(OfferCreate.waiting_for_price)
    async def offer_price(msg: Message, state: FSMContext):
        text = (msg.text or "").replace(",", ".").strip()
        try:
            price = float(text)
        except ValueError:
            await msg.answer("Введите только число — цену товара в рублях.")
            return
        if price <= 0:
            await msg.answer("Цена должна быть больше нуля.")
            return

        await state.update_data(price=price)
        await state.set_state(OfferCreate.waiting_for_days)
        await msg.answer("Сколько дней будет занимать доставка? (только число)")

    @router.message(OfferCreate.waiting_for_days)
    async def offer_days(msg: Message, state: FSMContext):
        try:
            days = int((msg.text or "").strip())
        except ValueError:
            await msg.answer("Введите количество дней доставки цифрой.")
            return
        if days <= 0:
            await msg.answer("Количество дней должно быть больше нуля.")
            return

        await state.update_data(days=days)
        await state.set_state(OfferCreate.waiting_for_condition)
        await msg.answer(
            "Укажите состояние вещи по шкале от 1 до 10.",
            reply_markup=Keyboards.condition_inline_kb(),
        )

    @router.message(OfferCreate.waiting_for_condition)
    async def offer_condition_text(msg: Message, state: FSMContext):
        try:
            condition = int((msg.text or "").strip())
        except ValueError:
            await msg.answer("Введите целое число от 1 до 10 или нажмите кнопку.")
            return
        if not (1 <= condition <= 10):
            await msg.answer("Число должно быть от 1 до 10.")
            return

        await state.update_data(condition=condition)
        await state.set_state(OfferCreate.waiting_for_photo)
        await msg.answer("Пришлите фотографию товара одним сообщением.")

    @router.callback_query(F.data.startswith("offer:cond:"))
    async def offer_condition_btn(cq: CallbackQuery, state: FSMContext):
        _, _, val = cq.data.split(":")
        condition = int(val)
        if not (1 <= condition <= 10):
            await cq.answer("Неверное значение.", show_alert=True)
            return
        await state.update_data(condition=condition)
        await state.set_state(OfferCreate.waiting_for_photo)
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cq.message.answer("Пришлите фотографию товара одним сообщением.")
        await cq.answer()

    @router.message(OfferCreate.waiting_for_photo, F.photo)
    async def offer_photo(msg: Message, state: FSMContext):
        photo = msg.photo[-1]
        data = await state.get_data()
        await state.clear()

        request_id = data.get("request_id")
        price = float(data.get("price"))
        days = int(data.get("days"))
        condition = int(data.get("condition"))

        user = msg.from_user
        price_cents = int(round(price * 100))

        offer_id = await db.create_offer(
            request_id=request_id,
            buyer_id=user.id,
            price_cents=price_cents,
            days=days,
            condition=condition,
            photo_file_id=photo.file_id,
        )

        moderation_thread_id = await ensure_moderation_thread_id(offer_id, msg.bot)

        req = await db.get_request(request_id)
        link = build_request_link(cfg, req) if req else None
        if link:
            request_title = f'<a href="{link}">заявку №{request_id}</a>'
        else:
            request_title = f"заявку №{request_id}"

        text = (
            f"Новый отклик #{offer_id} на {request_title}\n"
            f"От: {safe_username(user.username, user.id)} (id {user.id})\n\n"
            f"Цена: {price:.2f} руб.\n"
            f"Срок доставки: {days} дн.\n"
            f"Состояние вещи: {condition}/10"
        )

        if moderation_thread_id:
            await msg.bot.send_photo(
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                photo=photo.file_id,
                caption=text,
                reply_markup=Keyboards.offer_moderation_kb(offer_id),
            )
        else:
            await msg.bot.send_photo(
                chat_id=cfg.MODERATION_CHAT_ID,
                photo=photo.file_id,
                caption=(
                    "⚠️ Не удалось создать отдельный топик для сделки.\n\n"
                    f"{text}"
                ),
                reply_markup=Keyboards.offer_moderation_kb(offer_id),
            )

        await msg.answer(
            "Ваш отклик отправлен на модерацию. После принятия модератором "
            "вы получите сообщение с реквизитами для оплаты залога."
        )

    # ===== модерация откликов / создание сделки =====

    @router.callback_query(F.data.startswith("offer:approve:"))
    async def offer_approve(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Отклик не найден.", show_alert=True)
            return

        req = await db.get_request(offer["request_id"])
        if not req:
            await cq.answer("Заявка не найдена.", show_alert=True)
            return

        await db.set_offer_status(offer_id, "approved")

        buyer_id = req["user_id"]        # автор заявки (покупатель)
        seller_id = offer["buyer_id"]    # автор отклика (продавец)

        base_price = offer["price_cents"] / 100.0
        buyer_total = base_price * 1.07
        seller_deposit = base_price * 0.0535
        buyer_deposit = base_price * 0.2675

        deal_link = build_request_link(cfg, req) or ""
        if deal_link:
            deal_text = f'<a href="{deal_link}">Сделка №{offer_id}</a>'
        else:
            deal_text = f"Сделка №{offer_id}"

        block = (
            f"Сумма за которую продавец продает: {base_price:.2f} руб.\n"
            f"Сумма оплаты для покупателя: {buyer_total:.2f} руб.\n"
            f"Сумма залога для продавца: {seller_deposit:.2f} руб.\n"
            f"Сумма залога для покупателя: {buyer_deposit:.2f} руб."
        )

        buyer_text = (
            "✅ Ваш запрос получил одобренный отклик.\n\n"
            f"{deal_text}\n\n"
            f"Сумма товара: {base_price:.2f} руб.\n"
            f"Ваш залог (25%): {buyer_deposit:.2f} руб.\n\n"
            f"{block}\n\n"
            f"{cfg.MANAGER_REQUISITES_TEXT}\n\n"
            f"Укажите в комментарии «№{offer_id}».\n\n"
            "После оплаты нажмите кнопку «Оплатил»."
        )

        seller_text = (
            "✅ Ваш отклик одобрен, сделка создана.\n\n"
            f"{deal_text}\n\n"
            f"Сумма товара: {base_price:.2f} руб.\n"
            f"Ваш залог (5,35%): {seller_deposit:.2f} руб.\n\n"
            f"{block}\n\n"
            f"{cfg.MANAGER_REQUISITES_TEXT}\n\n"
            f"Укажите в комментарии «№{offer_id}».\n\n"
            "После оплаты нажмите кнопку «Оплатил»."
        )

        try:
            await cq.bot.send_message(
                chat_id=buyer_id,
                text=buyer_text,
                reply_markup=Keyboards.deal_paid_kb_for_side(offer_id, "buyer"),
            )
        except Exception:
            pass

        try:
            await cq.bot.send_message(
                chat_id=seller_id,
                text=seller_text,
                reply_markup=Keyboards.deal_paid_kb_for_side(offer_id, "seller"),
            )
        except Exception:
            pass

        # убираем "Откликнуться" у поста заявки
        if req.get("channel_message_id"):
            try:
                await cq.bot.edit_message_reply_markup(
                    chat_id=cfg.REQUESTS_PUBLIC_CHANNEL_ID,
                    message_id=req["channel_message_id"],
                    reply_markup=None,
                )
            except Exception:
                pass

        try:
            if cq.message.photo:
                await cq.message.edit_caption(
                    (cq.message.caption or "")
                    + "\n\n✅ Отклик одобрен, сделка создана.",
                    reply_markup=None,
                )
            else:
                await cq.message.edit_text(
                    (cq.message.text or "") + "\n\n✅ Отклик одобрен, сделка создана.",
                    reply_markup=None,
                )
        except Exception:
            pass

        await cq.answer("Отклик одобрен.")

    @router.callback_query(F.data.startswith("offer:reject:"))
    async def offer_reject(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Отклик не найден.", show_alert=True)
            return

        await db.set_offer_status(offer_id, "rejected")
        try:
            await cq.bot.send_message(
                chat_id=offer["buyer_id"],
                text=f"Ваш отклик #{offer_id} отклонён модератором.",
            )
        except Exception:
            pass

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Отклик отклонён.")

    # ===== оплата залогов =====

    @router.callback_query(F.data.startswith("deal:paid:"))
    async def deal_paid(cq: CallbackQuery):
        _, _, side, offer_id_s = cq.data.split(":")
        offer_id = int(offer_id_s)
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
        if side == "buyer":
            await db.set_buyer_deposit_status(offer_id, "pending")
        elif side == "seller":
            await db.set_seller_deposit_status(offer_id, "pending")

        text = (
            f"Поступил запрос проверки оплаты залога от стороны: {side}\n"
            f"Сделка #{offer_id}, заявка №{offer['request_id']}."
        )
        if moderation_thread_id:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                text=text,
                reply_markup=Keyboards.deal_payment_moderation_kb(offer_id, side),
            )
        else:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{text}",
                reply_markup=Keyboards.deal_payment_moderation_kb(offer_id, side),
            )
        await cq.answer("Запрос на проверку отправлен модератору.")

    @router.callback_query(F.data.startswith("deal:confirm:"))
    async def deal_payment_confirm(cq: CallbackQuery):
        _, _, side, offer_id_s = cq.data.split(":")
        offer_id = int(offer_id_s)
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        if side == "buyer":
            await db.set_buyer_deposit_status(offer_id, "confirmed")
            target_id = (await db.get_request(offer["request_id"]))["user_id"]
        elif side == "seller":
            await db.set_seller_deposit_status(offer_id, "confirmed")
            target_id = offer["buyer_id"]
        elif side == "final":
            await db.set_final_payment_status(offer_id, "confirmed")
            target_id = (await db.get_request(offer["request_id"]))["user_id"]
        else:
            await cq.answer("Неизвестная сторона.", show_alert=True)
            return

        try:
            await cq.bot.send_message(
                chat_id=target_id,
                text=(
                    f"Оплата {'остатка' if side == 'final' else 'залога'} "
                    f"({side}) по сделке №{offer_id} подтверждена."
                ),
            )
        except Exception:
            pass

        if side == "final":
            base_price = offer["price_cents"] / 100.0
            seller_deposit = base_price * 0.0535
            total_to_send = base_price + seller_deposit
            seller_req = await db.get_requisites(offer["buyer_id"]) or {}
            seller_req_text = (
                f"ФИО: {seller_req.get('fio') or '—'}\n"
                f"Карта: {seller_req.get('card') or '—'}\n"
                f"Банк: {seller_req.get('bank') or '—'}"
            )
            moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
            manager_text = (
                f"Финальная оплата подтверждена по сделке №{offer_id}.\n\n"
                f"Сумма к отправке продавцу (с залогом): {total_to_send:.2f} руб.\n\n"
                "Реквизиты продавца:\n"
                f"{seller_req_text}"
            )
            if moderation_thread_id:
                await cq.bot.send_message(
                    chat_id=cfg.MODERATION_CHAT_ID,
                    message_thread_id=moderation_thread_id,
                    text=manager_text,
                    reply_markup=Keyboards.deal_funds_sent_kb(offer_id),
                )
            else:
                await cq.bot.send_message(
                    chat_id=cfg.MODERATION_CHAT_ID,
                    text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{manager_text}",
                    reply_markup=Keyboards.deal_funds_sent_kb(offer_id),
                )

        offer = await db.get_offer(offer_id)
        if (
            offer["buyer_deposit_status"] == "confirmed"
            and offer["seller_deposit_status"] == "confirmed"
        ):
            await db.set_deal_status(offer_id, 1)

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Оплата подтверждена.")

    @router.callback_query(F.data.startswith("deal:cancel:"))
    async def deal_payment_cancel(cq: CallbackQuery):
        _, _, side, offer_id_s = cq.data.split(":")
        offer_id = int(offer_id_s)
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        if side == "buyer":
            await db.set_buyer_deposit_status(offer_id, "rejected")
            target_id = (await db.get_request(offer["request_id"]))["user_id"]
        elif side == "seller":
            await db.set_seller_deposit_status(offer_id, "rejected")
            target_id = offer["buyer_id"]
        elif side == "final":
            await db.set_final_payment_status(offer_id, "rejected")
            target_id = (await db.get_request(offer["request_id"]))["user_id"]
        else:
            await cq.answer("Неизвестная сторона.", show_alert=True)
            return

        try:
            await cq.bot.send_message(
                chat_id=target_id,
                text=(
                    f"Оплата {'остатка' if side == 'final' else 'залога'} "
                    f"({side}) по сделке №{offer_id} не прошла. "
                    "Проверьте данные и попробуйте ещё раз."
                ),
            )
        except Exception:
            pass

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Отметили как не прошедшую.")

    # ===== трек по статусу 6 =====

    @router.message(DealTrack.waiting_for_track)
    async def seller_track(msg: Message, state: FSMContext):
        data = await state.get_data()
        offer_id = data.get("offer_id")
        if not offer_id:
            await state.clear()
            await msg.answer("Что-то пошло не так, попробуйте ещё раз через меню статусов.")
            return

        track = (msg.text or "").strip()
        await db.set_track_info(offer_id, track, status="pending")
        await state.clear()

        offer = await db.get_offer(offer_id)
        if not offer:
            await msg.answer("Сделка не найдена.")
            return

        moderation_thread_id = await ensure_moderation_thread_id(offer_id, msg.bot)
        text = (
            f"Продавец указал трек-номер по сделке №{offer_id}:\n"
            f"{track}"
        )
        if moderation_thread_id:
            await msg.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                text=text,
                reply_markup=Keyboards.deal_track_moderation_kb(offer_id),
            )
        else:
            await msg.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{text}",
                reply_markup=Keyboards.deal_track_moderation_kb(offer_id),
            )
        await msg.answer("Трек-номер отправлен модератору на проверку.")

    @router.callback_query(F.data.startswith("deal:confirm:track:"))
    async def track_confirm(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await db.set_track_status(offer_id, "approved")
        seller_id = offer["buyer_id"]
        try:
            await cq.bot.send_message(
                chat_id=seller_id,
                text="Отлично! Менеджер ожидает прибытия товара.",
            )
        except Exception:
            pass

        moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
        arrival_text = (
            f"Трек-номер по сделке №{offer_id} одобрен.\n"
            "Проверьте товар и выберите действие."
        )
        if moderation_thread_id:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                text=arrival_text,
                reply_markup=Keyboards.deal_arrival_moderation_kb(offer_id),
            )
        else:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{arrival_text}",
                reply_markup=Keyboards.deal_arrival_moderation_kb(offer_id),
            )

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Трек подтверждён.")

    @router.callback_query(F.data.startswith("deal:cancel:track:"))
    async def track_reject(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await db.set_track_status(offer_id, "rejected")
        seller_id = offer["buyer_id"]
        try:
            await cq.bot.send_message(
                chat_id=seller_id,
                text="Трек-номер не принят. Пожалуйста, отправьте верный трек-код ещё раз.",
            )
        except Exception:
            pass

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Запросили новый трек.")

    # ===== получение товара менеджером =====

    @router.callback_query(F.data.startswith("deal:arrival_accept:"))
    async def deal_arrival_accept(cq: CallbackQuery, state: FSMContext):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await state.set_state(DealArrivalPhoto.waiting_for_photo)
        await state.update_data(offer_id=offer_id)
        await cq.message.answer(
            f"Пришлите фото товара по сделке №{offer_id} одним сообщением.",
        )
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cq.answer()

    @router.callback_query(F.data.startswith("deal:arrival_dispute:"))
    async def deal_arrival_dispute(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await db.set_offer_status(offer_id, "dispute")
        req = await db.get_request(offer["request_id"]) or {}
        buyer_id = req.get("user_id")
        seller_id = offer["buyer_id"]

        dispute_text = (
            f"По сделке №{offer_id} открыт спор. "
            f"Свяжитесь с поддержкой: @{cfg.SUPPORT_USERNAME}."
        )
        for target_id in (buyer_id, seller_id):
            if not target_id:
                continue
            try:
                await cq.bot.send_message(chat_id=target_id, text=dispute_text)
            except Exception:
                pass

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Спор открыт.")

    @router.message(DealArrivalPhoto.waiting_for_photo, F.photo)
    async def deal_arrival_photo(msg: Message, state: FSMContext):
        data = await state.get_data()
        offer_id = data.get("offer_id")
        if not offer_id:
            await state.clear()
            await msg.answer("Не удалось определить сделку. Попробуйте ещё раз.")
            return

        offer = await db.get_offer(offer_id)
        if not offer:
            await state.clear()
            await msg.answer("Сделка не найдена.")
            return

        req = await db.get_request(offer["request_id"]) or {}
        buyer_id = req.get("user_id")
        if not buyer_id:
            await state.clear()
            await msg.answer("Не удалось определить покупателя.")
            return

        photo = msg.photo[-1]
        caption = (
            f"Товар по сделке №{offer_id} прибыл.\n"
            "Проверьте соответствие сделки и выберите действие."
        )
        try:
            await msg.bot.send_photo(
                chat_id=buyer_id,
                photo=photo.file_id,
                caption=caption,
                reply_markup=Keyboards.deal_arrival_buyer_kb(offer_id),
            )
        except Exception:
            pass

        await state.clear()
        await msg.answer("Фото отправлено покупателю.")

    @router.callback_query(F.data.startswith("deal:buyer_accept:"))
    async def deal_buyer_accept(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        base_price = offer["price_cents"] / 100.0
        buyer_total = base_price * 1.07
        buyer_deposit = base_price * 0.2675
        remainder = max(buyer_total - buyer_deposit, 0)

        text = (
            f"✅ Вы подтвердили получение товара по сделке №{offer_id}.\n\n"
            f"Остаток к оплате: {remainder:.2f} руб.\n\n"
            f"{cfg.MANAGER_REQUISITES_TEXT}\n\n"
            f"Укажите в комментарии «№{offer_id}».\n\n"
            "После оплаты нажмите кнопку «Оплатил остаток»."
        )
        try:
            await cq.bot.send_message(
                chat_id=cq.from_user.id,
                text=text,
                reply_markup=Keyboards.deal_final_paid_kb(offer_id),
            )
        except Exception:
            pass

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Отправили реквизиты.")

    @router.callback_query(F.data.startswith("deal:buyer_dispute:"))
    async def deal_buyer_dispute(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await db.set_offer_status(offer_id, "dispute")
        moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
        dispute_text = (
            f"Покупатель открыл спор по сделке №{offer_id}.\n"
            f"Покупатель: {safe_username(cq.from_user.username, cq.from_user.id)}"
        )
        if moderation_thread_id:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                text=dispute_text,
            )
        else:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{dispute_text}",
            )

        try:
            await cq.bot.send_message(
                chat_id=cq.from_user.id,
                text=f"Спор по сделке №{offer_id} открыт. "
                     f"Свяжитесь с поддержкой: @{cfg.SUPPORT_USERNAME}.",
            )
        except Exception:
            pass

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Спор открыт.")

    # ===== финальная оплата покупателя =====

    @router.callback_query(F.data.startswith("deal:final_paid:"))
    async def deal_final_paid(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await db.set_final_payment_status(offer_id, "pending")
        moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
        text = f"Поступил запрос проверки финальной оплаты по сделке №{offer_id}."
        if moderation_thread_id:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                text=text,
                reply_markup=Keyboards.deal_payment_moderation_kb(offer_id, "final"),
            )
        else:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{text}",
                reply_markup=Keyboards.deal_payment_moderation_kb(offer_id, "final"),
            )

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cq.answer("Запрос отправлен модератору.")

    @router.callback_query(F.data.startswith("deal:funds_sent:"))
    async def deal_funds_sent(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        req = await db.get_request(offer["request_id"]) or {}
        buyer_id = req.get("user_id")
        seller_id = offer["buyer_id"]

        try:
            await cq.bot.send_message(
                chat_id=seller_id,
                text=f"Оплата за заказ №{offer_id} отправлена вместе с залогом по вашим реквизитам.",
            )
        except Exception:
            pass

        if buyer_id:
            try:
                await cq.bot.send_message(
                    chat_id=buyer_id,
                    text="Как хотите получить заказ?",
                    reply_markup=Keyboards.deal_delivery_choice_kb(offer_id),
                )
            except Exception:
                pass

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Отметили отправку.")

    @router.callback_query(F.data.startswith("deal:delivery:cdek:"))
    async def deal_delivery_cdek(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await db.set_delivery_method(offer_id, "cdek")
        cdek = await db.get_cdek_contacts(cq.from_user.id) or {}
        cdek_text = (
            f"Покупатель выбрал CDEK по сделке №{offer_id}.\n\n"
            f"ФИО: {cdek.get('fio') or '—'}\n"
            f"Телефон: {cdek.get('phone') or '—'}\n"
            f"ПВЗ: {cdek.get('pvz') or '—'}\n"
            f"Покупатель: {safe_username(cq.from_user.username, cq.from_user.id)}"
        )
        moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
        if moderation_thread_id:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                text=cdek_text,
                reply_markup=Keyboards.deal_cdek_sent_kb(offer_id),
            )
        else:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{cdek_text}",
                reply_markup=Keyboards.deal_cdek_sent_kb(offer_id),
            )

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cq.answer("Выбор сохранён.")

    @router.callback_query(F.data.startswith("deal:delivery:self:"))
    async def deal_delivery_self(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await db.set_delivery_method(offer_id, "self")
        moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
        info_text = (
            f"Покупатель выбрал самовывоз по сделке №{offer_id}.\n"
            f"Покупатель: {safe_username(cq.from_user.username, cq.from_user.id)}"
        )
        if moderation_thread_id:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                text=info_text,
            )
        else:
            await cq.bot.send_message(
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{info_text}",
            )

        try:
            await cq.bot.send_message(
                chat_id=cq.from_user.id,
                text="Скоро с вами свяжется менеджер для самовывоза.",
            )
        except Exception:
            pass

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cq.answer("Выбор сохранён.")

    @router.callback_query(F.data.startswith("deal:cdek_sent:"))
    async def deal_cdek_sent(cq: CallbackQuery, state: FSMContext):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await state.set_state(DealManagerTrack.waiting_for_track)
        await state.update_data(offer_id=offer_id)
        await cq.message.answer(
            f"Отправка по CDEK отмечена. Пришлите трек-код по сделке №{offer_id}.",
        )
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cq.answer()

    @router.message(DealManagerTrack.waiting_for_track)
    async def manager_track_send(msg: Message, state: FSMContext):
        data = await state.get_data()
        offer_id = data.get("offer_id")
        if not offer_id:
            await state.clear()
            await msg.answer("Не удалось определить сделку.")
            return

        track = (msg.text or "").strip()
        if not track:
            await msg.answer("Отправьте трек-код текстом.")
            return

        await db.set_manager_track_number(offer_id, track)
        offer = await db.get_offer(offer_id)
        req = await db.get_request(offer["request_id"]) or {}
        buyer_id = req.get("user_id")
        if buyer_id:
            try:
                await msg.bot.send_message(
                    chat_id=buyer_id,
                    text=f"Ваш трек-код по сделке №{offer_id}: {track}",
                )
            except Exception:
                pass

        await state.clear()
        await msg.answer("Трек-код отправлен покупателю.")

    # ===== закрытие сделки продавцом =====

    @router.callback_query(F.data.startswith("deal:close_prompt:"))
    async def deal_close_prompt(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        text = (
            "Вы уверены, что готовы отказаться от сделки? В таком случае ваш залог "
            "будет передан в пользу покупателя."
        )
        await cq.message.answer(text, reply_markup=Keyboards.deal_close_confirm_kb(offer_id))
        await cq.answer()

    @router.callback_query(F.data.startswith("deal:close_back:"))
    async def deal_close_back(cq: CallbackQuery):
        try:
            await cq.message.delete()
        except Exception:
            pass
        await cq.answer()

    @router.callback_query(F.data.startswith("deal:close:"))
    async def deal_close(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = await db.get_offer(offer_id)
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        seller_id = offer["buyer_id"]
        if cq.from_user.id != seller_id:
            await cq.answer("Отказаться от сделки может только продавец.", show_alert=True)
            return

        await db.set_offer_status(offer_id, "closed_by_seller")
        req = await db.get_request(offer["request_id"])
        buyer_id = req["user_id"]

        try:
            await cq.bot.send_message(
                chat_id=buyer_id,
                text=f"Продавец отменил сделку №{offer_id}.",
            )
        except Exception:
            pass

        try:
            await cq.bot.send_message(
                chat_id=seller_id,
                text=f"Вы отменили сделку №{offer_id}. Залог будет передан покупателю.",
            )
        except Exception:
            pass

        await cq.answer("Сделка закрыта.")
