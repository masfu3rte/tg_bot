from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from aiogram import Router

from db import Database
from handlers_profile import setup_profile_handlers


class ReferralVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.add_user(101, "tester", "Tester")
        await self.db.add_referral_bonus(101, 1300)
        self.cfg = SimpleNamespace(
            REFERRALS_VISIBLE=False,
            ASSETS_CHANNEL_ID=None,
            PROFILE_BANNER_MESSAGE_ID=None,
        )
        router = Router()
        setup_profile_handlers(router, self.db, self.cfg)
        self.messages = {
            handler.callback.__name__: handler.callback
            for handler in router.message.handlers
        }
        self.callbacks = {
            handler.callback.__name__: handler.callback
            for handler in router.callback_query.handlers
        }
        self.bot = AsyncMock()
        self.bot.get_me.return_value = SimpleNamespace(username="test_bot")
        self.msg = SimpleNamespace(
            from_user=SimpleNamespace(id=101),
            chat=SimpleNamespace(id=101),
            bot=self.bot,
            answer=AsyncMock(),
        )
        self.cq = SimpleNamespace(
            from_user=self.msg.from_user,
            message=AsyncMock(),
            bot=self.bot,
            answer=AsyncMock(),
            data="profile:referrals",
        )

        self.cq.message.chat.id = 101

    async def asyncTearDown(self):
        self.db.conn.close()

    async def test_profile_hides_referrals_and_keeps_contact_buttons(self):
        await self.messages["my_profile"](self.msg)
        keyboard = self.bot.send_message.await_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data for row in keyboard.inline_keyboard for button in row
        ]
        self.assertEqual(callbacks, ["profile:cdek", "profile:req"])

    async def test_old_referral_button_does_not_open_statistics(self):
        await self.callbacks["my_referrals_inline"](self.cq)
        self.cq.answer.assert_awaited_once_with("Раздел сейчас недоступен.")
        self.bot.get_me.assert_not_awaited()
        self.cq.message.answer.assert_not_awaited()
        self.cq.message.delete.assert_not_awaited()

    async def test_old_withdraw_button_preserves_balance_and_creates_no_request(self):
        await self.callbacks["referral_withdraw"](self.cq)
        self.assertEqual(await self.db.get_referral_balance_cents(101), 1300)
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM referral_withdrawals"
            ).fetchone()[0],
            0,
        )
        self.bot.send_message.assert_not_awaited()
        self.bot.create_forum_topic.assert_not_awaited()
        self.cq.answer.assert_awaited_once_with("Раздел сейчас недоступен.")

    async def test_typing_hidden_section_does_not_open_statistics(self):
        await self.messages["my_referrals"](self.msg)
        self.bot.get_me.assert_not_awaited()
        self.assertEqual(
            self.msg.answer.await_args.args[0], "Раздел сейчас недоступен."
        )

    async def test_section_can_be_restored_with_existing_balance(self):
        self.cfg.REFERRALS_VISIBLE = True
        await self.messages["my_profile"](self.msg)
        keyboard = self.bot.send_message.await_args.kwargs["reply_markup"]
        self.assertIn(
            "profile:referrals",
            [
                button.callback_data
                for row in keyboard.inline_keyboard
                for button in row
            ],
        )
        await self.messages["my_referrals"](self.msg)
        text = self.msg.answer.await_args.args[0]
        self.assertIn("Доход с комиссии: 13.00 руб.", text)
        self.assertIn("https://t.me/test_bot?start=ref_", text)
        self.assertEqual(await self.db.get_referral_balance_cents(101), 1300)


if __name__ == "__main__":
    unittest.main()
