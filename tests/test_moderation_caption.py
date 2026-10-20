from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from aiogram import Bot, Router
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageCaption, EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, User

from db import Database
from handlers_offers_deals import setup_offers_deals_handlers


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise AssertionError("Network access is forbidden in this test")
        yield

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        return Message(
            message_id=55, date=datetime.now(timezone.utc),
            chat=Chat(id=getattr(method, "chat_id", -100123), type="supergroup"),
        )


class ModerationCaptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.add_user(101, "buyer", "Buyer")
        await self.db.add_user(202, "seller", "Seller")
        self.request_id = await self.db.create_request(101, "Order", "Black jacket", "Size M", "request-photo")
        self.offer_id = await self.db.create_offer(self.request_id, 202, 100000, 7, 5, "offer-photo")
        self.session = RecordingSession()
        self.bot = Bot(token="123456789:offline_caption_check_only", session=self.session)
        router = Router()
        setup_offers_deals_handlers(router, self.db, SimpleNamespace(MANAGER_REQUISITES_TEXT="Payment details"))
        self.handlers = {h.callback.__name__: h.callback for h in router.callback_query.handlers}
        self.original = "Заявка №1\nНазвание: Black jacket\nОписание: Size M\nСумма: 1130.00₽"

    async def asyncTearDown(self):
        self.db.conn.close()
        await self.bot.session.close()

    async def invoke(self, name, action, *, caption="original", photo=True, user_id=101):
        message = Message(
            message_id=55, date=datetime.now(timezone.utc),
            chat=Chat(id=-100123, type="supergroup"), message_thread_id=11, is_topic_message=True,
            photo=[PhotoSize(file_id="photo", file_unique_id="unique", width=100, height=100)] if photo else None,
            caption=self.original if caption == "original" else caption,
            text=self.original if not photo else None,
        ).as_(self.bot)
        callback = CallbackQuery(
            id="callback", from_user=User(id=user_id, is_bot=False, first_name="Tester"),
            chat_instance="test", message=message, data=f"{action}:{self.offer_id}",
        ).as_(self.bot)
        await self.handlers[name](callback)

    def caption_edit(self):
        edits = [call for call in self.session.calls if isinstance(call, EditMessageCaption)]
        self.assertEqual(len(edits), 1)
        edit = edits[0]
        self.assertIsNone(edit.inline_message_id)
        self.assertEqual(edit.chat_id, -100123)
        self.assertEqual(edit.message_id, 55)
        self.assertIsNone(edit.reply_markup)
        return edit

    async def test_moderation_preserves_order_caption(self):
        await self.invoke("offer_approve", "offer:approve")
        edit = self.caption_edit()
        self.assertEqual(edit.caption, self.original + "\n\n✅ Отклик одобрен, ожидаем решение покупателя.")
        self.assertEqual((await self.db.get_offer(self.offer_id))["status"], "approved")

    async def test_moderation_rebuilds_missing_photo_caption_from_database(self):
        await self.invoke("offer_approve", "offer:approve", caption=None)
        edit = self.caption_edit()
        self.assertIn("Название: Black jacket", edit.caption)
        self.assertIn("Описание: Size M", edit.caption)
        self.assertIn("Общая сумма сделки: 1130.00₽", edit.caption)
        self.assertIn("Отклик одобрен", edit.caption)

    async def test_buyer_acceptance_preserves_photo_caption(self):
        await self.db.set_offer_status(self.offer_id, "approved")
        await self.invoke("offer_buyer_accept", "offer:buyer_accept")
        self.assertEqual(self.caption_edit().caption, self.original + "\n\n✅ Вы приняли отклик.")

    async def test_buyer_rejection_preserves_photo_caption(self):
        await self.db.set_offer_status(self.offer_id, "approved")
        await self.invoke("offer_buyer_reject", "offer:buyer_reject")
        self.assertEqual(self.caption_edit().caption, self.original + "\n\n❌ Вы отклонили отклик.")

    async def test_moderation_preserves_text_order(self):
        await self.invoke("offer_approve", "offer:approve", photo=False, caption=None)
        edits = [call for call in self.session.calls if isinstance(call, EditMessageText)]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0].text, self.original + "\n\n✅ Отклик одобрен, ожидаем решение покупателя.")
        self.assertFalse(any(isinstance(call, EditMessageCaption) for call in self.session.calls))


if __name__ == "__main__":
    unittest.main()
