from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from db import Database
from handlers_offers_deals import DEAL_STATUS_STEPS, setup_offers_deals_handlers
from handlers_sliders import setup_sliders_handlers
from states import DealTrack


class DealStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        for user_id in (101, 202, 303):
            await self.db.add_user(user_id, f"user{user_id}", "Tester")
        self.request_id = await self.db.create_request(
            101, "Order", "Item", "Details", "request-photo"
        )
        await self.db.set_request_status(self.request_id, "approved")
        self.offer_id = await self.db.create_offer(
            self.request_id, 202, 100000, 7, 5, "offer-photo"
        )
        await self.db.set_offer_status(self.offer_id, "buyer_accepted")
        await self.db.set_buyer_deposit_status(self.offer_id, "confirmed")
        await self.db.set_seller_deposit_status(self.offer_id, "confirmed")
        await self.db.set_offer_moderation_thread_id(self.offer_id, 11)
        cfg = SimpleNamespace(
            MODERATION_CHAT_ID=-100123,
            MODERATION_TOPIC_ID=11,
            MODERATOR_IDS=(303,),
            MANAGER_CDEK_CONTACT_TEXT="Manager contacts",
        )
        router = Router()
        setup_sliders_handlers(router, self.db, cfg)
        setup_offers_deals_handlers(router, self.db, cfg)
        self.callbacks = {
            handler.callback.__name__: handler.callback
            for handler in router.callback_query.handlers
        }
        self.messages = {
            handler.callback.__name__: handler.callback
            for handler in router.message.handlers
        }
        self.storage = MemoryStorage()
        self.state = FSMContext(
            self.storage, StorageKey(bot_id=1, chat_id=202, user_id=202)
        )
        self.bot = AsyncMock()
        self.cq = SimpleNamespace(
            data="",
            from_user=SimpleNamespace(id=202),
            bot=self.bot,
            message=self.message(202, 50, photo=True),
            answer=AsyncMock(),
        )

    def message(self, user_id, message_id=50, photo=False):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            chat=SimpleNamespace(id=user_id),
            message_id=message_id,
            photo=["photo"] if photo else None,
            answer=AsyncMock(),
            answer_photo=AsyncMock(),
            delete=AsyncMock(),
            edit_text=AsyncMock(),
            edit_reply_markup=AsyncMock(),
            bot=self.bot,
        )

    async def asyncTearDown(self):
        self.db.conn.close()
        await self.storage.close()

    async def open_status_menu(self, photo=True):
        self.cq.message = self.message(202, 50, photo=photo)
        self.cq.data = f"deal:status_menu:{self.offer_id}"
        await self.callbacks["deal_status_menu"](self.cq, self.state)
        self.cq.message = self.message(202, 51)

    async def pick(self, index):
        self.cq.data = f"deal:status_pick:{self.offer_id}:{index}"
        await self.callbacks["deal_status_set"](self.cq, self.state)

    async def test_seller_and_buyer_cards_show_each_saved_stage(self):
        for index, label in enumerate(DEAL_STATUS_STEPS, 1):
            with self.subTest(index=index):
                await self.db.set_deal_status(self.offer_id, index)
                for user_id, handler in (
                    (202, "offers_slider_start"),
                    (101, "requests_slider_start"),
                ):
                    msg = self.message(user_id)
                    await self.messages[handler](msg)
                    caption = msg.answer_photo.await_args.kwargs["caption"]
                    self.assertIn(f"Статус сделки: {label}", caption)
                    self.assertNotIn("«Изначально»", caption)

    async def test_track_status_is_shown_in_russian(self):
        for status, label in (
            ("approved", "Подтвержден"),
            ("pending", "На проверке"),
            ("rejected", "Отклонен"),
        ):
            with self.subTest(status=status):
                await self.db.set_track_info(self.offer_id, "111100101119", status)
                msg = self.message(202)
                await self.messages["offers_slider_start"](msg)
                caption = msg.answer_photo.await_args.kwargs["caption"]
                self.assertIn(f"Трек-номер: 111100101119 ({label})", caption)
                self.assertNotIn(f"({status})", caption)

    async def test_selected_stage_refreshes_original_photo_card_and_menu(self):
        await self.open_status_menu()
        await self.pick(5)
        self.assertEqual((await self.db.get_offer(self.offer_id))["deal_status"], 5)
        edit = self.bot.edit_message_caption.await_args.kwargs
        self.assertEqual((edit["chat_id"], edit["message_id"]), (202, 50))
        self.assertIn("Статус сделки: Товар у продавца", edit["caption"])
        menu = self.cq.message.edit_text.await_args.kwargs["reply_markup"]
        self.assertEqual(menu.inline_keyboard[4][0].text, "✅ Товар у меня")
        self.assertTrue(
            any(
                "изменён на: Товар у продавца" in call.kwargs["text"]
                for call in self.bot.send_message.await_args_list
            )
        )

    async def test_selected_stage_refreshes_original_text_card(self):
        await self.open_status_menu(photo=False)
        await self.pick(3)
        edit = self.bot.edit_message_text.await_args.kwargs
        self.assertEqual(edit["message_id"], 50)
        self.assertIn("Статус сделки: Товар на складе в другой стране", edit["text"])
        self.bot.edit_message_caption.assert_not_awaited()

    async def test_deleted_source_card_is_replaced_with_fresh_card(self):
        await self.open_status_menu()
        self.bot.edit_message_caption.side_effect = RuntimeError("message deleted")
        with self.assertLogs("handlers_sliders", level="WARNING"):
            await self.pick(4)
        self.assertIn(
            "Статус сделки: Товар отправлен в Россию",
            self.cq.message.answer_photo.await_args.kwargs["caption"],
        )

    async def test_return_button_preserves_tracking_input_state(self):
        await self.open_status_menu()
        await self.pick(6)
        self.cq.data = f"deal:status_back:{self.offer_id}"
        await self.callbacks["deal_status_back"](self.cq, self.state)
        self.cq.message.delete.assert_awaited_once()
        self.assertEqual(
            await self.state.get_state(), DealTrack.waiting_for_track.state
        )
        self.assertEqual((await self.state.get_data())["offer_id"], self.offer_id)
        self.assertNotIn("deal_status_card", await self.state.get_data())
        self.assertIn(
            "Товар в пути до менеджера",
            self.bot.edit_message_caption.await_args.kwargs["caption"],
        )

    async def test_notification_failure_does_not_block_card_refresh(self):
        await self.open_status_menu()
        self.bot.send_message.side_effect = RuntimeError("notification unavailable")
        with self.assertLogs("delivery", level="WARNING"):
            await self.pick(6)
        self.assertEqual((await self.db.get_offer(self.offer_id))["deal_status"], 6)
        self.assertIn(
            "Товар в пути до менеджера",
            self.bot.edit_message_caption.await_args.kwargs["caption"],
        )
        self.assertIn("ожидает доставки", self.cq.answer.await_args.args[0])

    async def test_invalid_or_unauthorized_status_change_does_not_modify_database(self):
        await self.db.set_deal_status(self.offer_id, 3)
        for index in (-1, 0, 7, "invalid"):
            with self.subTest(index=index):
                await self.pick(index)
                self.assertEqual(
                    (await self.db.get_offer(self.offer_id))["deal_status"], 3
                )
        self.cq.from_user.id = 101
        await self.pick(5)
        self.assertEqual((await self.db.get_offer(self.offer_id))["deal_status"], 3)

    async def test_buyer_card_prefers_accepted_offer_over_new_approved_offer(self):
        await self.db.set_deal_status(self.offer_id, 4)
        await self.db.set_offer_status(self.offer_id, "approved")
        competing = await self.db.create_offer(
            self.request_id, 303, 200000, 10, 8, "other-photo"
        )
        await self.db.set_offer_status(competing, "approved")
        await self.db.set_offer_status(self.offer_id, "buyer_accepted")
        msg = self.message(101)
        await self.messages["requests_slider_start"](msg)
        self.assertIn(
            "Статус сделки: Товар отправлен в Россию",
            msg.answer_photo.await_args.kwargs["caption"],
        )

    async def test_deposit_confirmation_initializes_but_never_resets_advanced_stage(
        self,
    ):
        await self.db.set_buyer_deposit_status(self.offer_id, "confirmed")
        await self.db.set_seller_deposit_status(self.offer_id, "confirmed")
        for stage, expected in ((0, 1), (1, 1), (4, 4), (6, 6)):
            with self.subTest(stage=stage):
                await self.db.set_deal_status(self.offer_id, stage)
                self.db.conn.execute(
                    "UPDATE offers SET buyer_deposit_status='pending' WHERE id=?",
                    (self.offer_id,),
                )
                self.db.conn.commit()
                self.cq.from_user.id = 303
                self.cq.message.chat.id = -100123
                self.cq.data = f"deal:confirm:buyer:{self.offer_id}:0"
                await self.callbacks["deal_payment_confirm"](self.cq)
                self.assertEqual(
                    (await self.db.get_offer(self.offer_id))["deal_status"], expected
                )


if __name__ == "__main__":
    unittest.main()
