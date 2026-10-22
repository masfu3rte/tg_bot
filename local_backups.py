"""Private local SQLite snapshots; configure encrypted remote backups separately."""

import asyncio
from datetime import datetime, timezone
import gzip
import logging
import os
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from backups import create_snapshot

log = logging.getLogger(__name__)


def local_backup_once(db_path, folder):
    target = Path(folder)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.chmod(0o700)
    filename = (
        f"bot-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.sqlite.gz"
    )
    with tempfile.TemporaryDirectory(dir=target, prefix=".snapshot-") as work:
        snapshot = Path(work) / "db.sqlite"
        create_snapshot(Path(db_path), snapshot)
        compressed = Path(work) / "db.sqlite.gz"
        with snapshot.open("rb") as source, gzip.open(compressed, "wb") as destination:
            shutil.copyfileobj(source, destination)
        compressed.chmod(0o600)
        os.replace(compressed, target / filename)
    return target / filename


def start_local_backup_task(db_path):
    if db_path == ":memory:" or os.getenv("LOCAL_BACKUP_ENABLED", "1") != "1":
        return None
    folder = os.getenv(
        "LOCAL_BACKUP_DIRECTORY", str(Path(db_path).resolve().parent / "backups")
    )
    interval = int(os.getenv("LOCAL_BACKUP_INTERVAL_SECONDS", "3600"))
    if interval < 60:
        raise RuntimeError("LOCAL_BACKUP_INTERVAL_SECONDS должна быть не меньше 60.")

    async def run():
        while True:
            try:
                await asyncio.to_thread(local_backup_once, db_path, folder)
            except Exception as exc:
                log.error("Local backup failed: %s", type(exc).__name__)
            await asyncio.sleep(interval)

    return asyncio.create_task(run())
