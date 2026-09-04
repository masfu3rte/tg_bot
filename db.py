import sqlite3
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
import secrets
import string


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        schemas = {
            "users": [
                ("user_id", "INTEGER PRIMARY KEY"),
                ("username", "TEXT"),
                ("full_name", "TEXT"),
                ("created_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
                ("offer_accepted", "INTEGER DEFAULT 0"),
                ("referral_code", "TEXT"),
                ("referrer_id", "INTEGER"),
                ("rating_sum", "INTEGER DEFAULT 0"),
                ("rating_count", "INTEGER DEFAULT 0"),
            ],
            "cdek_contacts": [
                ("user_id", "INTEGER PRIMARY KEY"),
                ("fio", "TEXT"),
                ("phone", "TEXT"),
                ("pvz", "TEXT"),
            ],
            "requisites": [
                ("user_id", "INTEGER PRIMARY KEY"),
                ("fio", "TEXT"),
                ("card", "TEXT"),
                ("bank", "TEXT"),
            ],
            "referral_balances": [
                ("user_id", "INTEGER PRIMARY KEY"),
                ("balance_cents", "INTEGER NOT NULL DEFAULT 0"),
            ],
            "referral_withdrawals": [
                ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
                ("user_id", "INTEGER NOT NULL"),
                ("amount_cents", "INTEGER NOT NULL"),
                ("status", "TEXT DEFAULT 'pending'"),
                ("created_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
            ],
            "reports_sent": [
                ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
                ("report_type", "TEXT NOT NULL"),
                ("period_start", "TEXT"),
                ("period_end", "TEXT"),
                ("sent_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
            ],
            "requests": [
                ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
                ("user_id", "INTEGER NOT NULL"),
                ("internal_title", "TEXT"),
                ("item_name", "TEXT"),
                ("description", "TEXT"),
                ("photo_file_id", "TEXT"),
                ("status", "TEXT DEFAULT 'pending'"),
                ("created_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
                ("channel_message_id", "INTEGER"),
                ("channel_is_photo", "INTEGER DEFAULT 0"),
            ],
            "offers": [
                ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
                ("request_id", "INTEGER NOT NULL"),
                ("buyer_id", "INTEGER NOT NULL"),
                ("price_cents", "INTEGER NOT NULL"),
                ("days", "INTEGER NOT NULL"),
                ("condition", "INTEGER NOT NULL"),
                ("photo_file_id", "TEXT"),
                ("status", "TEXT DEFAULT 'pending'"),
                ("created_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
                ("deal_status", "INTEGER DEFAULT 0"),
                ("buyer_deposit_status", "TEXT DEFAULT 'none'"),
                ("seller_deposit_status", "TEXT DEFAULT 'none'"),
                ("moderation_thread_id", "INTEGER"),
                ("track_number", "TEXT"),
                ("track_status", "TEXT DEFAULT 'none'"),
                ("final_payment_status", "TEXT DEFAULT 'none'"),
                ("manager_track_number", "TEXT"),
                ("delivery_method", "TEXT"),
                ("seller_rating", "INTEGER"),
                # Deal-reporting fields.  ``buyer_id`` above is the author of the
                # offer (the seller), so the actual buyer is stored explicitly.
                ("deal_buyer_id", "INTEGER"),
                ("seller_id", "INTEGER"),
                ("buyer_username", "TEXT"),
                ("seller_username", "TEXT"),
                ("amount_cents", "INTEGER"),
                ("service_fee_cents", "INTEGER"),
                ("seller_payout_cents", "INTEGER"),
                ("paid_at", "TEXT"),
                ("completed_at", "TEXT"),
                ("cancelled_at", "TEXT"),
                ("final_status", "TEXT"),
                ("is_disputed", "INTEGER NOT NULL DEFAULT 0"),
                ("dispute_result", "TEXT"),
            ],
        }

        for table_name, columns in schemas.items():
            self._ensure_table_schema(table_name, columns)

        self._backfill_deal_statistics()

    def _backfill_deal_statistics(self) -> None:
        """Populate reporting fields for deals created before this migration."""
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE offers
               SET deal_buyer_id = COALESCE(
                       deal_buyer_id,
                       (SELECT user_id FROM requests WHERE requests.id=offers.request_id)
                   ),
                   seller_id = COALESCE(seller_id, buyer_id),
                   buyer_username = COALESCE(
                       buyer_username,
                       (SELECT username FROM users WHERE users.user_id=(
                           SELECT user_id FROM requests WHERE requests.id=offers.request_id
                       ))
                   ),
                   seller_username = COALESCE(
                       seller_username,
                       (SELECT username FROM users WHERE users.user_id=offers.buyer_id)
                   ),
                   service_fee_cents = COALESCE(service_fee_cents, ROUND(price_cents * 0.07)),
                   seller_payout_cents = COALESCE(seller_payout_cents, price_cents),
                   amount_cents = COALESCE(
                       amount_cents, price_cents + ROUND(price_cents * 0.07)
                   ),
                   paid_at = CASE
                       WHEN paid_at IS NULL AND buyer_deposit_status='confirmed'
                       THEN created_at ELSE paid_at END,
                   completed_at = CASE
                       WHEN completed_at IS NULL AND final_payment_status='confirmed'
                       THEN created_at ELSE completed_at END,
                   final_status = COALESCE(
                       final_status,
                       CASE
                           WHEN final_payment_status='confirmed' THEN 'completed'
                           WHEN status='closed_by_seller' THEN 'cancelled'
                           WHEN status='buyer_rejected' THEN 'cancelled'
                           WHEN status='dispute' THEN 'dispute'
                           ELSE status
                       END
                   ),
                   is_disputed = CASE WHEN status='dispute' THEN 1 ELSE is_disputed END
             WHERE status IN (
                       'approved', 'buyer_accepted', 'buyer_rejected',
                       'dispute', 'closed_by_seller'
                   )
                OR buyer_deposit_status='confirmed'
                OR seller_deposit_status='confirmed'
                OR final_payment_status='confirmed'
            """
        )
        self.conn.commit()

    def _ensure_table_schema(self, table_name: str, columns: list[tuple[str, str]]) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        exists = cur.fetchone() is not None
        column_defs = [f"{name} {definition}" for name, definition in columns]
        if not exists:
            cur.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(column_defs)})")
            self.conn.commit()
            return

        cur.execute(f"PRAGMA table_info({table_name})")
        existing_columns = [row["name"] for row in cur.fetchall()]
        desired_columns = [name for name, _ in columns]
        missing = [name for name in desired_columns if name not in existing_columns]
        extra = [name for name in existing_columns if name not in desired_columns]

        if extra:
            self._recreate_table(table_name, columns, existing_columns)
            return

        for name in missing:
            definition = dict(columns)[name]
            cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")
        self.conn.commit()

    def _recreate_table(
        self,
        table_name: str,
        columns: list[tuple[str, str]],
        existing_columns: list[str],
    ) -> None:
        cur = self.conn.cursor()
        temp_table = f"{table_name}_new"
        column_defs = [f"{name} {definition}" for name, definition in columns]
        cur.execute(f"DROP TABLE IF EXISTS {temp_table}")
        cur.execute(f"CREATE TABLE {temp_table} ({', '.join(column_defs)})")

        desired_columns = [name for name, _ in columns]
        common_columns = [name for name in desired_columns if name in existing_columns]
        if common_columns:
            cols = ", ".join(common_columns)
            cur.execute(
                f"INSERT INTO {temp_table} ({cols}) SELECT {cols} FROM {table_name}"
            )

        cur.execute(f"DROP TABLE {table_name}")
        cur.execute(f"ALTER TABLE {temp_table} RENAME TO {table_name}")
        self.conn.commit()

    def _row_to_dict(self, row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        return dict(row) if row else None

    # ===== USERS =====

    async def add_user(self, user_id: int, username: Optional[str], full_name: str) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
        is_new = cur.fetchone() is None
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
        if is_new:
            await self.ensure_referral_code(user_id)
        return is_new

    def _generate_referral_code(self, length: int = 8) -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    async def ensure_referral_code(self, user_id: int) -> str:
        cur = self.conn.cursor()
        cur.execute("SELECT referral_code FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if row and row["referral_code"]:
            return row["referral_code"]

        code = self._generate_referral_code()
        cur.execute("SELECT 1 FROM users WHERE referral_code=?", (code,))
        while cur.fetchone() is not None:
            code = self._generate_referral_code()
            cur.execute("SELECT 1 FROM users WHERE referral_code=?", (code,))

        cur.execute(
            "UPDATE users SET referral_code=? WHERE user_id=?",
            (code, user_id),
        )
        self.conn.commit()
        return code

    async def get_user_id_by_referral_code(self, code: str) -> Optional[int]:
        cur = self.conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE referral_code=?", (code,))
        row = cur.fetchone()
        return row["user_id"] if row else None

    async def get_referrer_id(self, user_id: int) -> Optional[int]:
        cur = self.conn.cursor()
        cur.execute("SELECT referrer_id FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return row["referrer_id"] if row else None

    async def set_referrer(self, user_id: int, referrer_id: int):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE users SET referrer_id=? WHERE user_id=?",
            (referrer_id, user_id),
        )
        self.conn.commit()

    async def get_referral_count(self, user_id: int) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE referrer_id=?", (user_id,))
        row = cur.fetchone()
        return row["cnt"] if row else 0

    async def get_referral_balance_cents(self, user_id: int) -> int:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT balance_cents FROM referral_balances WHERE user_id=?",
            (user_id,),
        )
        row = cur.fetchone()
        return row["balance_cents"] if row else 0

    async def add_referral_bonus(self, user_id: int, amount_cents: int):
        if amount_cents <= 0:
            return
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO referral_balances (user_id, balance_cents)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                balance_cents=balance_cents + excluded.balance_cents
            """,
            (user_id, amount_cents),
        )
        self.conn.commit()

    async def request_referral_withdrawal(self, user_id: int, amount_cents: int) -> bool:
        if amount_cents <= 0:
            return False
        cur = self.conn.cursor()
        cur.execute(
            "SELECT balance_cents FROM referral_balances WHERE user_id=?",
            (user_id,),
        )
        row = cur.fetchone()
        balance = row["balance_cents"] if row else 0
        if balance < amount_cents:
            return False
        cur.execute(
            "UPDATE referral_balances SET balance_cents=? WHERE user_id=?",
            (balance - amount_cents, user_id),
        )
        cur.execute(
            """
            INSERT INTO referral_withdrawals (user_id, amount_cents)
            VALUES (?, ?)
            """,
            (user_id, amount_cents),
        )
        self.conn.commit()
        return True

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
            WHERE buyer_id=? AND final_payment_status='confirmed'
            """,
            (user_id,),
        )
        responses_sum_cents = cur.fetchone()["s"]
        responses_sum = responses_sum_cents / 100.0

        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM offers
            WHERE buyer_id=? AND final_payment_status='confirmed'
            """,
            (user_id,),
        )
        completed_responses_count = cur.fetchone()["cnt"]

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
            "responses_sum": f"{responses_sum:.2f} руб.",
            "completed_responses_count": completed_responses_count,
            "deals_sum": f"{deals_sum:.2f} руб.",
            "cdek": cdek,
            "req": req,
        }

    async def get_report_stats(
        self, start_dt: Optional[datetime], end_dt: Optional[datetime]
    ) -> Dict[str, int]:
        cur = self.conn.cursor()

        def format_dt(dt: datetime) -> str:
            return dt.strftime("%Y-%m-%d %H:%M:%S")

        def build_date_filter(column: str):
            clauses = []
            params: list[str] = []
            if start_dt:
                clauses.append(f"{column} >= ?")
                params.append(format_dt(start_dt))
            if end_dt:
                clauses.append(f"{column} < ?")
                params.append(format_dt(end_dt))
            where_clause = " AND ".join(clauses)
            return where_clause, params

        users_filter, users_params = build_date_filter("created_at")
        users_sql = "SELECT COUNT(*) AS cnt FROM users"
        if users_filter:
            users_sql += f" WHERE {users_filter}"
        cur.execute(users_sql, users_params)
        new_users = cur.fetchone()["cnt"]

        offers_filter, offers_params = build_date_filter("created_at")
        offers_sql = "SELECT COUNT(*) AS cnt FROM offers WHERE deal_buyer_id IS NOT NULL"
        if offers_filter:
            offers_sql += f" AND {offers_filter}"
        cur.execute(offers_sql, offers_params)
        new_deals = cur.fetchone()["cnt"]

        return {
            "new_users": new_users,
            "new_deals": new_deals,
        }

    @staticmethod
    def _report_period_filter(
        start_dt: Optional[datetime], end_dt: Optional[datetime]
    ) -> Tuple[str, list[str]]:
        # Completed deals belong to the period by completed_at, cancelled deals by
        # cancelled_at, and open deals by created_at (their only final event so far).
        event_column = (
            "CASE WHEN completed_at IS NOT NULL THEN completed_at "
            "WHEN cancelled_at IS NOT NULL THEN cancelled_at ELSE created_at END"
        )
        clauses = ["deal_buyer_id IS NOT NULL"]
        params: list[str] = []
        if start_dt:
            clauses.append(f"{event_column} >= ?")
            params.append(start_dt.strftime("%Y-%m-%d %H:%M:%S"))
        if end_dt:
            clauses.append(f"{event_column} < ?")
            params.append(end_dt.strftime("%Y-%m-%d %H:%M:%S"))
        return " AND ".join(clauses), params

    async def get_completed_deals_for_period(
        self, start_dt: Optional[datetime], end_dt: Optional[datetime]
    ) -> list[Dict[str, Any]]:
        """Return every deal assigned to a reporting period (name kept for API clarity)."""
        where, params = self._report_period_filter(start_dt, end_dt)
        cur = self.conn.cursor()
        cur.execute(
            f"""
            SELECT id, deal_buyer_id AS buyer_id, seller_id,
                   buyer_username, seller_username, amount_cents,
                   service_fee_cents, seller_payout_cents, created_at, paid_at,
                   completed_at, cancelled_at,
                   COALESCE(final_status, status) AS status,
                   is_disputed, dispute_result,
                   CASE WHEN completed_at IS NOT NULL
                        THEN CAST((julianday(completed_at)-julianday(created_at))*86400 AS INTEGER)
                   END AS duration_seconds
              FROM offers
             WHERE {where}
             ORDER BY COALESCE(completed_at, cancelled_at, created_at), id
            """,
            params,
        )
        return [dict(row) for row in cur.fetchall()]

    async def get_deals_statistics(
        self, start_dt: Optional[datetime], end_dt: Optional[datetime]
    ) -> Dict[str, Any]:
        deals = await self.get_completed_deals_for_period(start_dt, end_dt)
        completed = [d for d in deals if d["status"] == "completed"]
        cancelled = [d for d in deals if d["status"] == "cancelled"]
        terminal_statuses = {"completed", "cancelled", "refunded", "closed_by_seller"}
        terminal = [d for d in deals if d["status"] in terminal_statuses]
        durations = [d["duration_seconds"] for d in completed if d["duration_seconds"] is not None]
        turnover = sum(d["amount_cents"] or 0 for d in completed)
        fees = sum(d["service_fee_cents"] or 0 for d in completed)
        payouts = sum(d["seller_payout_cents"] or 0 for d in completed)
        return {
            "deals": deals,
            "active_deals_count": sum(d["status"] not in terminal_statuses for d in deals),
            "completed_deals_count": len(completed),
            "cancelled_deals_count": len(cancelled),
            "disputed_deals_count": sum(bool(d["is_disputed"]) for d in deals),
            "turnover_cents": turnover,
            "service_fee_cents": fees,
            "seller_payout_cents": payouts,
            "average_check_cents": round(turnover / len(completed)) if completed else 0,
            "average_fee_cents": round(fees / len(completed)) if completed else 0,
            "average_duration_seconds": round(sum(durations) / len(durations)) if durations else 0,
            "success_percentage": (len(completed) / len(terminal) * 100) if terminal else 0.0,
        }

    async def get_active_deals_summary(self) -> Dict[str, int]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) AS cnt, COALESCE(SUM(price_cents), 0) AS total
            FROM offers
            WHERE status='approved' AND final_payment_status!='confirmed'
            """
        )
        row = cur.fetchone()
        return {
            "active_deals_count": row["cnt"],
            "active_deals_sum_cents": row["total"],
        }

    async def has_report_sent(
        self, report_type: str, period_start: Optional[str], period_end: Optional[str]
    ) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM reports_sent
            WHERE report_type=? AND period_start IS ? AND period_end IS ?
            """,
            (report_type, period_start, period_end),
        )
        return cur.fetchone() is not None

    async def mark_report_sent(
        self, report_type: str, period_start: Optional[str], period_end: Optional[str]
    ) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            INSERT INTO reports_sent (report_type, period_start, period_end)
            VALUES (?, ?, ?)
            """,
            (report_type, period_start, period_end),
        )
        self.conn.commit()

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return self._row_to_dict(cur.fetchone())

    async def get_cdek_contacts(self, user_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM cdek_contacts WHERE user_id=?", (user_id,))
        return self._row_to_dict(cur.fetchone())

    async def get_requisites(self, user_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM requisites WHERE user_id=?", (user_id,))
        return self._row_to_dict(cur.fetchone())

    async def add_user_rating(self, user_id: int, rating: int) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            UPDATE users
            SET rating_sum = rating_sum + ?,
                rating_count = rating_count + 1
            WHERE user_id=?
            """,
            (rating, user_id),
        )
        self.conn.commit()

    async def set_offer_seller_rating(self, offer_id: int, rating: int) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE offers SET seller_rating=? WHERE id=?",
            (rating, offer_id),
        )
        self.conn.commit()

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

    async def get_request_moderation_thread_id(self, request_id: int) -> Optional[int]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT moderation_thread_id
              FROM offers
             WHERE request_id=?
               AND moderation_thread_id IS NOT NULL
             ORDER BY id ASC
             LIMIT 1
            """,
            (request_id,),
        )
        row = cur.fetchone()
        return row["moderation_thread_id"] if row else None

    async def has_other_offer_with_statuses(
        self,
        request_id: int,
        offer_id: int,
        statuses: tuple[str, ...],
    ) -> bool:
        if not statuses:
            return False

        placeholders = ",".join("?" for _ in statuses)
        cur = self.conn.cursor()
        cur.execute(
            f"""
            SELECT 1
              FROM offers
             WHERE request_id=?
               AND id!=?
               AND status IN ({placeholders})
             LIMIT 1
            """,
            (request_id, offer_id, *statuses),
        )
        return cur.fetchone() is not None

    async def get_offers_count_for_request(self, request_id: int) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) AS cnt FROM offers WHERE request_id=?", (request_id,))
        row = cur.fetchone()
        return row["cnt"] if row else 0

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
        if status == "approved":
            cur.execute(
                """
                UPDATE offers
                   SET status=?,
                       deal_buyer_id=(SELECT user_id FROM requests WHERE id=offers.request_id),
                       seller_id=buyer_id,
                       buyer_username=(SELECT username FROM users WHERE user_id=(
                           SELECT user_id FROM requests WHERE id=offers.request_id)),
                       seller_username=(SELECT username FROM users WHERE user_id=offers.buyer_id),
                       service_fee_cents=ROUND(price_cents * 0.07),
                       seller_payout_cents=price_cents,
                       amount_cents=price_cents + ROUND(price_cents * 0.07),
                       final_status='approved'
                 WHERE id=?
                """,
                (status, offer_id),
            )
        elif status == "dispute":
            cur.execute(
                "UPDATE offers SET status=?, final_status='dispute', is_disputed=1 WHERE id=?",
                (status, offer_id),
            )
        elif status in {"closed_by_seller", "buyer_rejected", "cancelled", "refunded"}:
            final_status = "cancelled" if status in {"closed_by_seller", "buyer_rejected"} else status
            cur.execute(
                """UPDATE offers SET status=?, final_status=?,
                          cancelled_at=COALESCE(cancelled_at, CURRENT_TIMESTAMP),
                          dispute_result=CASE WHEN is_disputed=1
                              THEN COALESCE(dispute_result, ?) ELSE dispute_result END
                     WHERE id=?""",
                (status, final_status, final_status, offer_id),
            )
        else:
            cur.execute(
                "UPDATE offers SET status=?, final_status=? WHERE id=?",
                (status, status, offer_id),
            )
        self.conn.commit()

    async def update_deal_statistics(
        self, offer_id: int, *, dispute_result: Optional[str] = None,
        final_status: Optional[str] = None
    ) -> None:
        """Record a moderator-supplied dispute outcome without bypassing transitions."""
        cur = self.conn.cursor()
        cur.execute(
            """UPDATE offers
                  SET dispute_result=COALESCE(?, dispute_result),
                      final_status=COALESCE(?, final_status)
                WHERE id=?""",
            (dispute_result, final_status, offer_id),
        )
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
            """UPDATE offers SET buyer_deposit_status=?,
                      paid_at=CASE WHEN ?='confirmed' THEN COALESCE(paid_at, CURRENT_TIMESTAMP)
                                   ELSE paid_at END
                 WHERE id=?""",
            (status, status, offer_id),
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
            """UPDATE offers SET final_payment_status=?,
                      paid_at=CASE WHEN ?='confirmed'
                          THEN COALESCE(paid_at, CURRENT_TIMESTAMP) ELSE paid_at END,
                      completed_at=CASE WHEN ?='confirmed'
                          THEN COALESCE(completed_at, CURRENT_TIMESTAMP) ELSE completed_at END,
                      final_status=CASE WHEN ?='confirmed' THEN 'completed' ELSE final_status END,
                      dispute_result=CASE WHEN ?='confirmed' AND is_disputed=1
                          THEN COALESCE(dispute_result, 'completed') ELSE dispute_result END
                 WHERE id=?""",
            (status, status, status, status, status, offer_id),
        )
        self.conn.commit()

    async def set_manager_track_number(self, offer_id: int, track_number: str):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE offers SET manager_track_number=? WHERE id=?",
            (track_number, offer_id),
        )
        self.conn.commit()

    async def set_delivery_method(self, offer_id: int, method: str):
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE offers SET delivery_method=? WHERE id=?",
            (method, offer_id),
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
