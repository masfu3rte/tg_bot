import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from db import Database


class OfferAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")

    async def asyncTearDown(self):
        self.db.conn.close()

    async def test_new_user_has_no_acceptance_time(self):
        await self.db.add_user(101, "tester", "Tester")
        user = await self.db.get_user(101)
        self.assertFalse(user["offer_accepted"])
        self.assertIsNone(user["offer_first_accepted_at"])

    async def test_first_acceptance_records_current_utc_time(self):
        await self.db.add_user(101, "tester", "Tester")
        before = datetime.now(timezone.utc).replace(microsecond=0)
        await self.db.set_offer_accepted(101, True)
        after = datetime.now(timezone.utc)
        user = await self.db.get_user(101)
        recorded = datetime.strptime(user["offer_first_accepted_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        self.assertLessEqual(before, recorded)
        self.assertLessEqual(recorded, after)
        self.assertTrue(user["offer_accepted"])
        self.assertEqual(user["username"], "tester")

    async def test_acceptance_can_create_user_with_timestamp(self):
        await self.db.set_offer_accepted(101, True)
        user = await self.db.get_user(101)
        self.assertTrue(user["offer_accepted"])
        self.assertIsNotNone(user["offer_first_accepted_at"])

    async def test_repeated_acceptance_and_revocation_preserve_first_time(self):
        original_time = "2020-01-02 03:04:05"
        self.db.conn.execute(
            "INSERT INTO users (user_id, offer_accepted, offer_first_accepted_at) VALUES (101, 1, ?)",
            (original_time,),
        )
        self.db.conn.commit()
        for accepted in (True, False, True):
            await self.db.set_offer_accepted(101, accepted)
            await self.db.add_user(101, "updated", "Updated")
            user = await self.db.get_user(101)
            self.assertEqual(user["offer_accepted"], int(accepted))
            self.assertEqual(user["offer_first_accepted_at"], original_time)

    async def test_rejection_does_not_create_acceptance_time(self):
        await self.db.set_offer_accepted(101, False)
        self.assertIsNone((await self.db.get_user(101))["offer_first_accepted_at"])
        await self.db.set_offer_accepted(101, True)
        self.assertIsNotNone((await self.db.get_user(101))["offer_first_accepted_at"])

    async def test_existing_database_migrates_without_inventing_historical_times(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "legacy.sqlite")
            with sqlite3.connect(path) as conn:
                conn.execute("""
                    CREATE TABLE users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT, full_name TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        offer_accepted INTEGER DEFAULT 0,
                        referral_code TEXT, referrer_id INTEGER,
                        rating_sum INTEGER DEFAULT 0, rating_count INTEGER DEFAULT 0
                    )
                """)
                conn.execute("""
                    INSERT INTO users (user_id, username, offer_accepted, referral_code)
                    VALUES (101, 'existing', 1, 'saved-referral'), (102, 'new', 0, NULL)
                """)
            db = Database(path)
            try:
                user = await db.get_user(101)
                self.assertTrue(user["offer_accepted"])
                self.assertEqual(user["referral_code"], "saved-referral")
                self.assertIsNone(user["offer_first_accepted_at"])
                self.assertIsNone((await db.get_user(102))["offer_first_accepted_at"])
                await db.set_offer_accepted(101, True)
                recorded = (await db.get_user(101))["offer_first_accepted_at"]
                self.assertIsNotNone(recorded)
            finally:
                db.conn.close()
            reopened = Database(path)
            try:
                self.assertEqual((await reopened.get_user(101))["offer_first_accepted_at"], recorded)
                self.assertEqual(reopened.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                reopened.conn.close()


if __name__ == "__main__":
    unittest.main()
