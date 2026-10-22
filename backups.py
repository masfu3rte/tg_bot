"""Consistent, encrypted SQLite backups to the configured Google Drive folder."""

import argparse
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
from importlib import import_module
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from uuid import uuid4

from config import BackupConfig, load_backup_config

logger = logging.getLogger(__name__)


class BackupError(RuntimeError):
    pass


def load_encryption_module():
    try:
        return import_module("pyrage")
    except ImportError:
        raise BackupError(
            "Недоступен модуль шифрования pyrage. Установите зависимости командой "
            "python -m pip install -r requirements.txt, используя Python из окружения бота."
        ) from None


def validate_config(cfg: BackupConfig) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]*", cfg.remote):
        raise BackupError(
            "BACKUP_RCLONE_REMOTE должна содержать имя remote rclone без двоеточия."
        )
    if cfg.interval_seconds < 60:
        raise BackupError("BACKUP_INTERVAL_SECONDS должна быть не меньше 60.")
    if not cfg.recipient:
        raise BackupError("Не задан публичный ключ шифрования BACKUP_AGE_RECIPIENT.")
    pyrage = load_encryption_module()
    try:
        pyrage.x25519.Recipient.from_str(cfg.recipient)
    except pyrage.RecipientError:
        raise BackupError(
            "BACKUP_AGE_RECIPIENT должна содержать публичный ключ age вида age1…."
        ) from None
    if not shutil.which(cfg.rclone_binary):
        raise BackupError(
            "Не установлен rclone; установите его или задайте BACKUP_RCLONE_BINARY."
        )


def run_rclone(cfg: BackupConfig, *arguments: str) -> str:
    command = [
        cfg.rclone_binary,
        *arguments,
        "--drive-root-folder-id",
        cfg.folder_id,
        "--retries",
        "3",
        "--low-level-retries",
        "3",
        "--contimeout",
        "30s",
        "--timeout",
        "2m",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise BackupError("Истекло время ожидания Google Drive/rclone.") from None
    except OSError:
        raise BackupError("Не удалось запустить rclone.") from None
    if result.returncode:
        # Do not log rclone stderr: backend diagnostics may contain credentials.
        raise BackupError(
            f"rclone: код {result.returncode}. Проверьте авторизацию remote, "
            "право записи в папку Google Drive и сетевой доступ."
        )
    return result.stdout


def check_access(cfg: BackupConfig) -> None:
    validate_config(cfg)
    run_rclone(cfg, "lsf", f"{cfg.remote}:", "--max-depth", "1")


def create_snapshot(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise BackupError(
            "Файл DB_PATH не найден; пустая база для резервирования не создаётся."
        )
    deadline = time.monotonic() + 30

    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise BackupError("Не удалось получить снимок SQLite за 30 секунд.")

    try:
        with closing(
            sqlite3.connect(
                source.resolve().as_uri() + "?mode=ro", uri=True, timeout=10
            )
        ) as src:
            with closing(sqlite3.connect(destination)) as snapshot:
                src.backup(snapshot, pages=256, progress=progress, sleep=0.05)
                if snapshot.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise BackupError(
                        "Проверка целостности резервной копии SQLite не прошла."
                    )
        destination.chmod(0o600)
    except sqlite3.Error:
        raise BackupError("Не удалось создать корректный снимок SQLite.") from None


def file_md5(path: Path) -> str:
    # Drive exposes MD5 for uploaded binary files; this checks transfer integrity.
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_once(cfg: BackupConfig) -> str:
    check_access(cfg)
    pyrage = load_encryption_module()
    filename = f"tg_bot-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:12]}.sqlite.gz.age"
    remote_path = f"{cfg.remote}:{filename}"
    # Only transient files are kept locally, including when upload or verification fails.
    with tempfile.TemporaryDirectory(prefix="tg-bot-backup-") as directory:
        root = Path(directory)
        snapshot = root / "snapshot.sqlite"
        compressed = root / "snapshot.sqlite.gz"
        encrypted = root / filename
        create_snapshot(Path(cfg.db_path), snapshot)
        with snapshot.open("rb") as src, gzip.open(compressed, "wb") as dst:
            shutil.copyfileobj(src, dst)
        compressed.chmod(0o600)
        try:
            pyrage.encrypt_file(
                str(compressed),
                str(encrypted),
                [pyrage.x25519.Recipient.from_str(cfg.recipient)],
            )
        except pyrage.EncryptError:
            raise BackupError(
                "Не удалось зашифровать снимок; загрузка отменена."
            ) from None
        encrypted.chmod(0o600)
        checksum = file_md5(encrypted)
        run_rclone(cfg, "copyto", str(encrypted), remote_path)
        remote_checksum = run_rclone(cfg, "md5sum", remote_path).split()
        if not remote_checksum or remote_checksum[0].lower() != checksum:
            raise BackupError("Контрольная сумма копии в Google Drive не совпадает.")
    return filename


async def backup_scheduler(cfg: BackupConfig) -> None:
    while True:
        try:
            filename = await asyncio.to_thread(backup_once, cfg)
            logger.info("Резервная копия проверена в Google Drive: %s", filename)
        except Exception as exc:
            # Avoid credential disclosure through external library exception messages.
            if isinstance(exc, BackupError):
                logger.error("Резервное копирование: %s", exc)
            else:
                logger.error("Резервное копирование: ошибка %s", type(exc).__name__)
        await asyncio.sleep(cfg.interval_seconds)


def start_backup_task() -> asyncio.Task | None:
    cfg = load_backup_config()
    if not cfg.enabled:
        logger.warning(
            "Копии Google Drive выключены. Настройте BACKUP_ENABLED и ключ age; локальные копии не защищают от потери диска."
        )
        return None
    try:
        validate_config(cfg)
    except BackupError as exc:
        logger.error("Резервное копирование не запущено: %s", exc)
        return None
    return asyncio.create_task(backup_scheduler(cfg))


def generate_key(path: Path) -> None:
    pyrage = load_encryption_module()
    identity = pyrage.x25519.Identity.generate()
    # Never overwrite an existing private key or print it to logs.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(str(identity) + "\n")
    print("Приватный ключ сохранён в указанный файл. Храните его вне сервера бота.")
    print("BACKUP_AGE_RECIPIENT=" + str(identity.to_public()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument(
        "--check",
        action="store_true",
        help="Проверить настройки и доступ к папке без загрузки БД",
    )
    commands.add_argument(
        "--loop",
        action="store_true",
        help="Запускать резервное копирование по расписанию",
    )
    commands.add_argument(
        "--generate-key",
        type=Path,
        metavar="FILE",
        help="Создать личный ключ age на доверенном компьютере",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        if args.generate_key:
            generate_key(args.generate_key)
            return 0
        cfg = load_backup_config()
        validate_config(cfg)
        if args.check:
            check_access(cfg)
            print("Доступ к папке Google Drive подтверждён; загрузка ещё не проверена.")
        elif args.loop:
            asyncio.run(backup_scheduler(cfg))
        else:
            print("Копия загружена и проверена: " + backup_once(cfg))
    except (BackupError, RuntimeError, OSError) as exc:
        logger.error(
            "%s",
            exc if isinstance(exc, (BackupError, RuntimeError)) else type(exc).__name__,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
