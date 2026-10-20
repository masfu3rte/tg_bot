from contextlib import redirect_stdout
from dataclasses import replace
import gzip
import hashlib
import io
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import pyrage

import backups
from config import BACKUP_DRIVE_FOLDER_ID, BackupConfig


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "source.sqlite"
        self.conn = sqlite3.connect(self.source)
        self.addCleanup(self.conn.close)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("CREATE TABLE records (value TEXT)")
        self.conn.execute("INSERT INTO records VALUES ('saved-record')")
        self.conn.commit()
        self.identity = pyrage.x25519.Identity.generate()
        self.cfg = BackupConfig(True, str(self.source), "gdrive", str(self.identity.to_public()), 3600)
        self.uploaded = None
        self.upload_dir = None
        self.remote_calls = []
        self.which = patch("backups.shutil.which", return_value="/usr/bin/rclone")
        self.which.start()
        self.addCleanup(self.which.stop)

    def remote(self, cfg, *args):
        self.remote_calls.append(args)
        self.assertEqual(cfg.folder_id, BACKUP_DRIVE_FOLDER_ID)
        if args[0] == "copyto":
            self.upload_dir = Path(args[1]).parent
            self.uploaded = Path(args[1]).read_bytes()
            self.assertTrue(args[2].startswith("gdrive:tg_bot-"))
        if args[0] == "md5sum":
            return hashlib.md5(self.uploaded, usedforsecurity=False).hexdigest() + "  backup.age\n"
        return ""

    def test_live_wal_database_is_encrypted_uploaded_and_restorable(self):
        with patch("backups.run_rclone", side_effect=self.remote):
            filename = backups.backup_once(self.cfg)
        self.assertTrue(filename.endswith(".sqlite.gz.age"))
        self.assertTrue(self.uploaded.startswith(b"age-encryption.org/v1"))
        restored = self.root / "restored.sqlite"
        restored.write_bytes(gzip.decompress(pyrage.decrypt(self.uploaded, [self.identity])))
        with sqlite3.connect(restored) as conn:
            self.assertEqual(conn.execute("SELECT value FROM records").fetchall(), [("saved-record",)])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        self.assertEqual(self.conn.execute("SELECT value FROM records").fetchall(), [("saved-record",)])
        self.assertFalse(self.upload_dir.exists())

    def test_failed_upload_cleans_up_transient_snapshot(self):
        def fail(cfg, *args):
            self.remote(cfg, *args)
            if args[0] == "copyto":
                raise backups.BackupError("upload failed")
            return ""
        with patch("backups.run_rclone", side_effect=fail):
            with self.assertRaisesRegex(backups.BackupError, "upload failed"):
                backups.backup_once(self.cfg)
        self.assertFalse(self.upload_dir.exists())

    def test_checksum_mismatch_is_failure_and_cleans_up(self):
        def mismatch(cfg, *args):
            result = self.remote(cfg, *args)
            return "wrong-checksum  backup.age" if args[0] == "md5sum" else result
        with patch("backups.run_rclone", side_effect=mismatch):
            with self.assertRaisesRegex(backups.BackupError, "Контрольная сумма"):
                backups.backup_once(self.cfg)
        self.assertFalse(self.upload_dir.exists())

    def test_missing_source_never_creates_empty_database_or_uploads(self):
        missing = self.root / "missing.sqlite"
        with patch("backups.run_rclone", side_effect=self.remote):
            with self.assertRaisesRegex(backups.BackupError, "DB_PATH"):
                backups.backup_once(replace(self.cfg, db_path=str(missing)))
        self.assertFalse(missing.exists())
        self.assertIsNone(self.uploaded)

    def test_invalid_database_is_not_uploaded(self):
        invalid = self.root / "invalid.sqlite"
        invalid.write_bytes(b"not a SQLite database")
        with patch("backups.run_rclone", side_effect=self.remote):
            with self.assertRaises(backups.BackupError):
                backups.backup_once(replace(self.cfg, db_path=str(invalid)))
        self.assertIsNone(self.uploaded)

    def test_missing_encryption_key_prevents_any_drive_request(self):
        with patch("backups.run_rclone") as remote:
            with self.assertRaisesRegex(backups.BackupError, "BACKUP_AGE_RECIPIENT"):
                backups.backup_once(replace(self.cfg, recipient=""))
        remote.assert_not_called()

    def test_rclone_is_bound_to_requested_folder_and_errors_hide_credentials(self):
        result = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        with patch("backups.subprocess.run", return_value=result) as run:
            backups.run_rclone(self.cfg, "lsf", "gdrive:")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--drive-root-folder-id") + 1], BACKUP_DRIVE_FOLDER_ID)
        result = subprocess.CompletedProcess([], 1, stdout="", stderr="secret-token-from-backend")
        with patch("backups.subprocess.run", return_value=result):
            with self.assertRaises(backups.BackupError) as caught:
                backups.run_rclone(self.cfg, "lsf", "gdrive:")
        self.assertNotIn("secret-token", str(caught.exception))

    def test_private_key_is_not_printed_or_overwritten(self):
        path = self.root / "backup.agekey"
        output = io.StringIO()
        with redirect_stdout(output):
            backups.generate_key(path)
        private_key = path.read_text().strip()
        self.assertNotIn(private_key, output.getvalue())
        self.assertIn("BACKUP_AGE_RECIPIENT=age1", output.getvalue())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            backups.generate_key(path)
        self.assertEqual(path.read_text().strip(), private_key)


class BackupSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_uploads_on_start_and_waits_for_next_hour(self):
        import asyncio
        from unittest.mock import AsyncMock
        cfg = BackupConfig(True, "unused.sqlite", "gdrive", "unused", 3600)
        with patch("backups.backup_once", return_value="backup.age") as upload:
            with patch("backups.asyncio.sleep", new_callable=AsyncMock, side_effect=asyncio.CancelledError) as sleep:
                with self.assertRaises(asyncio.CancelledError):
                    await backups.backup_scheduler(cfg)
        upload.assert_called_once_with(cfg)
        sleep.assert_awaited_once_with(3600)


if __name__ == "__main__":
    unittest.main()
