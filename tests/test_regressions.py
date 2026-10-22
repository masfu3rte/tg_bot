from contextlib import closing

"""Regression tests exercise real aiogram dispatch with a network-free Telegram session."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.methods import (
    GetMe,
    GetChatMember,
    CreateForumTopic,
    AnswerCallbackQuery,
    DeleteMessage,
    SendMediaGroup,
)
from aiogram.types import (
    User,
    Chat,
    Message,
    CallbackQuery,
    Update,
    PhotoSize,
    ForumTopic,
    ChatMemberMember,
)

from access import BUYERS, SELLERS, MODERATORS, REQUEST_OWNERS, PUBLIC
from config import load_config
from db import Database
from delivery import content, queue_batch, drain, chunks
from money import parse_price, amounts
from navigation import NavigationMiddleware
from storage import SQLiteStorage
from states import RequestCreate, OfferCreate
from handlers_start_help import setup_start_help_handlers
from handlers_profile import setup_profile_handlers
from handlers_requests import setup_requests_handlers
from handlers_offers_deals import setup_offers_deals_handlers
from handlers_sliders import setup_sliders_handlers
from handlers_reports import setup_reports_handlers


class OfflineTelegram(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.fail_chats = set()
        self.next_id = 100

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise AssertionError("Network access forbidden")
        yield

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if getattr(method, "chat_id", None) in self.fail_chats:
            raise TelegramNetworkError(
                method=method, message="offline simulated failure"
            )
        text = getattr(method, "caption", None) or getattr(method, "text", None) or ""
        if len(text) > 4096 or (getattr(method, "caption", None) and len(text) > 1024):
            raise AssertionError("Telegram length limit exceeded")
        if "<XL>" in text:
            raise AssertionError("User HTML was not escaped")
        if isinstance(method, GetMe):
            return User(
                id=123456789, is_bot=True, first_name="Offline", username="offline_bot"
            )
        if isinstance(method, GetChatMember):
            return ChatMemberMember(
                user=User(id=303, is_bot=False, first_name="Member"), status="member"
            )
        if isinstance(method, CreateForumTopic):
            return ForumTopic(message_thread_id=11, name="Offline", icon_color=0)
        if isinstance(method, (AnswerCallbackQuery, DeleteMessage)):
            return True
        self.next_id += 1
        msg = Message(
            message_id=self.next_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=getattr(method, "chat_id", 101), type="private"),
        )
        return [msg, msg] if isinstance(method, SendMediaGroup) else msg


class BotRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        for uid in (101, 202, 303, 404):
            await self.db.add_user(uid, f"user{uid}", "Test User")
            await self.db.set_offer_accepted(uid, True)
            await self.db.set_cdek_contacts(uid, "Test User", "123456789", "Office")
            await self.db.set_requisites(uid, "Test User", "123456789", "Test Bank")
        self.rid = await self.db.create_request(
            101, "Private title", "Jacket", "Description", "photo"
        )
        await self.db.set_request_status(self.rid, "approved")
        self.oid = await self.db.create_offer(self.rid, 202, 100000, 7, 5, "photo")
        await self.db.set_offer_status(self.oid, "approved")
        await self.db.set_offer_moderation_thread_id(self.oid, 11)
        self.cfg = NS(
            MODERATION_CHAT_ID=-100123,
            MODERATION_TOPIC_ID=11,
            REPORTS_TOPIC_ID=12,
            MODERATOR_IDS=(303,),
            REQUESTS_PUBLIC_CHANNEL_ID=-100456,
            ASSETS_CHANNEL_ID=None,
            START_BANNER_ID=None,
            MY_REQUESTS_BANNER_MESSAGE_ID=None,
            REQUEST_SENT_BANNER_MESSAGE_ID=None,
            MY_OFFERS_BANNER_MESSAGE_ID=None,
            PROFILE_BANNER_MESSAGE_ID=None,
            REFERRALS_VISIBLE=False,
            MANAGER_REQUISITES_TEXT="Manager\nAccount\nBank",
            MANAGER_CDEK_CONTACT_TEXT="Manager\nPhone\nOffice",
            SUPPORT_USERNAME="someoutmanager",
            OFFER_URL="https://example.org/offer",
            PRIVACY_POLICY_URL="https://example.org/privacy",
            CHANNEL_URL="https://t.me/someout",
            SUPPORT_URL="https://t.me/someoutmanager",
            REQUESTS_CHANNEL_URL="https://t.me/someout",
            ADS_URL="",
        )
        self.session = OfflineTelegram()
        self.bot = Bot(token="123456789:offline_tests_only", session=self.session)
        self.dp = Dispatcher(
            storage=SQLiteStorage(":memory:"), events_isolation=SimpleEventIsolation()
        )
        self.dp.message.outer_middleware(NavigationMiddleware())
        self.dp.callback_query.outer_middleware(NavigationMiddleware())
        for setup in (
            setup_start_help_handlers,
            setup_profile_handlers,
            setup_requests_handlers,
            setup_offers_deals_handlers,
            setup_sliders_handlers,
            setup_reports_handlers,
        ):
            setup(self.dp, self.db, self.cfg)
        self.update_id = 0

    async def asyncTearDown(self):
        self.db.conn.close()
        await self.dp.storage.close()
        await self.bot.session.close()

    def state(self, uid=202, chat=None):
        return self.dp.fsm.get_context(bot=self.bot, user_id=uid, chat_id=chat or uid)

    async def message(self, text=None, uid=202, chat=None, photo=False):
        self.update_id += 1
        msg = Message(
            message_id=self.update_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat or uid, type="private" if chat is None else "supergroup"),
            from_user=User(
                id=uid, is_bot=False, first_name="Tester", username=f"user{uid}"
            ),
            text=text,
            photo=(
                [
                    PhotoSize(
                        file_id="photo", file_unique_id="unique", width=100, height=100
                    )
                ]
                if photo
                else None
            ),
        )
        return await self.dp.feed_update(
            self.bot, Update(update_id=self.update_id, message=msg)
        )

    async def callback(self, data, uid=101, chat=None):
        self.update_id += 1
        msg = Message(
            message_id=self.update_id,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat or uid, type="private" if chat is None else "supergroup"),
            from_user=User(id=123456789, is_bot=True, first_name="Bot"),
        )
        cq = CallbackQuery(
            id=str(self.update_id),
            from_user=User(
                id=uid, is_bot=False, first_name="Tester", username=f"user{uid}"
            ),
            chat_instance="offline",
            message=msg,
            data=data,
        )
        return await self.dp.feed_update(
            self.bot, Update(update_id=self.update_id, callback_query=cq)
        )

    async def moderator(self, data):
        return await self.callback(data, 303, self.cfg.MODERATION_CHAT_ID)

    async def deposits(self):
        await self.callback(f"offer:buyer_accept:{self.oid}")
        for side, uid in (("buyer", 101), ("seller", 202)):
            await self.callback(f"deal:paid:{side}:{self.oid}", uid)
            await self.moderator(f"deal:confirm:{side}:{self.oid}:1")

    async def final_payment(self):
        await self.deposits()
        await self.callback(f"deal:status_pick:{self.oid}:6", 202)
        await self.message("1234567890")
        await self.moderator(f"deal:confirm:track:{self.oid}")
        await self.moderator(f"deal:arrival_accept:{self.oid}")
        await self.message(uid=303, chat=-100123, photo=True)
        await self.callback(f"deal:buyer_accept:{self.oid}")
        await self.callback(f"deal:final_paid:{self.oid}")
        await self.moderator(f"deal:confirm:final:{self.oid}:1")

    async def test_full_workflow_and_delivery(self):
        await self.final_payment()
        self.assertEqual(
            (await self.db.get_offer(self.oid))["final_status"], "completed"
        )
        await self.moderator(f"deal:funds_sent:{self.oid}")
        await self.callback(f"deal:delivery:cdek:{self.oid}")
        await self.moderator(f"deal:cdek_sent:{self.oid}")
        await self.message("999888777", 303, -100123)
        offer = await self.db.get_offer(self.oid)
        self.assertEqual(offer["manager_track_number"], "999888777")
        self.assertEqual(offer["funds_sent"], 1)

    async def test_outsider_cannot_confirm_payment(self):
        await self.callback(f"deal:confirm:final:{self.oid}:0", 404)
        self.assertEqual(
            (await self.db.get_offer(self.oid))["final_payment_status"], "none"
        )

    async def test_moderator_cannot_confirm_from_private_chat(self):
        await self.callback(f"deal:confirm:final:{self.oid}:0", 303)
        self.assertEqual(
            (await self.db.get_offer(self.oid))["final_payment_status"], "none"
        )

    async def test_wrong_payment_side_rejected(self):
        await self.callback(f"offer:buyer_accept:{self.oid}")
        await self.callback(f"deal:paid:buyer:{self.oid}", 202)
        self.assertEqual(
            (await self.db.get_offer(self.oid))["buyer_deposit_status"], "none"
        )

    async def test_foreign_navigation_has_no_private_data(self):
        await self.callback(f"ur:next:{self.rid}", 404)
        self.assertFalse(
            any(
                "Private title" in (getattr(c, "text", None) or "")
                for c in self.session.calls
            )
        )

    async def test_confirmed_deposit_is_immutable(self):
        await self.deposits()
        await self.callback(f"deal:paid:buyer:{self.oid}")
        self.assertEqual(
            (await self.db.get_offer(self.oid))["buyer_deposit_status"], "confirmed"
        )

    async def test_duplicate_final_confirmation_never_recredits(self):
        await self.db.set_referrer(101, 404)
        await self.final_payment()
        balance = await self.db.get_referral_balance_cents(404)
        self.assertEqual(balance, 1300)
        await self.callback(f"deal:final_paid:{self.oid}")
        await self.moderator(f"deal:confirm:final:{self.oid}:1")
        self.assertEqual(await self.db.get_referral_balance_cents(404), balance)
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM referral_credits").fetchone()[0],
            1,
        )

    async def test_rejected_payment_old_moderation_button_is_stale(self):
        await self.callback(f"offer:buyer_accept:{self.oid}")
        await self.callback(f"deal:paid:buyer:{self.oid}")
        await self.moderator(f"deal:cancel:buyer:{self.oid}:1")
        await self.callback(f"deal:paid:buyer:{self.oid}")
        await self.moderator(f"deal:confirm:buyer:{self.oid}:1")
        self.assertEqual(
            (await self.db.get_offer(self.oid))["buyer_deposit_status"], "pending"
        )
        await self.moderator(f"deal:confirm:buyer:{self.oid}:2")
        self.assertEqual(
            (await self.db.get_offer(self.oid))["buyer_deposit_status"], "confirmed"
        )

    async def test_accepted_deal_blocks_deletion(self):
        await self.callback(f"offer:buyer_accept:{self.oid}")
        await self.callback(f"ur:del:{self.rid}")
        self.assertEqual((await self.db.get_request(self.rid))["status"], "approved")

    async def test_completed_deal_cannot_be_cancelled(self):
        await self.final_payment()
        await self.callback(f"deal:close:{self.oid}", 202)
        self.assertEqual(
            (await self.db.get_offer(self.oid))["final_status"], "completed"
        )

    async def test_accepted_offer_cannot_be_rejected(self):
        await self.deposits()
        await self.callback(f"offer:buyer_reject:{self.oid}")
        self.assertEqual(
            (await self.db.get_offer(self.oid))["status"], "buyer_accepted"
        )

    async def test_unpaid_shipping_stage_rejected(self):
        await self.callback(f"deal:status_pick:{self.oid}:6", 202)
        self.assertEqual((await self.db.get_offer(self.oid))["deal_status"], 0)

    async def test_nontext_title_keeps_form(self):
        await self.callback("requests:new", 202)
        await self.message(photo=True)
        self.assertEqual(
            await self.state().get_state(),
            RequestCreate.waiting_for_internal_title.state,
        )

    async def test_stale_skip_photo_has_no_effect(self):
        await self.callback("request:skip_photo")
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 1
        )

    async def test_nan_price_rejected_before_photo(self):
        await self.state().set_state(OfferCreate.waiting_for_price)
        await self.message("nan")
        self.assertEqual(
            await self.state().get_state(), OfferCreate.waiting_for_price.state
        )

    async def test_excessive_days_rejected(self):
        await self.state().set_state(OfferCreate.waiting_for_days)
        await self.message("9999999999999999999999")
        self.assertEqual(
            await self.state().get_state(), OfferCreate.waiting_for_days.state
        )

    async def test_navigation_exits_form(self):
        await self.state().set_state(RequestCreate.waiting_for_internal_title)
        await self.message("📮 Мои отклики")
        self.assertIsNone(await self.state().get_state())
        self.assertNotIn("internal_title", await self.state().get_data())

    async def test_cancel_clears_form(self):
        await self.state().set_state(RequestCreate.waiting_for_description)
        await self.message("/cancel")
        self.assertIsNone(await self.state().get_state())

    async def test_unpaid_card_does_not_claim_deposits_paid(self):
        await self.message("Активные запросы", 101)
        text = "\n".join(
            (getattr(c, "caption", None) or getattr(c, "text", None) or "")
            for c in self.session.calls
        )
        self.assertNotIn("Статус сделки: Внесены залоги", text)
        self.assertIn("Ожидается внесение залогов", text)

    async def test_completed_records_are_archived(self):
        await self.final_payment()
        self.assertIsNone(await self.db.get_first_offer_for_user(202))
        self.assertIsNone(await self.db.get_first_active_request(101))
        await self.callback("offers:archive", 202)
        self.assertTrue(
            any(
                "Архив откликов" in (getattr(c, "text", None) or "")
                for c in self.session.calls
            )
        )

    async def test_own_request_deep_link_rejected(self):
        await self.message(f"/start req_{self.rid}", 101)
        self.assertIsNone(await self.state(101).get_state())

    async def test_pending_request_deep_link_rejected(self):
        await self.db.set_request_status(self.rid, "pending")
        await self.message(f"/start req_{self.rid}")
        self.assertIsNone(await self.state().get_state())

    async def test_terms_required_for_request_creation(self):
        await self.db.set_offer_accepted(202, False)
        await self.callback("requests:new", 202)
        self.assertIsNone(await self.state().get_state())

    async def test_photo_cannot_be_saved_as_track(self):
        await self.deposits()
        await self.callback(f"deal:status_pick:{self.oid}:6", 202)
        await self.message(photo=True)
        self.assertIsNone((await self.db.get_offer(self.oid))["track_number"])

    async def test_long_html_request_is_delivered_without_oversized_caption(self):
        await self.callback("requests:new", 202)
        for text in ("Private", "Jacket <XL>", "x" * 1500):
            await self.message(text)
        await self.message(photo=True)
        self.assertTrue(
            any(
                "&lt;XL&gt;" in (getattr(c, "text", None) or "")
                for c in self.session.calls
            )
        )
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0], 2
        )

    async def test_moderation_delivery_failure_is_queued(self):
        await self.callback("requests:new", 202)
        for text in ("Title", "Jacket", "Description"):
            await self.message(text)
        self.session.fail_chats.add(-100123)
        await self.message(photo=True)
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE status='pending'"
            ).fetchone()[0],
            1,
        )
        self.session.fail_chats.clear()
        self.db.conn.execute("UPDATE outbox SET next_attempt=0")
        self.db.conn.commit()
        await drain(self.db, self.bot)
        self.assertEqual(
            self.db.conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE status='pending'"
            ).fetchone()[0],
            0,
        )

    async def test_publication_failure_not_marked_approved(self):
        await self.db.set_request_status(self.rid, "pending")
        self.session.fail_chats.add(-100456)
        await self.moderator(f"request:approve:{self.rid}:0")
        req = await self.db.get_request(self.rid)
        self.assertEqual(req["status"], "publishing")
        self.assertIsNone(req["channel_message_id"])
        self.session.fail_chats.clear()
        self.db.conn.execute("UPDATE outbox SET next_attempt=0")
        self.db.conn.commit()
        await drain(self.db, self.bot)
        self.assertEqual((await self.db.get_request(self.rid))["status"], "approved")

    async def test_duplicate_approval_does_not_publish_twice(self):
        await self.db.set_request_status(self.rid, "pending")
        await self.moderator(f"request:approve:{self.rid}:0")
        await self.moderator(f"request:approve:{self.rid}:0")
        posts = [
            c for c in self.session.calls if getattr(c, "chat_id", None) == -100456
        ]
        self.assertEqual(len(posts), 1)

    async def test_edit_form_saves_actual_change(self):
        await self.db.set_offer_status(self.oid, "rejected")
        await self.callback(f"ur:edit:{self.rid}")
        await self.callback("request:edit_field:item_name")
        await self.message("New jacket", 101)
        req = await self.db.get_request(self.rid)
        self.assertEqual(
            (req["item_name"], req["status"], req["revision"]),
            ("New jacket", "pending", 1),
        )
        await self.moderator(f"request:approve:{self.rid}:0")
        self.assertEqual((await self.db.get_request(self.rid))["status"], "pending")
        await self.moderator(f"request:approve:{self.rid}:1")
        self.assertEqual((await self.db.get_request(self.rid))["status"], "approved")

    async def test_noop_counter_is_answered(self):
        await self.callback(f"ur:noop:{self.rid}")
        self.assertIsInstance(self.session.calls[-1], AnswerCallbackQuery)

    async def test_all_callbacks_have_explicit_access_classification(self):
        allowed = (
            BUYERS | SELLERS | MODERATORS | REQUEST_OWNERS | PUBLIC | {"deal_paid"}
        )
        self.assertFalse(
            {obj.callback.__name__ for obj in self.dp.callback_query.handlers} - allowed
        )

    async def test_deposit_instruction_preserves_kopecks(self):
        await self.callback(f"offer:buyer_accept:{self.oid}")
        self.assertTrue(
            any(
                "282.50₽" in (getattr(c, "text", None) or "")
                for c in self.session.calls
            )
        )

    async def test_callback_back_clears_form(self):
        await self.state(101).set_state(RequestCreate.waiting_for_photo)
        await self.callback("requests:back")
        self.assertIsNone(await self.state(101).get_state())

    async def test_default_admin_policy_rejects_regular_group_member(self):
        self.cfg.MODERATOR_IDS = ()
        await self.db.set_request_status(self.rid, "pending")
        await self.moderator(f"request:approve:{self.rid}:0")
        self.assertEqual((await self.db.get_request(self.rid))["status"], "pending")

    async def test_cannot_edit_while_publication_is_pending(self):
        await self.db.set_offer_status(self.oid, "rejected")
        await self.callback(f"ur:edit:{self.rid}")
        await self.callback("request:edit_field:item_name")
        await self.db.set_request_status(self.rid, "publishing")
        await self.message("Racing edit", 101)
        self.assertEqual((await self.db.get_request(self.rid))["item_name"], "Jacket")

    async def test_personal_title_keeps_moderation_revision(self):
        await self.db.set_offer_status(self.oid, "rejected")
        await self.db.set_request_status(self.rid, "pending")
        await self.callback(f"ur:edit:{self.rid}")
        await self.callback("request:edit_field:internal_title")
        await self.message("New private title", 101)
        req = await self.db.get_request(self.rid)
        self.assertEqual(
            (req["internal_title"], req["revision"]), ("New private title", 0)
        )
        await self.moderator(f"request:approve:{self.rid}:0")
        self.assertEqual((await self.db.get_request(self.rid))["status"], "approved")

    async def test_failed_arrival_photo_does_not_enable_buyer_acceptance(self):
        await self.deposits()
        await self.callback(f"deal:status_pick:{self.oid}:6", 202)
        await self.message("1234567890")
        await self.moderator(f"deal:confirm:track:{self.oid}")
        await self.moderator(f"deal:arrival_accept:{self.oid}")
        self.session.fail_chats.add(101)
        await self.message(uid=303, chat=-100123, photo=True)
        self.assertEqual((await self.db.get_offer(self.oid))["arrival_notified"], 0)
        await self.callback(f"deal:buyer_accept:{self.oid}")
        self.assertEqual((await self.db.get_offer(self.oid))["buyer_received"], 0)

    async def test_paid_cancellation_requires_moderator_resolution(self):
        await self.deposits()
        await self.callback(f"deal:close:{self.oid}", 202)
        self.assertEqual((await self.db.get_offer(self.oid))["status"], "dispute")
        await self.message(f"/resolve_cancel {self.oid}", 404)
        self.assertEqual((await self.db.get_offer(self.oid))["status"], "dispute")
        await self.message(f"/resolve_cancel {self.oid}", 303, -100123)
        self.assertEqual(
            (await self.db.get_offer(self.oid))["final_status"], "cancelled"
        )


class PersistenceAndConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_fsm_survives_reopening_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "state.sqlite")
            key = StorageKey(bot_id=1, chat_id=2, user_id=2)
            first = SQLiteStorage(path)
            await first.set_state(key, RequestCreate.waiting_for_photo)
            await first.set_data(key, {"item_name": "Saved"})
            await first.close()
            second = SQLiteStorage(path)
            self.assertEqual(
                await second.get_state(key), RequestCreate.waiting_for_photo.state
            )
            self.assertEqual(await second.get_data(key), {"item_name": "Saved"})
            await second.close()
            self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)

    async def test_outbox_survives_reopening_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "bot.db")
            db = Database(path)
            session = OfflineTelegram()
            session.fail_chats.add(101)
            bot = Bot(token="123456789:offline_persistence", session=session)
            self.assertFalse(
                await queue_batch(db, bot, "test", content(101, "Saved notification"))
            )
            db.conn.close()
            db = Database(path)
            session.fail_chats.clear()
            db.conn.execute("UPDATE outbox SET next_attempt=0")
            db.conn.commit()
            await drain(db, bot)
            self.assertEqual(
                db.conn.execute("SELECT status FROM outbox").fetchone()[0], "sent"
            )
            db.conn.close()
            await bot.session.close()

    async def test_invalid_environment_fails_with_line_number(self):
        with tempfile.TemporaryDirectory() as folder:
            env = Path(folder) / ".env"
            env.write_text("BOT_TOKEN=test\nThis is invalid\n")
            with patch.dict(os.environ, {"ENV_FILE": str(env)}):
                with self.assertRaisesRegex(RuntimeError, "2"):
                    load_config()

    async def test_decimal_validation_and_financial_identity(self):
        for value in ("nan", "inf", "-1", "0", "0.001", "100000001", "not a price"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_price(value)
        self.assertEqual(parse_price("12,34"), 1234)
        for cents in (1, 10, 99, 100, 100001, 99999999):
            money = amounts(cents)
            self.assertEqual(
                money["buyer_deposit"] + money["remainder"], money["total"]
            )
            self.assertEqual(money["base"] + money["fee"], money["total"])

    async def test_html_chunks_stay_bounded(self):
        blocks = chunks("<b>" + ("Товар &amp; описание " * 1000) + "</b>")
        self.assertGreater(len(blocks), 1)
        self.assertTrue(all(len(block) <= 3500 for block in blocks))
        self.assertTrue(
            all(block.startswith("<b>") and block.endswith("</b>") for block in blocks)
        )

    async def test_migration_preserves_unknown_columns(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "bot.db")
            db = Database(path)
            db.conn.execute("ALTER TABLE users ADD COLUMN legacy_value TEXT")
            db.conn.execute("INSERT INTO users(user_id,legacy_value) VALUES (1,'keep')")
            db.conn.commit()
            db.conn.close()
            db = Database(path)
            self.assertEqual(
                db.conn.execute("SELECT legacy_value FROM users").fetchone()[0], "keep"
            )
            db.conn.close()


class LocalSnapshotTests(unittest.TestCase):
    def test_local_snapshot_restores_and_has_private_permissions(self):
        import gzip
        from local_backups import local_backup_once

        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.sqlite"
            with closing(sqlite3.connect(source)) as db, db:
                db.execute("CREATE TABLE sample(value TEXT)")
                db.execute("INSERT INTO sample VALUES ('saved')")
            copy = local_backup_once(source, Path(folder) / "copies")
            restored = Path(folder) / "restored.sqlite"
            restored.write_bytes(gzip.decompress(copy.read_bytes()))
            with closing(sqlite3.connect(restored)) as db, db:
                self.assertEqual(
                    db.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
                self.assertEqual(
                    db.execute("SELECT value FROM sample").fetchone()[0], "saved"
                )
            self.assertEqual(copy.stat().st_mode & 0o777, 0o600)
            self.assertEqual(copy.parent.stat().st_mode & 0o777, 0o700)

    def test_missing_source_does_not_create_empty_backup(self):
        from local_backups import local_backup_once
        from backups import BackupError

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(BackupError):
                local_backup_once(
                    Path(folder) / "missing.sqlite", Path(folder) / "copies"
                )
            self.assertEqual(list((Path(folder) / "copies").glob("*.gz")), [])


if __name__ == "__main__":
    unittest.main()
