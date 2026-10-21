from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import backups
from config import BackupConfig


class BackupDependencyTests(unittest.TestCase):
    def test_bot_reaches_polling_without_pyrage_when_backups_are_disabled(self):
        # A fresh process reproduces the server failure even when pyrage is installed here.
        script = """
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch
sys.modules['pyrage'] = None
with patch.dict(os.environ, {'BOT_TOKEN': '123456789:offline_startup_only', 'DB_PATH': ':memory:', 'BACKUP_ENABLED': '0'}):
    import main
    with patch.object(main.Dispatcher, 'start_polling', new_callable=AsyncMock) as polling:
        with patch.object(main, 'start_reports_task'):
            asyncio.run(main.main())
    polling.assert_awaited_once()
print('polling reached')
"""
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("polling reached", result.stdout)

    def test_enabled_backups_log_missing_package_without_stopping_bot(self):
        cfg = BackupConfig(True, "unused.sqlite", "gdrive", "age1test", 3600)
        with patch("backups.load_backup_config", return_value=cfg):
            with patch("backups.import_module", side_effect=ModuleNotFoundError("pyrage")):
                with self.assertLogs("backups", level="ERROR") as logs:
                    task = backups.start_backup_task()
        self.assertIsNone(task)
        self.assertIn("pip install -r requirements.txt", " ".join(logs.output))

    def test_missing_package_never_reaches_drive(self):
        cfg = BackupConfig(True, "unused.sqlite", "gdrive", "age1test", 3600)
        with patch("backups.import_module", side_effect=ModuleNotFoundError("pyrage")):
            with patch("backups.run_rclone") as remote:
                with self.assertRaisesRegex(backups.BackupError, "pyrage"):
                    backups.backup_once(cfg)
        remote.assert_not_called()

    def test_missing_package_does_not_create_private_key_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key.agekey"
            with patch("backups.import_module", side_effect=ModuleNotFoundError("pyrage")):
                with self.assertRaisesRegex(backups.BackupError, "pyrage"):
                    backups.generate_key(path)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
