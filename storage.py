"""Persistent FSM storage; no network service is needed for a single bot process."""

import json
import os
import sqlite3
from dataclasses import asdict
from pathlib import Path

from aiogram.fsm.storage.base import BaseStorage, StorageKey


class SQLiteStorage(BaseStorage):
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=10)
        if path != ":memory:":
            os.chmod(path, 0o600)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS fsm (key TEXT PRIMARY KEY, state TEXT, data TEXT NOT NULL)"
        )
        self.conn.commit()

    def key(self, key: StorageKey) -> str:
        return json.dumps(asdict(key), sort_keys=True)

    async def set_state(self, key: StorageKey, state=None):
        value = state.state if hasattr(state, "state") else state
        with self.conn:
            self.conn.execute(
                "INSERT INTO fsm VALUES (?, ?, '{}') ON CONFLICT(key) DO UPDATE SET state=excluded.state",
                (self.key(key), value),
            )

    async def get_state(self, key: StorageKey):
        row = self.conn.execute(
            "SELECT state FROM fsm WHERE key=?", (self.key(key),)
        ).fetchone()
        return row[0] if row else None

    async def set_data(self, key: StorageKey, data: dict):
        with self.conn:
            self.conn.execute(
                "INSERT INTO fsm VALUES (?, NULL, ?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                (self.key(key), json.dumps(data, ensure_ascii=False)),
            )

    async def get_data(self, key: StorageKey):
        row = self.conn.execute(
            "SELECT data FROM fsm WHERE key=?", (self.key(key),)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    async def close(self):
        self.conn.close()
