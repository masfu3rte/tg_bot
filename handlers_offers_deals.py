from delivery import notify
from access import secure_messages
from utils import escape_record
import asyncio
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InputMediaPhoto
from aiogram.fsm.context import FSMContext

from config import Config
from db import Database
from keyboards import Keyboards
from states import OfferCreate, DealTrack, DealArrivalPhoto, DealManagerTrack
from utils import build_request_link, work_chat_user
from access import secure_callbacks
from delivery import content, enqueue, queue_batch
from money import parse_price, amounts, rubles
from decimal import Decimal
from utils import escape_text
import logging

logger = logging.getLogger(__name__)


DEAL_STATUS_STEPS = [
    "Внесены залоги",
    "Товар выкуплен",
    "Товар на складе в другой стране",
    "Товар отправлен в Россию",
    "Товар у продавца",
    "Товар в пути до менеджера",
]


def setup_offers_deals_handlers(router: Router, db: Database, cfg: Config):
    message_start = len(router.message.handlers)
    callback_start = len(router.callback_query.handlers)
    deal_arrival_album_buffers: dict[tuple[int, str], dict] = {}
    deal_arrival_album_tasks: dict[tuple[int, str], asyncio.Task] = {}

    async def flush_deal_arrival_album(key):
        await asyncio.sleep(0.8)
        payload = deal_arrival_album_buffers.pop(key, None)
        deal_arrival_album_tasks.pop(key, None)
        if not payload:
            return
        photos = payload["photos"][:10]
        buyer = payload["buyer_id"]
        oid = payload["offer_id"]
        rid = payload["request_id"]
        jobs = []
        if len(photos) > 1:
            jobs = [
                dict(
                    method="send_media_group",
                    kwargs=dict(
                        chat_id=buyer, media=[InputMediaPhoto(media=f) for f in photos]
                    ),
                )
            ]
        else:
            jobs = content(buyer, "Фото товара", photo=photos[0])
        jobs += content(
            buyer,
            f"Товар по сделке №{rid} прибыл. Проверьте фото.",
            reply_markup=Keyboards.deal_arrival_buyer_kb(oid),
            effect={"kind": "arrival", "id": oid},
        )
        delivered = await queue_batch(
            db, payload["bot"], f"arrival-album-{oid}-{key[1]}", jobs
        )
        await payload["state"].clear()
        await payload["bot"].send_message(
            chat_id=payload["manager_chat_id"],
            text=(
                "Фото отправлены покупателю."
                if delivered
                else "Фото сохранены в очереди доставки покупателю."
            ),
        )

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

    def format_deposit_status(status: str) -> str:
        return "✅" if status == "confirmed" else "❌"

    def format_due_date(offer: dict) -> str:
        created_at = offer.get("created_at")
        if not created_at:
            return "дд.мм.гггг"
        try:
            parsed = datetime.fromisoformat(str(created_at).replace("Z", ""))
        except Exception:
            return "дд.мм.гггг"
        days = offer.get("days") or 0
        due = parsed + timedelta(days=days)
        return due.strftime("%d.%m.%Y")

    async def build_deal_moderation_intro(
        offer: dict,
        req: dict | None,
    ) -> str:
        request_id = offer["request_id"]
        buyer_id = req["user_id"] if req else 0
        seller_id = offer["buyer_id"]

        buyer_user = await db.get_user(buyer_id) if buyer_id else None
        seller_user = await db.get_user(seller_id)

        offers_count = await db.get_offers_count_for_request(request_id)

        buyer_requisites = (
            escape_record(await db.get_requisites(buyer_id)) if buyer_id else None
        )
        seller_requisites = escape_record(await db.get_requisites(seller_id))
        buyer_contacts = (
            escape_record(await db.get_cdek_contacts(buyer_id)) if buyer_id else None
        )

        item_name = req.get("item_name") if req else None
        description = req.get("description") if req else None

        money = amounts(offer["price_cents"])
        base_price = Decimal(money["base"]) / 100
        buyer_total = Decimal(money["total"]) / 100
        service_fee = Decimal(money["fee"]) / 100
        buyer_deposit = Decimal(money["buyer_deposit"]) / 100
        seller_deposit = Decimal(money["seller_deposit"]) / 100

        buyer_username = work_chat_user(
            buyer_user.get("username") if buyer_user else None,
            buyer_id,
        )
        seller_username = work_chat_user(
            seller_user.get("username") if seller_user else None,
            seller_id,
        )

        buyer_card = buyer_requisites.get("card") if buyer_requisites else None
        buyer_fio = buyer_requisites.get("fio") if buyer_requisites else None
        seller_card = seller_requisites.get("card") if seller_requisites else None
        seller_fio = seller_requisites.get("fio") if seller_requisites else None

        buyer_contact_fio = buyer_contacts.get("fio") if buyer_contacts else None
        buyer_contact_phone = buyer_contacts.get("phone") if buyer_contacts else None
        buyer_contact_pvz = buyer_contacts.get("pvz") if buyer_contacts else None

        status_map = {
            "pending": "ожидает модерации",
            "approved": "ожидает решения покупателя",
            "buyer_accepted": "ожидает оплату залогов",
            "buyer_rejected": "отклонено покупателем",
            "rejected": "отклонено модератором",
        }
        status_label = status_map.get(offer.get("status"), offer.get("status") or "")

        def fmt(value: str | None) -> str:
            return value or ""

        return (
            f"Заявка №{request_id}\n"
            f"• Название: {fmt(item_name)}\n"
            f"• Описание: {fmt(description)}\n\n"
            "Покупатель:\n"
            f"User: {buyer_username}\n\n"
            f"Количество откликов: {offers_count}\n\n"
            "Принятый отклик:\n"
            "Продавец:\n"
            f"User: {seller_username}\n\n"
            f"Общая сумма сделки: {buyer_total:.2f}₽\n"
            f"Сумма которую получает продавец: {base_price:.2f}₽\n"
            f"Наценка сервиса: {service_fee:.2f}₽\n"
            f"Залог покупателя: {buyer_deposit:.2f}₽\n"
            f"- Внесен: {format_deposit_status(offer.get('buyer_deposit_status'))}\n"
            f"Залог продавца: {seller_deposit:.2f}₽\n"
            f"- Внесен: {format_deposit_status(offer.get('seller_deposit_status'))}\n"
            f"Сроки до: {format_due_date(offer)}\n\n"
            "Реквизиты продавца\n"
            f"Номер карты: {fmt(seller_card)}\n"
            f"Фио: {fmt(seller_fio)}\n\n"
            "Реквизиты покупателя\n"
            f"Номер карты: {fmt(buyer_card)}\n"
            f"Фио: {fmt(buyer_fio)}\n\n"
            "Данные покупателя для отправки:\n"
            f"Фио: {fmt(buyer_contact_fio)}\n"
            f"Адрес пункта выдачи: {fmt(buyer_contact_pvz)}\n"
            f"Номер телефона: {fmt(buyer_contact_phone)}\n\n"
            f"Статус: {status_label}"
        )

    def build_deal_payment_texts(
        offer_id: int, offer: dict, req: dict
    ) -> tuple[str, str]:
        money = amounts(offer["price_cents"])
        buyer_total = Decimal(money["total"]) / 100
        seller_deposit = Decimal(money["seller_deposit"]) / 100
        buyer_deposit = Decimal(money["buyer_deposit"]) / 100

        deal_link = build_request_link(cfg, req) or ""
        if deal_link:
            deal_text = f'<a href="{deal_link}">Сделка №{offer['request_id']}</a>'
        else:
            deal_text = f"Сделка №{offer['request_id']}"

        buyer_requisites_text = cfg.MANAGER_REQUISITES_TEXT.replace(
            "Реквизиты менеджера для оплаты залога:",
            "<b>Реквизиты менеджера для оплаты залога:</b>",
            1,
        ).replace(":\n", ":\n\n", 1)
        buyer_text = (
            "✅ <b>Вы подтвердили отклик, можно переходить к оплате залогов.</b>\n\n"
            f"{deal_text}\n\n"
            f"Сумма товара: {buyer_total:.2f}₽.\n"
            f"Ваш залог (25%): {buyer_deposit:.2f}₽.\n\n"
            f"{buyer_requisites_text}\n\n"
            f"Укажите в комментарии к платежу «№{offer['request_id']}».\n\n"
            "После оплаты нажмите кнопку «Оплатил»."
        )

        seller_requisites_text = cfg.MANAGER_REQUISITES_TEXT.replace(":\n", ":\n\n", 1)
        seller_text = (
            "✅ Покупатель принял ваш отклик, можно переходить к оплате залога.\n\n"
            f"{deal_text}\n\n"
            f"Сумма сделки: {buyer_total:.2f}₽.\n"
            f"Ваш залог (5%): {seller_deposit:.2f}₽.\n\n"
            f"{seller_requisites_text}\n\n"
            f"Укажите в комментарии «№{offer['request_id']}».\n\n"
            "После оплаты нажмите кнопку «Оплатил»."
        )

        return buyer_text, seller_text

    # ===== FSM отклика =====

    @router.message(OfferCreate.waiting_for_price)
    async def offer_price(msg: Message, state: FSMContext):
        try:
            cents = parse_price(msg.text or "")
        except ValueError as exc:
            await msg.answer(str(exc))
            return
        await state.update_data(price_cents=cents)
        await state.set_state(OfferCreate.waiting_for_days)
        await msg.answer("Сколько дней займёт доставка? Введите число от 1 до 365.")

    @router.message(OfferCreate.waiting_for_days)
    async def offer_days(msg: Message, state: FSMContext):
        try:
            days = int((msg.text or "").strip())
            if not 1 <= days <= 365:
                raise ValueError()
        except ValueError:
            await msg.answer("Введите число дней от 1 до 365.")
            return
        await state.update_data(days=days)
        await state.set_state(OfferCreate.waiting_for_condition)
        await msg.answer(
            "Укажите состояние вещи от 1 до 10.",
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
        if await state.get_state() != OfferCreate.waiting_for_condition.state:
            await cq.answer("Кнопка устарела.", show_alert=True)
            return
        _, _, val = cq.data.split(":")
        condition = int(val)
        if not (1 <= condition <= 10):
            await cq.answer("Неверное значение.", show_alert=True)
            return
        await state.update_data(condition=condition)
        await state.set_state(OfferCreate.waiting_for_photo)
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        await cq.message.answer("Пришлите фотографию товара одним сообщением.")
        await cq.answer()

    @router.message(OfferCreate.waiting_for_photo, F.photo)
    async def offer_photo(msg: Message, state: FSMContext):
        data = await state.get_data()
        if not await db.is_offer_accepted(
            msg.from_user.id
        ) or not await db.has_full_profile(msg.from_user.id):
            await msg.answer(
                "Сначала примите условия и заполните профиль через /start."
            )
            return
        if not all(
            k in data for k in ("request_id", "price_cents", "days", "condition")
        ):
            await msg.answer("Форма устарела. Откройте заявку заново.")
            return
        offer_id = data.get("saved_offer_id")
        if not offer_id:
            try:
                offer_id = await db.create_offer(
                    data["request_id"],
                    msg.from_user.id,
                    data["price_cents"],
                    data["days"],
                    data["condition"],
                    msg.photo[-1].file_id,
                )
            except ValueError as exc:
                await msg.answer(str(exc))
                return
            await state.update_data(saved_offer_id=offer_id)
        thread = await ensure_moderation_thread_id(offer_id, msg.bot)
        offer = escape_record(await db.get_offer(offer_id))
        req = escape_record(await db.get_request(offer["request_id"]))
        text = await build_deal_moderation_intro(offer, req)
        jobs = content(
            cfg.MODERATION_CHAT_ID,
            text,
            photo=offer["photo_file_id"],
            message_thread_id=thread or cfg.MODERATION_TOPIC_ID,
            reply_markup=Keyboards.offer_moderation_kb(offer_id),
        )
        delivered = await queue_batch(db, msg.bot, f"new-offer-{offer_id}", jobs)
        await state.clear()
        await msg.answer(
            "Ваш отклик отправлен на модерацию."
            if delivered
            else "Отклик сохранён. Доставка модератору будет повторена автоматически."
        )

    # ===== модерация откликов / создание сделки =====

    @router.callback_query(F.data.startswith("offer:approve:"))
    async def offer_approve(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        req = escape_record(await db.get_request(offer["request_id"]))
        if not await db.transition_offer(offer_id, "approved", ("pending",)):
            await cq.answer("Отклик уже обработан.")
            return
        price = rubles(amounts(offer["price_cents"])["total"])
        buyer_text = f"✅ По заявке №{req['id']} одобрен отклик.\nЦена: {price} ₽\nДоставка: {offer['days']} дней\nСостояние: {offer['condition']}/10\nПринять отклик?"
        jobs = content(
            req["user_id"],
            buyer_text,
            reply_markup=Keyboards.offer_buyer_decision_kb(offer_id),
        )
        jobs += content(
            offer["buyer_id"],
            f"Отклик по заявке №{req['id']} одобрен. Ожидаем решения покупателя.",
        )
        delivered = await queue_batch(db, cq.bot, f"approved-offer-{offer_id}", jobs)
        try:
            if cq.message.photo or cq.message.caption is not None:
                base = cq.message.caption or await build_deal_moderation_intro(
                    offer, req
                )
                if len(base) < 850:
                    await cq.message.edit_caption(
                        caption=base
                        + "\n\n✅ Отклик одобрен, ожидаем решение покупателя.",
                        reply_markup=None,
                    )
                else:
                    await cq.message.edit_reply_markup(reply_markup=None)
            else:
                await cq.message.edit_text(
                    (cq.message.text or "")
                    + "\n\n✅ Отклик одобрен, ожидаем решение покупателя.",
                    reply_markup=None,
                )
        except Exception as exc:
            logger.warning("Could not refresh moderation card: %s", type(exc).__name__)
        await cq.answer(
            "Отклик одобрен."
            if delivered
            else "Отклик одобрен; уведомления в очереди доставки."
        )

    @router.callback_query(F.data.startswith("offer:buyer_accept:"))
    async def offer_buyer_accept(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        req = escape_record(await db.get_request(offer["request_id"]))
        buyer_text, seller_text = build_deal_payment_texts(offer_id, offer, req)
        jobs = content(
            req["user_id"],
            buyer_text,
            reply_markup=Keyboards.deal_paid_kb_for_side(offer_id, "buyer"),
        )
        jobs += content(
            offer["buyer_id"],
            seller_text,
            reply_markup=Keyboards.deal_paid_kb_for_side(offer_id, "seller"),
        )
        if not await db.transition_offer(offer_id, "buyer_accepted", ("approved",)):
            await cq.answer(
                "Отклик недоступен или уже выбрана другая сделка.", show_alert=True
            )
            return
        delivered = await queue_batch(db, cq.bot, f"accepted-offer-{offer_id}", jobs)
        if req.get("channel_message_id"):
            try:
                await cq.bot.edit_message_reply_markup(
                    chat_id=cfg.REQUESTS_PUBLIC_CHANNEL_ID,
                    message_id=req["channel_message_id"],
                    reply_markup=None,
                )
            except Exception as exc:
                logger.warning(
                    "Could not update public request %s: %s",
                    req["id"],
                    type(exc).__name__,
                )
        try:
            if cq.message.photo or cq.message.caption is not None:
                await cq.message.edit_caption(
                    caption=(cq.message.caption or "") + "\n\n✅ Вы приняли отклик.",
                    reply_markup=None,
                )
            else:
                await cq.message.edit_text(
                    (cq.message.text or "") + "\n\n✅ Вы приняли отклик.",
                    reply_markup=None,
                )
        except Exception as exc:
            logger.warning("Could not refresh acceptance card: %s", type(exc).__name__)
        await cq.answer(
            "Отклик принят."
            if delivered
            else "Отклик принят; реквизиты сохранены в очереди доставки."
        )

    @router.callback_query(F.data.startswith("offer:buyer_reject:"))
    async def offer_buyer_reject(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Отклик не найден.", show_alert=True)
            return

        req = escape_record(await db.get_request(offer["request_id"]))
        if not req:
            await cq.answer("Заявка не найдена.", show_alert=True)
            return

        if req["user_id"] != cq.from_user.id:
            await cq.answer("Нет доступа к этому отклику.", show_alert=True)
            return

        if offer["status"] in {"buyer_rejected", "rejected"}:
            await cq.answer("Отклик уже отклонён.", show_alert=True)
            return

        if not await db.transition_offer(offer_id, "buyer_rejected", ("approved",)):
            await cq.answer("Отклик уже обработан.")
            return

        try:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=offer["buyer_id"],
                text=f"Покупатель отклонил отклик по сделке №{offer['request_id']}.",
            )
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        if req.get("channel_message_id"):
            try:
                bot_me = await cq.bot.get_me()
                bot_link = f"https://t.me/{bot_me.username}?start=req_{req['id']}"
                await cq.bot.edit_message_reply_markup(
                    chat_id=cfg.REQUESTS_PUBLIC_CHANNEL_ID,
                    message_id=req["channel_message_id"],
                    reply_markup=Keyboards.request_public_kb(bot_link),
                )
            except Exception as exc:
                logger.warning(
                    "Optional Telegram operation failed: %s", type(exc).__name__
                )

        try:
            if cq.message.photo or cq.message.caption is not None:
                base_caption = cq.message.caption or ""
                await cq.message.edit_caption(
                    caption=base_caption + "\n\n❌ Вы отклонили отклик.",
                    reply_markup=None,
                )
            else:
                await cq.message.edit_text(
                    (cq.message.text or "") + "\n\n❌ Вы отклонили отклик.",
                    reply_markup=None,
                )
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        await cq.answer("Отклик отклонён.")

    @router.callback_query(F.data.startswith("offer:reject:"))
    async def offer_reject(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Отклик не найден.", show_alert=True)
            return

        if not await db.transition_offer(offer_id, "rejected", ("pending",)):
            await cq.answer("Отклик уже обработан.")
            return
        try:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=offer["buyer_id"],
                text=f"Ваш отклик #{offer_id} отклонён модератором.",
            )
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        await cq.answer("Отклик отклонён.")

    # ===== оплата залогов =====

    @router.callback_query(F.data.startswith("deal:paid:"))
    async def deal_paid(cq: CallbackQuery):
        _, _, side, offer_s = cq.data.split(":")[:4]
        offer_id = int(offer_s)
        if not await db.change_payment(offer_id, side, "pending"):
            await cq.answer(
                "Оплата уже подтверждена или проверка уже запрошена.", show_alert=True
            )
            return
        offer = escape_record(await db.get_offer(offer_id))
        money = amounts(offer["price_cents"])
        thread = await ensure_moderation_thread_id(offer_id, cq.bot)
        jobs = content(
            cfg.MODERATION_CHAT_ID,
            f"Проверка залога {side}, сделка №{offer['request_id']}.\nСумма: {rubles(money[side+'_deposit'])} ₽",
            message_thread_id=thread or cfg.MODERATION_TOPIC_ID,
            reply_markup=Keyboards.deal_payment_moderation_kb(
                offer_id, side, offer[side + "_payment_version"]
            ),
        )
        delivered = await queue_batch(
            db,
            cq.bot,
            f'payment-check-{offer_id}-{side}-{offer[side+"_payment_version"]}',
            jobs,
        )
        await cq.answer(
            "Запрос на проверку отправлен."
            if delivered
            else "Запрос сохранён в очереди доставки."
        )

    @router.callback_query(
        F.data.startswith("deal:confirm:buyer:")
        | F.data.startswith("deal:confirm:seller:")
        | F.data.startswith("deal:confirm:final:")
    )
    async def deal_payment_confirm(cq: CallbackQuery):
        _, _, side, offer_s = cq.data.split(":")[:4]
        offer_id = int(offer_s)
        if not await db.change_payment(offer_id, side, "confirmed"):
            await cq.answer(
                "Платёж уже обработан или ещё не запрошен.", show_alert=True
            )
            return
        offer = escape_record(await db.get_offer(offer_id))
        req = escape_record(await db.get_request(offer["request_id"]))
        target = req["user_id"] if side in ("buyer", "final") else offer["buyer_id"]
        jobs = content(
            target,
            f"Оплата {'остатка' if side=='final' else 'залога'} по сделке №{req['id']} подтверждена.",
        )
        if side == "final":
            money = amounts(offer["price_cents"])
            seller = escape_record(await db.get_requisites(offer["buyer_id"])) or {}
            text = (
                f"Финальная оплата подтверждена по сделке №{req['id']}.\n"
                f"К отправке продавцу с залогом: {rubles(money['base']+money['seller_deposit'])} ₽\n"
                f"ФИО: {escape_text(seller.get('fio') or '—')}\nКарта: {escape_text(seller.get('card') or '—')}\nБанк: {escape_text(seller.get('bank') or '—')}"
            )
            thread = await ensure_moderation_thread_id(offer_id, cq.bot)
            jobs += content(
                cfg.MODERATION_CHAT_ID,
                text,
                message_thread_id=thread or cfg.MODERATION_TOPIC_ID,
                reply_markup=Keyboards.deal_funds_sent_kb(offer_id),
            )
        elif (
            offer["buyer_deposit_status"]
            == offer["seller_deposit_status"]
            == "confirmed"
        ):
            jobs += content(
                req["user_id"],
                "Оба залога подтверждены. Следите за сделкой в активных запросах.",
            )
            jobs += content(
                offer["buyer_id"],
                "Оба залога подтверждены. Можно выкупать товар и обновлять этапы доставки.",
            )
        delivered = await queue_batch(
            db, cq.bot, f"payment-confirmed-{offer_id}-{side}", jobs
        )
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.answer(
            "Оплата подтверждена."
            if delivered
            else "Оплата подтверждена; уведомления в очереди доставки."
        )

    @router.callback_query(
        F.data.startswith("deal:cancel:buyer:")
        | F.data.startswith("deal:cancel:seller:")
        | F.data.startswith("deal:cancel:final:")
    )
    async def deal_payment_cancel(cq: CallbackQuery):
        _, _, side, offer_s = cq.data.split(":")[:4]
        offer_id = int(offer_s)
        if not await db.change_payment(offer_id, side, "rejected"):
            await cq.answer("Этот платёж уже обработан.", show_alert=True)
            return
        offer = escape_record(await db.get_offer(offer_id))
        req = escape_record(await db.get_request(offer["request_id"]))
        target = req["user_id"] if side in ("buyer", "final") else offer["buyer_id"]
        kb = (
            Keyboards.deal_final_paid_kb(offer_id)
            if side == "final"
            else Keyboards.deal_paid_kb_for_side(offer_id, side)
        )
        jobs = content(
            target,
            f"Оплата по сделке №{req['id']} не подтверждена. Проверьте перевод и повторите запрос.",
            reply_markup=kb,
        )
        await queue_batch(
            db,
            cq.bot,
            f'payment-rejected-{offer_id}-{side}-{offer[side+"_payment_version"]}',
            jobs,
        )
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.answer("Оплата не подтверждена.")

    # ===== трек по статусу 6 =====

    @router.message(DealTrack.waiting_for_track)
    async def seller_track(msg: Message, state: FSMContext):
        data = await state.get_data()
        offer_id = data.get("offer_id")
        if not offer_id:
            await state.clear()
            await msg.answer(
                "Что-то пошло не так, попробуйте ещё раз через меню статусов."
            )
            return

        track = (msg.text or "").strip()
        if not 3 <= len(track) <= 64 or not all(
            c.isalnum() or c in "- " for c in track
        ):
            await msg.answer(
                "Отправьте трек текстом: от 3 до 64 букв, цифр, пробелов или дефисов."
            )
            return
        await db.set_track_info(offer_id, track, status="pending")

        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await msg.answer("Сделка не найдена.")
            return

        moderation_thread_id = await ensure_moderation_thread_id(offer_id, msg.bot)
        text = (
            f"Продавец указал трек-номер по сделке №{offer['request_id']}:\n"
            f"{escape_text(track)}"
        )
        if moderation_thread_id:
            await notify(
                db,
                msg.bot,
                "send_message",
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                text=text,
                reply_markup=Keyboards.deal_track_moderation_kb(offer_id),
            )
        else:
            await notify(
                db,
                msg.bot,
                "send_message",
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{text}",
                reply_markup=Keyboards.deal_track_moderation_kb(offer_id),
            )
        await state.clear()
        await msg.answer("Трек-номер отправлен модератору на проверку.")

    @router.callback_query(F.data.startswith("deal:confirm:track:"))
    async def track_confirm(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await db.set_track_status(offer_id, "approved")
        seller_id = offer["buyer_id"]
        try:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=seller_id,
                text="Отлично! Менеджер ожидает прибытия товара.",
            )
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
        arrival_text = (
            f"Трек-номер по сделке №{offer['request_id']} одобрен.\n"
            "Проверьте товар и выберите действие."
        )
        if moderation_thread_id:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                text=arrival_text,
                reply_markup=Keyboards.deal_arrival_moderation_kb(offer_id),
            )
        else:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{arrival_text}",
                reply_markup=Keyboards.deal_arrival_moderation_kb(offer_id),
            )

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        await cq.answer("Трек подтверждён.")

    @router.callback_query(F.data.startswith("deal:cancel:track:"))
    async def track_reject(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await db.set_track_status(offer_id, "rejected")
        seller_id = offer["buyer_id"]
        try:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=seller_id,
                text="Трек-номер не принят. Пожалуйста, отправьте верный трек-код ещё раз.",
            )
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        await cq.answer("Запросили новый трек.")

    # ===== получение товара менеджером =====

    @router.callback_query(F.data.startswith("deal:arrival_accept:"))
    async def deal_arrival_accept(cq: CallbackQuery, state: FSMContext):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await state.set_state(DealArrivalPhoto.waiting_for_photo)
        await state.update_data(offer_id=offer_id)
        await cq.message.answer(
            f"Пришлите фото товара по сделке №{offer['request_id']} одним сообщением.",
        )
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        await cq.answer()

    @router.callback_query(F.data.startswith("deal:arrival_dispute:"))
    async def deal_arrival_dispute(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        if not await db.transition_offer(offer_id, "dispute", ("buyer_accepted",)):
            await cq.answer("Спор уже открыт или недоступен.")
            return
        req = escape_record(await db.get_request(offer["request_id"])) or {}
        buyer_id = req.get("user_id")
        seller_id = offer["buyer_id"]

        dispute_text = (
            f"По сделке №{offer['request_id']} открыт спор. "
            f"Свяжитесь с поддержкой: @{cfg.SUPPORT_USERNAME}."
        )
        for target_id in (buyer_id, seller_id):
            if not target_id:
                continue
            try:
                await notify(
                    db, cq.bot, "send_message", chat_id=target_id, text=dispute_text
                )
            except Exception as exc:
                logger.warning(
                    "Optional Telegram operation failed: %s", type(exc).__name__
                )

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        await cq.answer("Спор открыт.")

    @router.message(DealArrivalPhoto.waiting_for_photo, F.photo)
    async def deal_arrival_photo(msg: Message, state: FSMContext):
        data = await state.get_data()
        offer_id = data.get("offer_id")
        if not offer_id:
            await state.clear()
            await msg.answer("Не удалось определить сделку. Попробуйте ещё раз.")
            return

        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await state.clear()
            await msg.answer("Сделка не найдена.")
            return

        req = escape_record(await db.get_request(offer["request_id"])) or {}
        buyer_id = req.get("user_id")
        if not buyer_id:
            await state.clear()
            await msg.answer("Не удалось определить покупателя.")
            return

        photo = msg.photo[-1]
        media_group_id = msg.media_group_id

        if media_group_id:
            key = (msg.chat.id, media_group_id)
            payload = deal_arrival_album_buffers.get(key)
            if not payload:
                payload = {
                    "bot": msg.bot,
                    "buyer_id": buyer_id,
                    "offer_id": offer_id,
                    "request_id": offer["request_id"],
                    "manager_chat_id": msg.chat.id,
                    "state": state,
                    "photos": [],
                }
                deal_arrival_album_buffers[key] = payload

            payload["photos"].append(photo.file_id)

            if key not in deal_arrival_album_tasks:
                deal_arrival_album_tasks[key] = asyncio.create_task(
                    flush_deal_arrival_album(key)
                )
            return

        caption = (
            f"Товар по сделке №{offer['request_id']} прибыл.\n"
            "Проверьте соответствие сделки и выберите действие."
        )
        jobs = content(
            buyer_id,
            caption,
            photo=photo.file_id,
            reply_markup=Keyboards.deal_arrival_buyer_kb(offer_id),
            effect={"kind": "arrival", "id": offer_id},
        )
        delivered = await queue_batch(
            db, msg.bot, f"arrival-photo-{offer_id}-{msg.message_id}", jobs
        )
        await state.clear()
        await msg.answer(
            "Фото отправлено покупателю."
            if delivered
            else "Фото сохранено в очереди доставки покупателю."
        )

    @router.callback_query(F.data.startswith("deal:buyer_accept:"))
    async def deal_buyer_accept(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        money = amounts(offer["price_cents"])
        buyer_total = Decimal(money["total"]) / 100
        buyer_deposit = Decimal(money["buyer_deposit"]) / 100
        remainder = max(buyer_total - buyer_deposit, 0)

        text = (
            f"✅ Вы подтвердили получение товара по сделке №{offer['request_id']}.\n\n"
            f"Остаток к оплате: {remainder:.2f} руб.\n\n"
            f"{cfg.MANAGER_REQUISITES_TEXT}\n\n"
            f"Укажите в комментарии «№{offer['request_id']}».\n\n"
            "После оплаты нажмите кнопку «Оплатил остаток»."
        )
        jobs = content(
            cq.from_user.id, text, reply_markup=Keyboards.deal_final_paid_kb(offer_id)
        )
        with db.conn:
            db.conn.execute(
                "UPDATE offers SET buyer_received=1 WHERE id=?", (offer_id,)
            )
            enqueue(db, f"buyer-received-{offer_id}", jobs)
        delivered = await queue_batch(db, cq.bot, f"buyer-received-{offer_id}", jobs)

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        await cq.answer(
            "Отправили реквизиты."
            if delivered
            else "Реквизиты сохранены в очереди доставки."
        )

    @router.callback_query(F.data.startswith("deal:buyer_dispute:"))
    async def deal_buyer_dispute(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        if not await db.transition_offer(offer_id, "dispute", ("buyer_accepted",)):
            await cq.answer("Спор уже открыт или недоступен.")
            return
        moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
        dispute_text = (
            f"Покупатель открыл спор по сделке №{offer['request_id']}.\n"
            f"Покупатель: {work_chat_user(cq.from_user.username, cq.from_user.id)}"
        )
        if moderation_thread_id:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                text=dispute_text,
            )
        else:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{dispute_text}",
            )

        try:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=cq.from_user.id,
                text=f"Спор по сделке №{offer['request_id']} открыт. "
                f"Свяжитесь с поддержкой: @{cfg.SUPPORT_USERNAME}.",
            )
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        await cq.answer("Спор открыт.")

    # ===== финальная оплата покупателя =====

    @router.callback_query(F.data.startswith("deal:final_paid:"))
    async def deal_final_paid(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        if not await db.change_payment(offer_id, "final", "pending"):
            await cq.answer(
                "Финальная оплата уже обработана или ещё недоступна.", show_alert=True
            )
            return
        offer = escape_record(await db.get_offer(offer_id))
        thread = await ensure_moderation_thread_id(offer_id, cq.bot)
        jobs = content(
            cfg.MODERATION_CHAT_ID,
            f"Проверка финальной оплаты по сделке №{offer['request_id']}. Сумма: {rubles(amounts(offer['price_cents'])['remainder'])} ₽",
            message_thread_id=thread or cfg.MODERATION_TOPIC_ID,
            reply_markup=Keyboards.deal_payment_moderation_kb(
                offer_id, "final", offer["final_payment_version"]
            ),
        )
        delivered = await queue_batch(
            db,
            cq.bot,
            f'payment-check-{offer_id}-final-{offer["final_payment_version"]}',
            jobs,
        )
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.answer(
            "Запрос отправлен модератору."
            if delivered
            else "Запрос сохранён в очереди доставки."
        )

    @router.callback_query(F.data.startswith("deal:funds_sent:"))
    async def deal_funds_sent(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await db.set_workflow_flag(offer_id, "funds_sent")
        req = escape_record(await db.get_request(offer["request_id"])) or {}
        buyer_id = req.get("user_id")
        seller_id = offer["buyer_id"]

        try:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=seller_id,
                text=f"Оплата за заказ №{offer['request_id']} отправлена вместе с залогом по вашим реквизитам.",
            )
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        if buyer_id:
            try:
                await notify(
                    db,
                    cq.bot,
                    "send_message",
                    chat_id=buyer_id,
                    text="Как хотите получить заказ?",
                    reply_markup=Keyboards.deal_delivery_choice_kb(offer_id),
                )
            except Exception as exc:
                logger.warning(
                    "Optional Telegram operation failed: %s", type(exc).__name__
                )
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        await cq.answer("Отметили отправку.")

    @router.callback_query(F.data.startswith("deal:rate:"))
    async def deal_rate_seller(cq: CallbackQuery):
        _, _, offer_id_s, rating_s = cq.data.split(":")
        offer_id = int(offer_id_s)
        rating = int(rating_s)
        if rating < 1 or rating > 5:
            await cq.answer("Некорректная оценка.", show_alert=True)
            return

        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        req = escape_record(await db.get_request(offer["request_id"])) or {}
        buyer_id = req.get("user_id")
        seller_id = offer.get("buyer_id")
        if cq.from_user.id != buyer_id:
            await cq.answer("Оценить может только покупатель.", show_alert=True)
            return
        if offer.get("seller_rating") is not None:
            await cq.answer("Оценка уже сохранена.", show_alert=True)
            return

        await db.set_offer_seller_rating(offer_id, rating)
        if seller_id:
            await db.add_user_rating(seller_id, rating)
            try:
                await notify(
                    db,
                    cq.bot,
                    "send_message",
                    chat_id=seller_id,
                    text=f"Покупатель оценил вас на {rating}⭐ по сделке №{offer['request_id']}.",
                )
            except Exception as exc:
                logger.warning(
                    "Optional Telegram operation failed: %s", type(exc).__name__
                )

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        await cq.answer("Спасибо за оценку!")

    @router.callback_query(F.data.startswith("deal:delivery:cdek:"))
    async def deal_delivery_cdek(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await db.set_delivery_method(offer_id, "cdek")
        cdek = escape_record(await db.get_cdek_contacts(cq.from_user.id)) or {}
        cdek_text = (
            f"Покупатель выбрал CDEK по сделке №{offer['request_id']}.\n\n"
            f"ФИО: {cdek.get('fio') or '—'}\n"
            f"Телефон: {cdek.get('phone') or '—'}\n"
            f"ПВЗ: {cdek.get('pvz') or '—'}\n"
            f"Покупатель: {work_chat_user(cq.from_user.username, cq.from_user.id)}"
        )
        moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
        if moderation_thread_id:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                text=cdek_text,
                reply_markup=Keyboards.deal_cdek_sent_kb(offer_id),
            )
        else:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{cdek_text}",
                reply_markup=Keyboards.deal_cdek_sent_kb(offer_id),
            )

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        await cq.answer("Выбор сохранён.")

    @router.callback_query(F.data.startswith("deal:delivery:self:"))
    async def deal_delivery_self(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await db.set_delivery_method(offer_id, "self")
        moderation_thread_id = await ensure_moderation_thread_id(offer_id, cq.bot)
        info_text = (
            f"Покупатель выбрал самовывоз по сделке №{offer['request_id']}.\n"
            f"Покупатель: {work_chat_user(cq.from_user.username, cq.from_user.id)}"
        )
        if moderation_thread_id:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=cfg.MODERATION_CHAT_ID,
                message_thread_id=moderation_thread_id,
                text=info_text,
            )
        else:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=cfg.MODERATION_CHAT_ID,
                text=f"⚠️ Не удалось создать отдельный топик для сделки.\n\n{info_text}",
            )

        try:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=cq.from_user.id,
                text="Скоро с вами свяжется менеджер для самовывоза.",
            )
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        await cq.answer("Выбор сохранён.")

    @router.callback_query(F.data.startswith("deal:cdek_sent:"))
    async def deal_cdek_sent(cq: CallbackQuery, state: FSMContext):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        await state.set_state(DealManagerTrack.waiting_for_track)
        await state.update_data(offer_id=offer_id)
        await cq.message.answer(
            f"Отправка по CDEK отмечена. Пришлите трек-код по сделке №{offer['request_id']}.",
        )
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
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
        if not 3 <= len(track) <= 64 or not all(
            c.isalnum() or c in "- " for c in track
        ):
            await msg.answer("Отправьте трек-код текстом.")
            return

        await db.set_manager_track_number(offer_id, track)
        offer = escape_record(await db.get_offer(offer_id))
        req = escape_record(await db.get_request(offer["request_id"])) or {}
        buyer_id = req.get("user_id")
        if buyer_id:
            try:
                await notify(
                    db,
                    msg.bot,
                    "send_message",
                    chat_id=buyer_id,
                    text=f"Ваш трек-код по сделке №{offer['request_id']}: {escape_text(track)}",
                )
            except Exception as exc:
                logger.warning(
                    "Optional Telegram operation failed: %s", type(exc).__name__
                )

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
        await cq.message.answer(
            text, reply_markup=Keyboards.deal_close_confirm_kb(offer_id)
        )
        await cq.answer()

    @router.callback_query(F.data.startswith("deal:close_back:"))
    async def deal_close_back(cq: CallbackQuery):
        try:
            await cq.message.delete()
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)
        await cq.answer()

    @router.callback_query(F.data.startswith("deal:close:"))
    async def deal_close(cq: CallbackQuery):
        offer_id = int(cq.data.split(":")[-1])
        offer = escape_record(await db.get_offer(offer_id))
        if not offer:
            await cq.answer("Сделка не найдена.", show_alert=True)
            return

        seller_id = offer["buyer_id"]
        if cq.from_user.id != seller_id:
            await cq.answer(
                "Отказаться от сделки может только продавец.", show_alert=True
            )
            return

        if (
            offer["buyer_deposit_status"] == "confirmed"
            or offer["seller_deposit_status"] == "confirmed"
        ):
            if not await db.transition_offer(offer_id, "dispute", ("buyer_accepted",)):
                await cq.answer("Запрос уже обработан.")
                return
            req = await db.get_request(offer["request_id"])
            jobs = content(
                cfg.MODERATION_CHAT_ID,
                f"Продавец запросил отмену. Отклик №{offer_id}, заявка №{offer['request_id']}. Проверьте платежи и расчёты со сторонами. После завершения расчётов: /resolve_cancel {offer_id}",
                message_thread_id=offer.get("moderation_thread_id")
                or cfg.MODERATION_TOPIC_ID,
            )
            jobs += content(
                req["user_id"],
                f"По сделке №{req['id']} запрошена отмена. Менеджер проверит расчёты; сделка приостановлена.",
            )
            jobs += content(
                seller_id,
                "Запрос отмены передан менеджеру. Решение по залогам требует проверки.",
            )
            delivered = await queue_batch(
                db, cq.bot, f"cancellation-request-{offer_id}", jobs
            )
            await cq.answer(
                "Запрос отмены передан менеджеру."
                if delivered
                else "Запрос отмены сохранён; уведомления ожидают доставки."
            )
            return

        if not await db.transition_offer(
            offer_id, "closed_by_seller", ("buyer_accepted",)
        ):
            await cq.answer("Сделку нельзя отменить этим действием.", show_alert=True)
            return
        req = escape_record(await db.get_request(offer["request_id"]))
        buyer_id = req["user_id"]

        try:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=buyer_id,
                text=f"Продавец отменил сделку №{offer['request_id']}.",
            )
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        try:
            await notify(
                db,
                cq.bot,
                "send_message",
                chat_id=seller_id,
                text=f"Вы отменили сделку №{offer['request_id']}. Залог будет передан покупателю.",
            )
        except Exception as exc:
            logger.warning("Optional Telegram operation failed: %s", type(exc).__name__)

        await cq.answer("Сделка закрыта.")

    secure_callbacks(router, db, cfg, callback_start)

    secure_messages(router, db, cfg, message_start)
