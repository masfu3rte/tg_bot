import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import dotenv_values

from config import load_config
from db import Database
from handlers_offers_deals import setup_offers_deals_handlers
from handlers_sliders import setup_sliders_handlers
from update_manager_requisites import update_manager_requisites


class ManagerRequisitesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env_file = Path(self.directory.name) / ".env"

    def assert_payment_details(self, text):
        self.assertIn("Номер счета: 40802810320001147062", text)
        self.assertIn("БИК: 044525104", text)
        self.assertIn("<b>Внимание‼️</b>", text)
        self.assertIn("через кнопку «По реквизитам»", text)
        self.assertIn("обратитесь к @someoutmanager", text)
        self.assertNotIn("&#x", text)
        self.assertNotIn("Есть есть", text)

    def test_update_preserves_existing_settings_and_is_repeatable(self):
        self.env_file.write_text(
            "# Keep existing setup\nBOT_TOKEN=offline-only-test-token\nDB_PATH=existing.sqlite\n"
            "MANAGER_REQUISITES_TEXT='Old payment instructions'\nREFERRALS_VISIBLE=0\n"
        )
        update_manager_requisites(self.env_file)
        values = dotenv_values(self.env_file)
        self.assertEqual(values["BOT_TOKEN"], "offline-only-test-token")
        self.assertEqual(values["DB_PATH"], "existing.sqlite")
        self.assertEqual(values["REFERRALS_VISIBLE"], "0")
        self.assert_payment_details(values["MANAGER_REQUISITES_TEXT"])
        saved = self.env_file.read_text()
        update_manager_requisites(self.env_file)
        self.assertEqual(self.env_file.read_text(), saved)

    def test_update_creates_only_payment_setting_when_file_is_missing(self):
        update_manager_requisites(self.env_file)
        values = dotenv_values(self.env_file)
        self.assertEqual(set(values), {"MANAGER_REQUISITES_TEXT"})
        self.assert_payment_details(values["MANAGER_REQUISITES_TEXT"])

    def assert_cdek_contacts(self, text):
        self.assertIn("Контактные данные для отправки товара менеджеру через CDEK:", text)
        self.assertIn("1. Широков Владислав Дмитриевич", text)
        self.assertIn("2. 79998623067", text)
        self.assertIn("3. Москва, ул. Черняховского, 5 корп. 2", text)
        self.assertIn("Вам нужно самостоятельно оплатить доставку товара.", text)
        self.assertIn("В ином случае он будет отправлен обратно.", text)

    def test_cdek_update_preserves_bank_details_and_other_settings(self):
        self.env_file.write_text(
            "BOT_TOKEN=offline-only-test-token\nDB_PATH=existing.sqlite\n"
            "MANAGER_REQUISITES_TEXT='Keep bank details'\nMANAGER_CDEK_CONTACT_TEXT='Old CDEK contact'\n"
        )
        update_manager_requisites(self.env_file, cdek_only=True)
        values = dotenv_values(self.env_file)
        self.assertEqual(values["BOT_TOKEN"], "offline-only-test-token")
        self.assertEqual(values["DB_PATH"], "existing.sqlite")
        self.assertEqual(values["MANAGER_REQUISITES_TEXT"], "Keep bank details")
        self.assert_cdek_contacts(values["MANAGER_CDEK_CONTACT_TEXT"])
        saved = self.env_file.read_text()
        update_manager_requisites(self.env_file, cdek_only=True)
        self.assertEqual(self.env_file.read_text(), saved)

    async def test_seller_receives_new_cdek_contacts_when_goods_are_with_seller(self):
        update_manager_requisites(self.env_file, cdek_only=True)
        value = dotenv_values(self.env_file)["MANAGER_CDEK_CONTACT_TEXT"]
        with patch.dict(os.environ, {
            "BOT_TOKEN": "123456789:offline_cdek_check_only",
            "DB_PATH": ":memory:", "MANAGER_CDEK_CONTACT_TEXT": value,
        }):
            cfg = load_config()
        db = Database(":memory:")
        storage = MemoryStorage()
        state = FSMContext(storage, StorageKey(bot_id=1, chat_id=202, user_id=202))
        try:
            await db.add_user(101, "buyer", "Buyer")
            await db.add_user(202, "seller", "Seller")
            request_id = await db.create_request(101, "Order", "Item", "Details", None)
            offer_id = await db.create_offer(request_id, 202, 100000, 3, 5, "photo")
            await db.set_offer_status(offer_id, "buyer_accepted")
            router = Router()
            setup_sliders_handlers(router, db, cfg)
            handler = next(h.callback for h in router.callback_query.handlers if h.callback.__name__ == "deal_status_set")
            cq = SimpleNamespace(
                data=f"deal:status_pick:{offer_id}:5", from_user=SimpleNamespace(id=202),
                bot=AsyncMock(), message=AsyncMock(), answer=AsyncMock(),
            )
            await handler(cq, state)
            contacts = [call.kwargs["text"] for call in cq.bot.send_message.await_args_list if call.kwargs["chat_id"] == 202]
            self.assertEqual(len(contacts), 1)
            self.assert_cdek_contacts(contacts[0])
        finally:
            db.conn.close()
            await storage.close()

    async def test_buyer_seller_and_final_payment_messages_use_new_details(self):
        update_manager_requisites(self.env_file)
        value = dotenv_values(self.env_file)["MANAGER_REQUISITES_TEXT"]
        with patch.dict(os.environ, {
            "BOT_TOKEN": "123456789:offline_payment_check_only",
            "DB_PATH": ":memory:", "MANAGER_REQUISITES_TEXT": value,
        }):
            cfg = load_config()
        db = Database(":memory:")
        try:
            await db.add_user(101, "buyer", "Buyer")
            await db.add_user(202, "seller", "Seller")
            request_id = await db.create_request(101, "Order", "Item", "Details", None)
            offer_id = await db.create_offer(request_id, 202, 100000, 3, 5, "photo")
            await db.set_offer_status(offer_id, "approved")
            router = Router()
            setup_offers_deals_handlers(router, db, cfg)
            handlers = {h.callback.__name__: h.callback for h in router.callback_query.handlers}
            cq = SimpleNamespace(
                data=f"offer:buyer_accept:{offer_id}", from_user=SimpleNamespace(id=101),
                bot=AsyncMock(), message=AsyncMock(), answer=AsyncMock(),
            )
            await handlers["offer_buyer_accept"](cq)
            self.assertEqual(cq.bot.send_message.await_count, 2)
            for call in cq.bot.send_message.await_args_list:
                self.assert_payment_details(call.kwargs["text"])
            cq.bot.send_message.reset_mock()
            cq.data = f"deal:buyer_accept:{offer_id}"
            await handlers["deal_buyer_accept"](cq)
            self.assert_payment_details(cq.bot.send_message.await_args.kwargs["text"])
            self.assertIn("Остаток к оплате: 847.50 руб.", cq.bot.send_message.await_args.kwargs["text"])
        finally:
            db.conn.close()


if __name__ == "__main__":
    unittest.main()
