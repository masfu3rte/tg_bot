import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from db import Database
from handlers_start_help import WELCOME_TEXT, setup_start_help_handlers
from states import OfferCreate


class StartFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        self.storage = MemoryStorage()
        self.state = FSMContext(
            self.storage, StorageKey(bot_id=1, chat_id=101, user_id=101)
        )
        cfg = SimpleNamespace(
            ASSETS_CHANNEL_ID=None,
            START_BANNER_ID=None,
            OFFER_URL="https://example.com/offer",
            PRIVACY_POLICY_URL="https://example.com/privacy",
            CHANNEL_URL="https://t.me/someout",
            SUPPORT_URL="https://t.me/someoutmanager",
            REQUESTS_CHANNEL_URL="https://example.com/old-requests",
            ADS_URL="https://example.com/old-ads",
        )
        self.cfg = cfg
        router = Router()
        setup_start_help_handlers(router, self.db, cfg)
        self.start = next(
            h.callback
            for h in router.message.handlers
            if h.callback.__name__ == "cmd_start"
        )
        self.info = next(
            h.callback
            for h in router.message.handlers
            if h.callback.__name__ == "info_cmd"
        )
        self.help = next(
            h.callback
            for h in router.message.handlers
            if h.callback.__name__ == "help_cmd"
        )
        self.accept = router.callback_query.handlers[0].callback
        self.user = SimpleNamespace(id=101, username="seller", full_name="Seller")
        self.msg = SimpleNamespace(
            from_user=self.user,
            text="/start",
            answer=AsyncMock(),
            answer_photo=AsyncMock(),
            chat=SimpleNamespace(id=101),
            bot=AsyncMock(),
        )
        # A callback message is authored by the bot, not by the person clicking.
        self.callback_msg = SimpleNamespace(
            from_user=SimpleNamespace(id=999),
            chat=SimpleNamespace(id=101),
            answer=AsyncMock(),
            answer_photo=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        )
        self.cq = SimpleNamespace(
            from_user=self.user,
            message=self.callback_msg,
            answer=AsyncMock(),
            data="offer:accept",
        )
        await self.db.add_user(202, "buyer", "Buyer")
        self.request_id = await self.db.create_request(
            202, "Test", "Item", "Description", None
        )
        await self.db.set_request_status(self.request_id, "approved")
        await self.db.set_cdek_contacts(101, "Seller", "123", "Office")
        await self.db.set_requisites(101, "Seller", "123", "Bank")

    async def asyncTearDown(self):
        self.db.conn.close()
        await self.storage.close()

    async def open_request(self, payload=None):
        self.msg.text = f"/start {payload or f'req_{self.request_id}'}"
        await self.start(self.msg, self.state)

    def assert_terms_shown(self):
        self.assertIn(
            "Перед началом использования сервиса", self.msg.answer.await_args.args[0]
        )
        keyboard = self.msg.answer.await_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.inline_keyboard[-1][0].callback_data, "offer:accept")

    async def test_orders_link_requires_explicit_acceptance(self):
        await self.open_request()
        self.assert_terms_shown()
        self.assertFalse(await self.db.is_offer_accepted(101))
        self.assertIsNone(await self.state.get_state())
        self.assertNotIn("request_id", await self.state.get_data())
        self.callback_msg.answer.assert_not_awaited()

    async def test_returning_user_opens_orders_link_without_repeating_documents(self):
        await self.db.add_user(101, "seller", "Seller")
        await self.db.set_offer_accepted(101, True)
        await self.open_request()
        text = self.msg.answer.await_args.args[0]
        self.assertIn(f"заявку №{self.request_id}", text)
        self.assertNotIn("Перед началом использования сервиса", text)
        self.assertEqual(
            await self.state.get_state(), OfferCreate.waiting_for_price.state
        )
        self.assertEqual((await self.state.get_data())["request_id"], self.request_id)
        self.assertNotIn("pending_request_payload", await self.state.get_data())

    async def test_returning_user_start_shows_welcome_and_menu_without_documents(self):
        await self.start(self.msg, self.state)
        self.assert_terms_shown()
        await self.accept(self.cq, self.state)
        self.msg.answer.reset_mock()
        for _ in range(2):
            await self.start(self.msg, self.state)
        self.assertEqual(self.msg.answer.await_count, 2)
        for call in self.msg.answer.await_args_list:
            self.assertEqual(call.args[0], WELCOME_TEXT)
            self.assertIn("https://t.me/someout/445", call.args[0])
            self.assertIn("https://t.me/someout/446", call.args[0])
            self.assertTrue(call.kwargs["reply_markup"].keyboard)

    async def test_returning_user_start_sends_configured_banner_with_welcome(self):
        await self.db.add_user(101, "seller", "Seller")
        await self.db.set_offer_accepted(101, True)
        self.cfg.ASSETS_CHANNEL_ID = -100123
        self.cfg.START_BANNER_ID = 7
        await self.start(self.msg, self.state)
        self.msg.bot.copy_message.assert_awaited_once()
        sent = self.msg.bot.copy_message.await_args.kwargs
        self.assertEqual(
            (sent["chat_id"], sent["from_chat_id"], sent["message_id"]),
            (101, -100123, 7),
        )
        self.assertEqual(sent["caption"], WELCOME_TEXT)
        self.assertTrue(sent["reply_markup"].keyboard)
        self.msg.answer.assert_not_awaited()

    async def test_unavailable_banner_still_sends_welcome_and_menu(self):
        await self.db.add_user(101, "seller", "Seller")
        await self.db.set_offer_accepted(101, True)
        self.cfg.ASSETS_CHANNEL_ID = -100123
        self.cfg.START_BANNER_ID = 7
        self.msg.bot.copy_message.side_effect = RuntimeError("banner unavailable")
        await self.start(self.msg, self.state)
        self.assertEqual(self.msg.answer.await_args.args[0], WELCOME_TEXT)
        self.assertTrue(self.msg.answer.await_args.kwargs["reply_markup"].keyboard)

    async def test_start_repeats_documents_until_user_actually_accepts(self):
        await self.start(self.msg, self.state)
        await self.start(self.msg, self.state)
        self.assert_terms_shown()
        self.assertFalse(await self.db.is_offer_accepted(101))
        self.callback_msg.answer.assert_not_awaited()

    async def test_help_has_document_channel_and_support_links_only(self):
        await self.help(self.msg)
        self.msg.answer.assert_awaited_once()
        self.msg.bot.copy_message.assert_not_awaited()
        buttons = [
            button
            for row in self.msg.answer.await_args.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertEqual(
            [(button.text, button.url) for button in buttons],
            [
                ("📄 Публичная оферта", self.cfg.OFFER_URL),
                ("Политика обработки данных", self.cfg.PRIVACY_POLICY_URL),
                ("📢 Канал сервиса", self.cfg.CHANNEL_URL),
                ("🆘 Техподдержка", self.cfg.SUPPORT_URL),
            ],
        )
        self.assertTrue(all(button.callback_data is None for button in buttons))
        self.assertNotIn("Приветствуем", self.msg.answer.await_args.args[0])

    def test_welcome_guides_match_reader_roles(self):
        self.assertIn(
            '<a href="https://t.me/someout/446">Гайд по пользованию для покупателей</a>',
            WELCOME_TEXT,
        )
        self.assertIn(
            '<a href="https://t.me/someout/445">Гайд по пользованию для продавцов</a>',
            WELCOME_TEXT,
        )

    async def test_info_shows_only_documents_and_channel_without_welcome_or_banner(
        self,
    ):
        await self.db.add_user(101, "seller", "Seller")
        await self.db.set_offer_accepted(101, True)
        self.cfg.ASSETS_CHANNEL_ID = -100123
        self.cfg.START_BANNER_ID = 7
        await self.info(self.msg)
        self.msg.answer.assert_awaited_once()
        self.msg.bot.copy_message.assert_not_awaited()
        documents = self.msg.answer.await_args
        buttons = [
            button
            for row in documents.kwargs["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertEqual(
            [button.url for button in buttons],
            [
                "https://example.com/offer",
                "https://example.com/privacy",
                "https://t.me/someout",
            ],
        )
        self.assertFalse(
            any(button.callback_data == "offer:accept" for button in buttons)
        )
        self.assertNotIn("Приветствуем", documents.args[0])
        self.assertNotIn("Гайд", documents.args[0])
        self.assertNotIn("Техподдержка", str(documents.kwargs["reply_markup"]))

    async def test_info_before_acceptance_shows_documents_then_welcome_after_click(
        self,
    ):
        await self.info(self.msg)
        self.msg.answer.assert_awaited_once()
        self.assert_terms_shown()
        self.assertFalse(await self.db.is_offer_accepted(101))
        await self.accept(self.cq, self.state)
        self.assertTrue(await self.db.is_offer_accepted(101))
        self.assertEqual(self.callback_msg.answer.await_args.args[0], WELCOME_TEXT)

    async def test_info_preserves_pending_request_until_acceptance(self):
        await self.open_request()
        await self.info(self.msg)
        self.assertEqual(
            (await self.state.get_data())["pending_request_payload"],
            f"req_{self.request_id}",
        )
        await self.accept(self.cq, self.state)
        self.assertEqual(
            await self.state.get_state(), OfferCreate.waiting_for_price.state
        )
        self.assertEqual((await self.state.get_data())["request_id"], self.request_id)

    async def test_info_does_not_interrupt_active_offer(self):
        await self.db.add_user(101, "seller", "Seller")
        await self.db.set_offer_accepted(101, True)
        await self.state.set_state(OfferCreate.waiting_for_days)
        await self.state.set_data({"request_id": self.request_id, "price": 1000})
        await self.info(self.msg)
        self.assertEqual(
            await self.state.get_state(), OfferCreate.waiting_for_days.state
        )
        self.assertEqual(
            await self.state.get_data(), {"request_id": self.request_id, "price": 1000}
        )

    async def test_accept_button_records_time_for_legacy_user(self):
        await self.start(self.msg, self.state)
        self.db.conn.execute("UPDATE users SET offer_accepted=1 WHERE user_id=101")
        self.db.conn.commit()
        self.assertIsNone((await self.db.get_user(101))["offer_first_accepted_at"])
        await self.accept(self.cq, self.state)
        self.assertIsNotNone((await self.db.get_user(101))["offer_first_accepted_at"])

    async def test_acceptance_resumes_original_request_once(self):
        await self.open_request()
        await self.accept(self.cq, self.state)
        self.assertTrue(await self.db.is_offer_accepted(101))
        self.assertEqual(
            await self.state.get_state(), OfferCreate.waiting_for_price.state
        )
        self.assertEqual((await self.state.get_data())["request_id"], self.request_id)
        self.assertNotIn("pending_request_payload", await self.state.get_data())
        texts = [call.args[0] for call in self.callback_msg.answer.await_args_list]
        self.assertEqual(texts[0], WELCOME_TEXT)
        self.assertNotIn("Пусть каждая ваша сделка будет идеальной!", texts[0])
        self.assertIn(f"заявку №{self.request_id}", texts[1])
        await self.accept(self.cq, self.state)
        texts = [call.args[0] for call in self.callback_msg.answer.await_args_list]
        self.assertEqual(sum("Укажите цену" in text for text in texts), 1)

    async def test_plain_start_discards_pending_request_and_previous_state(self):
        await self.open_request()
        await self.state.set_state(OfferCreate.waiting_for_days)
        self.msg.text = "/start"
        await self.start(self.msg, self.state)
        self.assert_terms_shown()
        self.assertEqual(await self.state.get_data(), {})
        self.assertIsNone(await self.state.get_state())
        await self.accept(self.cq, self.state)
        self.callback_msg.answer.assert_awaited_once_with(
            WELCOME_TEXT,
            reply_markup=unittest.mock.ANY,
        )

    async def test_invalid_or_deleted_request_is_checked_after_acceptance(self):
        await self.db.set_request_status(self.request_id, "deleted")
        for payload in ("req_invalid", "req_999999", f"req_{self.request_id}"):
            with self.subTest(payload=payload):
                await self.db.set_offer_accepted(101, False)
                await self.open_request(payload)
                self.assert_terms_shown()
                await self.accept(self.cq, self.state)
                self.assertIn(
                    "Заявка не найдена", self.callback_msg.answer.await_args.args[0]
                )
                self.assertIsNone(await self.state.get_state())

    async def test_returning_user_gets_invalid_request_error_without_documents(self):
        await self.db.add_user(101, "seller", "Seller")
        await self.db.set_offer_accepted(101, True)
        for payload in ("req_invalid", "req_999999"):
            with self.subTest(payload=payload):
                await self.open_request(payload)
                self.assertIn("Заявка не найдена", self.msg.answer.await_args.args[0])
                self.assertIsNone(await self.state.get_state())

    async def test_incomplete_profile_cannot_start_offer_after_acceptance(self):
        self.db.conn.execute("DELETE FROM cdek_contacts WHERE user_id=101")
        self.db.conn.commit()
        await self.open_request()
        self.assert_terms_shown()
        await self.accept(self.cq, self.state)
        self.assertIn(
            "Перед откликом заполните", self.callback_msg.answer.await_args.args[0]
        )
        self.assertIsNone(await self.state.get_state())

    async def test_request_photo_is_preserved_after_acceptance(self):
        self.db.conn.execute(
            "UPDATE requests SET photo_file_id='photo' WHERE id=?", (self.request_id,)
        )
        self.db.conn.commit()
        await self.open_request()
        await self.accept(self.cq, self.state)
        self.assertEqual(
            self.callback_msg.answer_photo.await_args.kwargs["photo"], "photo"
        )
        self.assertIn(
            "Укажите цену", self.callback_msg.answer_photo.await_args.kwargs["caption"]
        )

    async def test_referral_link_still_registers_referrer_and_shows_documents(self):
        code = await self.db.ensure_referral_code(202)
        self.msg.text = f"/start ref_{code}"
        await self.start(self.msg, self.state)
        self.assertEqual(await self.db.get_referrer_id(101), 202)
        self.assert_terms_shown()
        self.assertFalse(await self.db.is_offer_accepted(101))


if __name__ == "__main__":
    unittest.main()
