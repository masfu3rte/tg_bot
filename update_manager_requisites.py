"""Update manager payment or CDEK details in .env without replacing other settings."""

import argparse
from pathlib import Path

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parent


def update_manager_requisites(env_file: Path, *, cdek_only: bool = False) -> None:
    name = "MANAGER_CDEK_CONTACT_TEXT" if cdek_only else "MANAGER_REQUISITES_TEXT"
    value = dotenv_values(ROOT / ".env.example").get(name)
    if not value:
        raise RuntimeError(f"В .env.example не заданы данные менеджера: {name}.")
    set_key(str(env_file), name, value, quote_mode="always")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--cdek-only",
        action="store_true",
        help="Обновить только контакты для отправки через CDEK",
    )
    args = parser.parse_args()
    update_manager_requisites(args.env_file, cdek_only=args.cdek_only)
    print("Данные менеджера обновлены. Перезапустите бота для применения.")


if __name__ == "__main__":
    main()
