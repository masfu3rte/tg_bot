from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)


class Keyboards:
    @staticmethod
    def bottom_menu() -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🧾 Мои запросы")],
                [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="📮 Мои отклики")],
            ],
            resize_keyboard=True,
        )

    @staticmethod
    def start_menu(offer_url: str, channel_url: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📄 Публичная оферта", url=offer_url)],
                [InlineKeyboardButton(text="📢 Канал сервиса", url=channel_url)],
                [InlineKeyboardButton(text="✅ Принимаю условия", callback_data="offer:accept")],
            ]
        )

    @staticmethod
    def help_menu(
        support_url: str,
        channel_url: str,
        requests_channel_url: str,
        ads_url: str,
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🆘 Техподдержка", url=support_url)],
                [InlineKeyboardButton(text="📢 Канал сервиса", url=channel_url)],
                [InlineKeyboardButton(text="🧾 Канал с запросами", url=requests_channel_url)],
                [InlineKeyboardButton(text="💰 Размещение рекламы", url=ads_url)],
            ]
        )

    @staticmethod
    def my_requests_menu() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Создать новый запрос", callback_data="requests:new"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="Активные запросы", callback_data="requests:active"
                    )
                ],
                [InlineKeyboardButton(text="Вернуться", callback_data="requests:back")],
            ]
        )

    @staticmethod
    def new_request_skip_photo_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Пропустить фото", callback_data="request:skip_photo")]
            ]
        )

    @staticmethod
    def profile_menu_inline() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✏️ Контактные данные CDEK", callback_data="profile:cdek"
                    )
                ],
                [InlineKeyboardButton(text="✏️ Реквизиты", callback_data="profile:req")],
                [InlineKeyboardButton(text="🤝 Рефералы", callback_data="profile:referrals")],
            ]
        )

    @staticmethod
    def referral_withdraw_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💸 Вывести средства", callback_data="referral:withdraw"
                    )
                ]
            ]
        )

    @staticmethod
    def referral_withdraw_back_kb() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💸 Вывести средства", callback_data="referral:withdraw"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="↩️ Вернуться в профиль", callback_data="profile:back"
                    )
                ],
            ]
        )

    @staticmethod
    def profile_back_inline() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="↩️ Вернуться в профиль", callback_data="profile:back"
                    )
                ]
            ]
        )

    @staticmethod
    def moderation_request_kb(request_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Одобрить", callback_data=f"request:approve:{request_id}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить", callback_data=f"request:reject:{request_id}"
                    ),
                ]
            ]
        )

    @staticmethod
    def request_public_kb(bot_link: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Откликнуться", url=bot_link)],
            ]
        )

    @staticmethod
    def user_requests_slider_kb(
        request_id: int, index: int, total: int
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="«", callback_data=f"ur:prev:{request_id}"),
                    InlineKeyboardButton(
                        text=f"{index}/{total}", callback_data=f"ur:noop:{request_id}"
                    ),
                    InlineKeyboardButton(text="»", callback_data=f"ur:next:{request_id}"),
                ],
                [
                    InlineKeyboardButton(
                        text="✏️ Редактировать", callback_data=f"ur:edit:{request_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🗑 Удалить заявку", callback_data=f"ur:del:{request_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="↩️ Вернуться к активным запросам",
                        callback_data="requests:active",
                    )
                ],
            ]
        )

    @staticmethod
    def my_offers_menu() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Активные отклики", callback_data="offers:active"
                    )
                ],
                [InlineKeyboardButton(text="Вернуться", callback_data="offers:back")],
            ]
        )

    @staticmethod
    def user_offers_slider_kb(
        offer_id: int, index: int, total: int
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="«", callback_data=f"uo:prev:{offer_id}"),
                    InlineKeyboardButton(
                        text=f"{index}/{total}", callback_data=f"uo:page:{offer_id}"
                    ),
                    InlineKeyboardButton(text="»", callback_data=f"uo:next:{offer_id}"),
                ],
                [
                    InlineKeyboardButton(
                        text="🔘 Изменить статус",
                        callback_data=f"deal:status_menu:{offer_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔘 Закрыть сделку",
                        callback_data=f"deal:close_prompt:{offer_id}",
                    )
                ],
            ]
        )

    @staticmethod
    def condition_inline_kb() -> InlineKeyboardMarkup:
        row1 = [
            InlineKeyboardButton(text=str(i), callback_data=f"offer:cond:{i}")
            for i in range(1, 6)
        ]
        row2 = [
            InlineKeyboardButton(text=str(i), callback_data=f"offer:cond:{i}")
            for i in range(6, 11)
        ]
        return InlineKeyboardMarkup(inline_keyboard=[row1, row2])

    @staticmethod
    def offer_moderation_kb(offer_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Принять отклик", callback_data=f"offer:approve:{offer_id}"
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить отклик", callback_data=f"offer:reject:{offer_id}"
                    ),
                ]
            ]
        )

    @staticmethod
    def offer_buyer_decision_kb(offer_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Принять отклик",
                        callback_data=f"offer:buyer_accept:{offer_id}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Отклонить отклик",
                        callback_data=f"offer:buyer_reject:{offer_id}",
                    ),
                ]
            ]
        )

    @staticmethod
    def deal_paid_kb_for_side(offer_id: int, side: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔘 Оплатил", callback_data=f"deal:paid:{side}:{offer_id}"
                    )
                ]
            ]
        )

    @staticmethod
    def deal_payment_moderation_kb(
        offer_id: int, side: str
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔘 Оплата прошла",
                        callback_data=f"deal:confirm:{side}:{offer_id}",
                    ),
                    InlineKeyboardButton(
                        text="🔘 Оплата не прошла",
                        callback_data=f"deal:cancel:{side}:{offer_id}",
                    ),
                ]
            ]
        )

    @staticmethod
    def deal_track_moderation_kb(offer_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔘 Трек принят",
                        callback_data=f"deal:confirm:track:{offer_id}",
                    ),
                    InlineKeyboardButton(
                        text="🔘 Трек не принят",
                        callback_data=f"deal:cancel:track:{offer_id}",
                    ),
                ]
            ]
        )

    @staticmethod
    def deal_arrival_moderation_kb(offer_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔘 Принять",
                        callback_data=f"deal:arrival_accept:{offer_id}",
                    ),
                    InlineKeyboardButton(
                        text="🔘 Открыть спор",
                        callback_data=f"deal:arrival_dispute:{offer_id}",
                    ),
                ]
            ]
        )

    @staticmethod
    def deal_arrival_buyer_kb(offer_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔘 Принять",
                        callback_data=f"deal:buyer_accept:{offer_id}",
                    ),
                    InlineKeyboardButton(
                        text="🔘 Открыть спор",
                        callback_data=f"deal:buyer_dispute:{offer_id}",
                    ),
                ]
            ]
        )

    @staticmethod
    def deal_final_paid_kb(offer_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔘 Оплатил остаток",
                        callback_data=f"deal:final_paid:{offer_id}",
                    )
                ]
            ]
        )

    @staticmethod
    def deal_funds_sent_kb(offer_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔘 Деньги отправлены",
                        callback_data=f"deal:funds_sent:{offer_id}",
                    )
                ]
            ]
        )

    @staticmethod
    def deal_delivery_choice_kb(offer_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔘 CDEK",
                        callback_data=f"deal:delivery:cdek:{offer_id}",
                    ),
                    InlineKeyboardButton(
                        text="🔘 Самовывоз Москва",
                        callback_data=f"deal:delivery:self:{offer_id}",
                    ),
                ]
            ]
        )

    @staticmethod
    def deal_cdek_sent_kb(offer_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔘 Отправил",
                        callback_data=f"deal:cdek_sent:{offer_id}",
                    )
                ]
            ]
        )

    @staticmethod
    def deal_rating_kb(offer_id: int) -> InlineKeyboardMarkup:
        stars = [
            InlineKeyboardButton(
                text=f"{rating}⭐",
                callback_data=f"deal:rate:{offer_id}:{rating}",
            )
            for rating in range(1, 6)
        ]
        return InlineKeyboardMarkup(inline_keyboard=[stars])

    @staticmethod
    def deal_status_menu_kb(
        offer_id: int, status_index: int, total: int
    ) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="«",
                        callback_data=f"deal:status_prev:{offer_id}:{status_index}",
                    ),
                    InlineKeyboardButton(
                        text=f"{status_index}/{total}",
                        callback_data=f"deal:status_show:{offer_id}:{status_index}",
                    ),
                    InlineKeyboardButton(
                        text="»",
                        callback_data=f"deal:status_next:{offer_id}:{status_index}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="✅ Выбрать этот статус",
                        callback_data=f"deal:status_set:{offer_id}:{status_index}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 Вернуться",
                        callback_data=f"deal:status_back:{offer_id}",
                    )
                ],
            ]
        )

    @staticmethod
    def deal_close_confirm_kb(offer_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔴 Закрыть❌", callback_data=f"deal:close:{offer_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 Вернуться",
                        callback_data=f"deal:close_back:{offer_id}",
                    )
                ],
            ]
        )
