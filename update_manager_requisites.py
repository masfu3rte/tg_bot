"""Update manager payment details in .env without replacing other settings."""
import argparse
from pathlib import Path

from dotenv import dotenv_values, set_key


ROOT = Path(__file__).resolve().parent


def update_manager_requisites(env_file: Path) -> None:
    value = dotenv_values(ROOT / ".env.example").get("MANAGER_REQUISITES_TEXT")
    if not value:
        raise RuntimeError("В .env.example не заданы реквизиты менеджера.")
    set_key(str(env_file), "MANAGER_REQUISITES_TEXT", value, quote_mode="always")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    update_manager_requisites(args.env_file)
    print("Реквизиты менеджера обновлены. Перезапустите бота для применения.")


if __name__ == "__main__":
    main()
