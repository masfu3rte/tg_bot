# Telegram Bot

Python Telegram-бот на `aiogram`.

## Безопасная настройка

Никогда не добавляйте реальные токены, пароли, базы данных и приватные ключи в GitHub-репозиторий.
Все секретные и окружение-зависимые значения хранятся в локальном файле `.env`, который игнорируется Git.

## Локальный запуск

1. Установите зависимости:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Создайте `.env` из примера:

```bash
cp .env.example .env
```

3. Откройте `.env` и вставьте токен Telegram-бота в переменную `BOT_TOKEN`:

```env
BOT_TOKEN=your_telegram_bot_token_here
```

4. Запустите бота:

```bash
python3 main.py
```

Если `BOT_TOKEN` не задан, приложение выведет понятную ошибку и не запустится.

## Запуск на VPS Ubuntu

```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv git -y
git clone <repo_url>
cd <project_folder>
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python3 main.py
```

В файле `.env` укажите реальный `BOT_TOKEN` и остальные значения, которые относятся к вашим чатам, каналам и контактным данным.

## Получение file_id

Вспомогательный скрипт также читает токен из `.env`:

```bash
python3 get_file_id_bot.py
```
