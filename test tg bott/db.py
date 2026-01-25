import sqlite3
from typing import Optional, Dict, Any, Tuple
from datetime import datetime


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        cur = self.conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id        INTEGER PRIMARY KEY,
                username       TEXT,
                full_name      TEXT,
                created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
                offer_accepted INTEGER DEFAULT 0
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cdek_contacts (
                user_id INTEGER PRIMARY KEY,
                fio     TEXT,
                phone   TEXT,
                pvz     TEXT
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS requisites (
                user_id INTEGER PRIMARY KEY,
                fio     TEXT,
                card    TEXT,
                bank    TEXT
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                internal_title      TEXT,
                item_name           TEXT,
                description         TEXT,
                photo_file_id       TEXT,
                status              TEXT DEFAULT 'pending',
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                channel_message_id  INTEGER,
                channel_is_photo    INTEGER DEFAULT 0
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS offers (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id              INTEGER NOT NULL,
                buyer_id                INTEGER NOT NULL,
                price_cents             INTEGER NOT NULL,
                days                    INTEGER NOT NULL,
                condition               INTEGER NOT NULL,
                photo_file_id           TEXT,
                status                  TEXT DEFAULT 'pending',
                created_at              TEXT DEFAULT CURRENT_TIMESTAMP,
                deal_status             INTEGER DEFAULT 0,
                buyer_deposit_status    TEXT DEFAULT 'none',
                seller_deposit_status   TEXT DEFAULT 'none',
                moderation_thread_id    INTEGER,
                track_number            TEXT,
                track_status            TEXT DEFAULT 'none',
                final_payment_status    TEXT DEFAULT 'none'
            )
            """
        )

        self.conn.commit()
        self._ensure_offers_thread_column()

    def _ensure_offers_thread_column(self):
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(offers)")
        columns = {row["name"] for row in cur.fetchall()}
        if "moderation_thread_id" not in columns:
            cur.execute("ALTER TABLE offers ADD COLUMN moderation_thread_id INTEGER")
            self.conn.commit()

    def _row_to_dict(self, row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        return dict(row) if row else None

    # ===== USERS =====

    async def add_user(self, user_id: int, username: Optional[str], full_name: str):
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO users (user_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name
            """,
            (user_id, username, full_name),
        )
        self.conn.commit()

    async def is_offer_accepted(self, user_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT offer_accepted FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return bool(row and row["offer_accepted"])

    async def set_offer_accepted(self, user_id: int, accepted: bool):
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO users (user_id, offer_accepted)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                offer_accepted=excluded.offer_accepted
            """,
            (user_id, int(accepted)),
        )
        self.conn.commit()

    # ===== PROFILE =====

    async def set_cdek_contacts(self, user_id: int, fio: str, phone: str, pvz: str):
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO cdek_contacts (user_id, fio, phone, pvz)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                fio=excluded.fio,
                phone=excluded.phone,
                pvz=excluded.pvz
            """,
            (user_id, fio, phone, pvz),
        )
        self.conn.commit()

    async def set_requisites(self, user_id: int, fio: str, card: str, bank: str):
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO requisites (user_id, fio, card, bank)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                fio=excluded.fio,
                card=excluded.card,
                bank=excluded.bank
            """,
            (user_id, fio, card, bank),
        )
        self.conn.commit()

    async def has_full_profile(self, user_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM cdek_contacts WHERE user_id=?", (user_id,))
        cdek = cur.fetchone()
        cur.execute("SELECT 1 FROM requisites WHERE user_id=?", (user_id,))
        req = cur.fetchone()
        return cdek is not None and req is not None

    async def get_profile_view(self, user_id: int) -> Dict[str, Any]:
        cur = self.conn.cursor()

        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        u = cur.fetchone()

        if u and u["created_at"]:
            try:
                dt = datetime.fromisoformat(u["created_at"].replace("Z", ""))
            except Exception:
                dt = datetime.utcnow()
        else:
            dt = datetime.utcnow()
        created_date = dt.strftime("%d.%m.%Y")

        cur.execute(
            "SELECT COUNT(*) AS cnt FROM requests WHERE user_id=? AND status!='deleted'",
            (user_id,),
        )
        requests_count = cur.fetchone()["cnt"]

        cur.execute(
            "SELECT COUNT(*) AS cnt FROM offers WHERE buyer_id=?",
            (user_id,),
        )
        responses_count = cur.fetchone()["cnt"]

        cur.execute(
            """
            SELECT COALESCE(SUM(price_cents), 0) AS s
            FROM offers
            WHERE (buyer_deposit_status='confirmed'
                   OR seller_deposit_status='confirmed'
                   OR final_payment_status='confirmed')
              AND (buyer_id=? OR request_id IN (
                    SELECT id FROM requests WHERE user_id=?
                  ))
            """,
            (user_id, user_id),
        )
        total_cents = cur.fetchone()["s"]
        deals_sum = total_cents / 100.0

        cur.execute("SELECT * FROM cdek_contacts WHERE user_id=?", (user_id,))
        cdek = self._row_to_dict(cur.fetchone()) or {"fio": None, "phone": None, "pvz": None}

        cur.execute("SELECT * FROM requisites WHERE user_id=?", (user_id,))
        req = self._row_to_dict(cur.fetchone()) or {"fio": None, "card": None, "bank": None}

        return {
            "user_id": user_id,
            "username": u["username"] if u else None,
            "created_date": created_date,
            "requests_count": requests_count,
            "responses_count": responses_count,
            "deals_sum": f"{deals_sum:.2f} руб.",
            "cdek": cdek,
            "req": req,
        }

    # ===== REQUESTS =====

    async def create_request(
        self,
        user_id: int,
        internal_title: str,
        item_name: str,
        description: str,
        photo_file_id: Optional[str],
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO requests (user_id, internal_title, item_name, description, photo_file_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, internal_title, item_name, description, photo_file_id),
        )
        self.conn.commit()
        return cur.lastrowid

    async def get_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM requests WHERE id=?", (request_id,))
        return self._row_to_dict(cur.fetchone())

    async def update_request_fields(
        self,
        request_id: int,
        internal_title: str,
        item_name: str,
        description: str,
    ):
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE requests
               SET internal_title=?,
                   item_name=?,
                   description=?
             WHERE id=?
            """,
            (internal_title, item_name, description, request_id),
        )
        self.conn.commit()

    async def set_request_status(self, request_id: int, status: str):
        cur = self.conn.cursor()
        cur.execute("UPDATE requests SET status=? WHERE id=?", (status, request_id))
        self.conn.commit()

    async def save_request_channel_message(
        self, request_id: int, message_id: int, is_photo: bool
    ):
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE requests
               SET channel_message_id=?,
                   channel_is_photo=?
             WHERE id=?
            """,
            (message_id, int(is_photo), request_id),
        )
        self.conn.commit()

    async def get_first_active_request(self, user_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT * FROM requests
             WHERE user_id=? AND status!='deleted'
             ORDER BY id
             LIMIT 1
            """,
            (user_id,),
        )
        return self._row_to_dict(cur.fetchone())

    async def get_adjacent_active_request(
        self, user_id: int, current_id: int, direction: str
    ) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        if direction == "next":
            cur.execute(
                """
                SELECT * FROM requests
                 WHERE user_id=? AND status!='deleted' AND id>?
                 ORDER BY id
                 LIMIT 1
                """,
                (user_id, current_id),
            )
        else:
            cur.execute(
                """
                SELECT * FROM requests
                 WHERE user_id=? AND status!='deleted' AND id<?
                 ORDER BY id DESC
                 LIMIT 1
                """,
                (user_id, current_id),
            )
        return self._row_to_dict(cur.fetchone())

    async def get_active_request_index_and_total(
        self, user_id: int, request_id: int
    ) -> Tuple[int, int]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id FROM requests WHERE user_id=? AND status!='deleted' ORDER BY id",
            (user_id,),
        )
        rows = cur.fetchall()
        ids = [r["id"] for r in rows]
        total = len(ids)
        index = ids.index(request_id) + 1 if request_id in ids else 1
        return index, total

    async def get_accepted_offer_for_request(
        self, request_id: int
    ) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT *
              FROM offers
             WHERE request_id=?
               AND status='approved'
             ORDER BY id DESC
             LIMIT 1
            """,
            (request_id,),
        )
        return self._row_to_dict(cur.fetchone())

    async def has_deal_for_request(self, request_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT 1
              FROM offers
             WHERE request_id=?
               AND status='approved'
             LIMIT 1
            """,
            (request_id,),
        )
        return cur.fetchone() is not None

    # ===== OFFERS =====

    async def create_offer(
        self,
        request_id: int,
        buyer_id: int,
        price_cents: int,
        days: int,
        condition: int,
        photo_file_id: str,
    ) -> int:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO offers
                (request_id, buyer_id, price_cents, days, condition, photo_file_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (request_id, buyer_id, price_cents, days, condition, photo_file_id),
        )
        self.conn.commit()
        return cur.lastrowid

    async def get_offer(self, offer_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM offers WHERE id=?", (offer_id,))
        return self._row_to_dict(cur.fetchone())

    async def set_offer_status(self, offer_id: int, status: str):
        cur = self.conn.cursor()
        cur.execute("UPDATE offers SET status=? WHERE id=?", (status, offer_id))
        self.conn.commit()

    async def set_offer_moderation_thread_id(self, offer_id: int, thread_id: int):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE offers SET moderation_thread_id=? WHERE id=?",
            (thread_id, offer_id),
        )
        self.conn.commit()

    async def set_buyer_deposit_status(self, offer_id: int, status: str):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE offers SET buyer_deposit_status=? WHERE id=?",
            (status, offer_id),
        )
        self.conn.commit()

    async def set_seller_deposit_status(self, offer_id: int, status: str):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE offers SET seller_deposit_status=? WHERE id=?",
            (status, offer_id),
        )
        self.conn.commit()

    async def set_deal_status(self, offer_id: int, deal_status: int):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE offers SET deal_status=? WHERE id=?",
            (deal_status, offer_id),
        )
        self.conn.commit()

    async def set_track_info(
        self, offer_id: int, track_number: str, status: str = "pending"
    ):
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE offers
               SET track_number=?,
                   track_status=?
             WHERE id=?
            """,
            (track_number, status, offer_id),
        )
        self.conn.commit()

    async def set_track_status(self, offer_id: int, status: str):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE offers SET track_status=? WHERE id=?",
            (status, offer_id),
        )
        self.conn.commit()

    async def set_final_payment_status(self, offer_id: int, status: str):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE offers SET final_payment_status=? WHERE id=?",
            (status, offer_id),
        )
        self.conn.commit()

    async def get_first_offer_for_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT * FROM offers
             WHERE buyer_id=?
             ORDER BY id
             LIMIT 1
            """,
            (user_id,),
        )
        return self._row_to_dict(cur.fetchone())

    async def get_adjacent_offer_for_user(
        self, buyer_id: int, current_id: int, direction: str
    ) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        if direction == "next":
            cur.execute(
                """
                SELECT * FROM offers
                 WHERE buyer_id=? AND id>?
                 ORDER BY id
                 LIMIT 1
                """,
                (buyer_id, current_id),
            )
        else:
            cur.execute(
                """
                SELECT * FROM offers
                 WHERE buyer_id=? AND id<?
                 ORDER BY id DESC
                 LIMIT 1
                """,
                (buyer_id, current_id),
            )
        return self._row_to_dict(cur.fetchone())

    async def get_offer_index_and_total(
        self, buyer_id: int, offer_id: int
    ) -> Tuple[int, int]:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id FROM offers WHERE buyer_id=? ORDER BY id",
            (buyer_id,),
        )
        rows = cur.fetchall()
        ids = [r["id"] for r in rows]
        total = len(ids)
        index = ids.index(offer_id) + 1 if offer_id in ids else 1
        return index, total
