"""Authorise callbacks at the handler boundary, including direct handler invocation."""

import logging
from functools import wraps

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InaccessibleMessage
from delivery import FeedbackProxy, delivery_deferred, notification_scope

log = logging.getLogger(__name__)
TERMINAL = {
    "completed",
    "cancelled",
    "refunded",
    "closed_by_seller",
    "buyer_rejected",
    "rejected",
}
MODERATORS = {
    "approve_request",
    "reject_request",
    "offer_approve",
    "offer_reject",
    "deal_payment_confirm",
    "deal_payment_cancel",
    "track_confirm",
    "track_reject",
    "deal_arrival_accept",
    "deal_arrival_dispute",
    "deal_funds_sent",
    "deal_cdek_sent",
}
BUYERS = {
    "offer_buyer_accept",
    "offer_buyer_reject",
    "deal_buyer_accept",
    "deal_buyer_dispute",
    "deal_final_paid",
    "deal_delivery_cdek",
    "deal_delivery_self",
    "deal_rate_seller",
}
SELLERS = {
    "deal_close",
    "deal_close_prompt",
    "deal_status_menu",
    "deal_status_set",
    "deal_status_back",
    "offer_prev",
    "offer_next",
}
REQUEST_OWNERS = {"req_prev", "req_next", "req_edit_start", "req_delete"}
PUBLIC = {
    "accept_offer",
    "my_referrals_inline",
    "referral_withdraw",
    "profile_cdek",
    "profile_req",
    "edit_cdek",
    "edit_req",
    "profile_back",
    "new_request_start_inline",
    "back_to_main_inline",
    "back_to_requests_menu_inline",
    "req_skip_photo",
    "requests_slider_start_inline",
    "offers_slider_start_inline",
    "offers_back_inline",
    "offer_condition_btn",
    "deal_close_back",
    "noop_callback",
    "requests_archive",
    "offers_archive",
    "edit_request_field",
    "edit_request_cancel",
}


async def is_moderator(event, cfg) -> bool:
    if not event.message or event.message.chat.id != getattr(
        cfg, "MODERATION_CHAT_ID", None
    ):
        return False
    ids = getattr(cfg, "MODERATOR_IDS", ())
    if ids:
        return event.from_user.id in ids
    try:
        member = await event.bot.get_chat_member(
            cfg.MODERATION_CHAT_ID, event.from_user.id
        )
        return member.status in ("administrator", "creator")
    except TelegramAPIError:
        return False


def secure_callbacks(router, db, cfg, start=0):
    for obj in router.callback_query.handlers[start:]:
        original = obj.callback
        name = original.__name__

        @wraps(original)
        async def guarded(cq, *args, __original=original, __name=name, **kwargs):
            async def deny(text="Действие недоступно или уже выполнено."):
                await cq.answer(text, show_alert=True)

            name = __name
            if (
                not cq.message
                or isinstance(cq.message, InaccessibleMessage)
                or not cq.data
            ):
                return await deny()
            if name in MODERATORS:
                if not await is_moderator(cq, cfg):
                    return await deny("Нет доступа к действиям модератора.")
            elif name in BUYERS | SELLERS | REQUEST_OWNERS | {"deal_paid"}:
                if cq.message.chat.id != cq.from_user.id:
                    return await deny("Откройте сделку в личном чате с ботом.")
            elif name not in PUBLIC:
                log.warning("Unclassified callback rejected: %s", name)
                return await deny()
            elif cq.message.chat.id != cq.from_user.id:
                return await deny("Откройте личный чат с ботом.")

            offer = req = None
            if name in MODERATORS | BUYERS | SELLERS | REQUEST_OWNERS | {"deal_paid"}:
                parts = cq.data.split(":")
                try:
                    versioned = (
                        name in {"deal_payment_confirm", "deal_payment_cancel"}
                        and len(parts) == 5
                    ) or (
                        name in {"approve_request", "reject_request"}
                        and len(parts) == 4
                    )
                    ident = int(
                        parts[-2]
                        if versioned or name in {"deal_status_set", "deal_rate_seller"}
                        else parts[-1]
                    )
                    if ident <= 0:
                        return await deny()
                except (ValueError, IndexError):
                    return await deny("Некорректная кнопка.")
                if name in REQUEST_OWNERS | {"approve_request", "reject_request"}:
                    req = await db.get_request(ident)
                    if not req:
                        return await deny("Заявка не найдена.")
                else:
                    offer = await db.get_offer(ident)
                    if not offer:
                        return await deny("Сделка не найдена.")
                    req = await db.get_request(offer["request_id"])
                    if not req:
                        return await deny("Заявка не найдена.")
                if name in {"deal_payment_confirm", "deal_payment_cancel"}:
                    side = parts[2] if len(parts) in (4, 5) else ""
                    if side not in ("buyer", "seller", "final"):
                        return await deny("Неверная сторона платежа.")
                    try:
                        version = int(parts[4]) if len(parts) == 5 else 0
                    except ValueError:
                        return await deny()
                    if version != offer.get(side + "_payment_version", 0):
                        return await deny("Запрос оплаты уже заменён новым.")
                if name in {"approve_request", "reject_request"}:
                    try:
                        revision = int(parts[3]) if len(parts) == 4 else 0
                    except ValueError:
                        return await deny()
                    if revision != req.get("revision", 0):
                        return await deny(
                            "Заявка изменена. Используйте новую карточку модерации."
                        )
                if (
                    name in REQUEST_OWNERS | BUYERS
                    and req["user_id"] != cq.from_user.id
                ):
                    return await deny("Это не ваша заявка.")
                if name in SELLERS and offer["buyer_id"] != cq.from_user.id:
                    return await deny("Действие доступно только продавцу.")
                if name == "deal_paid":
                    side = parts[2] if len(parts) == 4 else ""
                    target = (
                        req["user_id"]
                        if side == "buyer"
                        else offer["buyer_id"] if side == "seller" else None
                    )
                    if target != cq.from_user.id:
                        return await deny("Неверная сторона сделки.")
                delivery = {
                    "deal_funds_sent",
                    "deal_delivery_cdek",
                    "deal_delivery_self",
                    "deal_cdek_sent",
                    "deal_rate_seller",
                }
                readonly = {
                    "offer_prev",
                    "offer_next",
                    "req_prev",
                    "req_next",
                    "deal_status_back",
                }
                if offer and (
                    offer.get("final_status") in TERMINAL
                    or offer.get("status") in TERMINAL
                ):
                    if name not in delivery | readonly:
                        return await deny("Сделка уже завершена.")
                if name in delivery and offer["final_payment_status"] != "confirmed":
                    return await deny(
                        "Сначала требуется подтверждение финальной оплаты."
                    )
                if name in {
                    "deal_delivery_cdek",
                    "deal_delivery_self",
                    "deal_cdek_sent",
                } and not offer.get("funds_sent"):
                    return await deny(
                        "Сначала менеджер должен отметить отправку средств."
                    )
                if name in {"deal_delivery_cdek", "deal_delivery_self"} and offer.get(
                    "manager_track_number"
                ):
                    return await deny(
                        "Товар уже отправлен. Изменить способ получения нельзя."
                    )
                if (
                    name in {"offer_approve", "offer_reject"}
                    and offer["status"] != "pending"
                ):
                    return await deny()
                if (
                    name in {"offer_buyer_accept", "offer_buyer_reject"}
                    and offer["status"] != "approved"
                ):
                    return await deny()
                if (
                    name in {"approve_request", "reject_request"}
                    and req["status"] != "pending"
                ):
                    return await deny()
                deal_actions = {
                    "deal_status_menu",
                    "deal_status_set",
                    "track_confirm",
                    "track_reject",
                    "deal_arrival_accept",
                    "deal_arrival_dispute",
                    "deal_buyer_accept",
                    "deal_buyer_dispute",
                    "deal_final_paid",
                }
                if name in deal_actions:
                    if offer["status"] != "buyer_accepted" or any(
                        offer[k] != "confirmed"
                        for k in ("buyer_deposit_status", "seller_deposit_status")
                    ):
                        return await deny(
                            "Сначала должны быть подтверждены оба залога."
                        )
                if name in {"track_confirm", "track_reject"} and (
                    offer["track_status"] != "pending" or not offer["track_number"]
                ):
                    return await deny()
                if (
                    name in {"deal_arrival_accept", "deal_arrival_dispute"}
                    and offer["track_status"] != "approved"
                ):
                    return await deny("Сначала подтвердите трек.")
                if name in {
                    "deal_buyer_accept",
                    "deal_buyer_dispute",
                } and not offer.get("arrival_notified"):
                    return await deny("Фото товара ещё не переданы покупателю.")
            try:
                token = delivery_deferred.set(False)
                scope = notification_scope.set(
                    "callback:" + str(getattr(cq, "id", "direct"))
                )
                try:
                    return await __original(FeedbackProxy(cq), *args, **kwargs)
                finally:
                    delivery_deferred.reset(token)
                    notification_scope.reset(scope)
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                log.warning(
                    "Invalid callback input in %s: %s", name, type(exc).__name__
                )
                return await deny("Кнопка устарела. Откройте раздел заново.")

        obj.callback = guarded


def secure_messages(router, db, cfg, start=0):
    for obj in router.message.handlers[start:]:
        original = obj.callback
        name = original.__name__

        @wraps(original)
        async def guarded(msg, *args, __original=original, __name=name, **kwargs):
            state = kwargs.get("state") or (args[0] if args else None)
            manager = (
                __name in {"deal_arrival_photo", "manager_track_send"}
                or __name.startswith("report_")
                or __name in {"delivery_status", "retry_delivery", "resolve_cancel"}
            )
            if manager:
                event = type(
                    "Actor", (), dict(message=msg, from_user=msg.from_user, bot=msg.bot)
                )()
                if not await is_moderator(event, cfg):
                    await msg.answer(
                        "Действие доступно только модератору в рабочем чате."
                    )
                    return
            elif msg.chat.id != msg.from_user.id:
                await msg.answer("Используйте личный чат с ботом.")
                return
            if __name in {"seller_track", "deal_arrival_photo", "manager_track_send"}:
                data = await state.get_data() if state else {}
                offer = (
                    await db.get_offer(data.get("offer_id"))
                    if data.get("offer_id")
                    else None
                )
                if not offer:
                    await msg.answer("Сделка не найдена. Откройте её заново.")
                    return
                if __name == "seller_track" and (
                    offer["buyer_id"] != msg.from_user.id
                    or offer["status"] != "buyer_accepted"
                    or offer["deal_status"] != 6
                    or offer["final_payment_status"] == "confirmed"
                ):
                    await msg.answer("Ввод трека для этой сделки недоступен.")
                    return
                if __name == "deal_arrival_photo" and (
                    offer["track_status"] != "approved"
                    or offer["status"] != "buyer_accepted"
                    or offer["final_payment_status"] == "confirmed"
                ):
                    await msg.answer("Приёмка этой сделки недоступна.")
                    return
                if __name == "manager_track_send" and (
                    offer["final_payment_status"] != "confirmed"
                    or offer["delivery_method"] != "cdek"
                    or not offer["funds_sent"]
                ):
                    await msg.answer("Отправка по CDEK ещё не подтверждена.")
                    return
            token = delivery_deferred.set(False)
            scope = notification_scope.set(
                f'message:{msg.chat.id}:{getattr(msg,"message_id",0)}'
            )
            try:
                return await __original(FeedbackProxy(msg), *args, **kwargs)
            finally:
                delivery_deferred.reset(token)
                notification_scope.reset(scope)

        obj.callback = guarded
