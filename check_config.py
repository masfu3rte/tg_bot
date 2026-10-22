"""Validate settings without connecting to Telegram or changing the database."""

from pathlib import Path
from config import load_config, load_backup_config, validate_runtime_config


def main():
    cfg = load_config()
    validate_runtime_config(cfg)
    print(
        "Основные настройки заполнены; фактические права в Telegram ещё не проверены."
    )
    if not load_backup_config().enabled:
        print(
            "Google Drive: выключен. Локальные снимки не заменяют внешнее резервирование."
        )
    path = Path(__file__).resolve().parent / ".env"
    if path.exists() and path.stat().st_mode & 0o077:
        print("Ограничьте права локального .env до 0600.")


if __name__ == "__main__":
    main()
