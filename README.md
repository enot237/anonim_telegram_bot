# 14_feb_bot_gf — анонимный бот на 14 февраля (VK + Telegram) + админ‑панель

Проект для проведения анонимного знакомства/общения к 14 февраля. Участники регистрируются через VK или Telegram, заполняют анкету, после чего администратор формирует пары. В период общения бот пересылает сообщения партнёрам. В комплекте есть веб‑админка (Flask) для просмотра базы, управления режимами и рассылок.

## Что внутри

- `bot.py` — VK-бот (Long Poll). Регистрирует пользователей, задаёт вопросы анкеты и пересылает сообщения в период общения.
- `tg_bot.py` — Telegram‑бот (python-telegram-bot). Полная анкета, подтверждение/редактирование, пересылка сообщений в период общения. Использует режимы (`off`, `registration`, `prep`, `chat`, `finished`).
- `app.py` — Flask API + статическая админ‑панель (`static/index.html`). Управление пользователями, парами, режимом, тестовые/массовые рассылки в Telegram, пинги участникам.
- `bot.sqlite3` — база SQLite с пользователями, парами и настройками (создаётся автоматически, можно хранить рядом с проектом).
- `docker-compose.yml` — запуск веб‑части и Telegram‑бота в Docker.

## Как работает приложение

1. Пользователь пишет боту (VK или Telegram).
2. В период регистрации бот собирает анкету по шагам.
3. Администратор формирует пары (через админ‑панель / API).
4. В период общения сообщения пересылаются партнёрам.

Telegram‑бот управляется режимами в таблице `settings` и через админ‑панель. VK‑бот ориентируется на временные окна регистрации/общения.

## Переменные окружения (`.env`)

Файл `.env` загружается автоматически в `bot.py`, `tg_bot.py`, `app.py` через `python-dotenv`.

- `VK_GROUP_TOKEN` — токен сообщества VK с доступом к сообщениям.
- `VK_GROUP_ID` — ID сообщества VK.
- `VK_FIELDS` — поля для `users.get` (через запятую). Если пусто, используется `sex,bdate`.
- `VK_API_VERSION` — версия API VK для `vk_api` (опционально).
- `TG_BOT_TOKEN` — токен Telegram‑бота.
- `TG_ADMIN_IDS` — список Telegram ID администраторов (через запятую) для тестовых рассылок.
- `DB_PATH` — путь к SQLite файлу (по умолчанию `bot.sqlite3`).
- `API_HOST` — хост для Flask (`app.py`).
- `API_PORT` — порт для Flask.
- `API_DEBUG` — включить debug режим (`1/true/yes`).
- `MANAGER_PASS` — пароль для доступа к админ‑панели/API (роль manager).
- `ADMIN_PASS` — пароль для полного доступа (роль admin).
- `REG_START`, `REG_END` — окно регистрации (локальное время сервера).
- `PREP_START`, `PREP_END` — окно подготовки пар.
- `CHAT_START`, `CHAT_END` — окно общения.

Важно: время берётся из системного времени сервера. На VPS проверьте таймзону.

## База данных

Таблицы создаются автоматически при первом запуске.

`users` (основные поля):
- `user_id`, `platform` (`vk` или `tg`)
- `first_name`, `last_name`, `sex`, `age`
- `status` (`pre_registered`, `registered`)
- `registration_step` (текущий шаг анкеты)
- `bio`, `looking_for`, `love_definition`, `zodiac`, `date_idea`, `share`, `red_flags`, `pet`, `important`, `temperament`
- `profile_json` — сырой профиль VK/TG
- `registered_at`, `updated_at`
- `last_message_*` — последнее сообщение

`pairs`:
- `user1_id`, `user2_id` — ID пользователей в таблице `users`
- `created_at`

`settings`:
- `key`, `value`, `updated_at` — режим работы бота (`mode`).

## Админ‑панель и API

Админка доступна по адресу `http://HOST:PORT/?pass=YOUR_PASSWORD`.

Роли:
- `manager` — доступ к CRUD пользователей и пар.
- `admin` — всё выше + управление режимом и рассылки Telegram.

Основные API‑эндпоинты:
- `GET /api/health` — проверка статуса.
- `GET /api/users` — список пользователей.
- `GET /api/users/<id>` — карточка пользователя.
- `POST /api/users` — создать пользователя.
- `PUT /api/users/<id>` — обновить пользователя.
- `DELETE /api/users/<id>` — удалить пользователя.
- `POST /api/pairs` — создать пару.
- `DELETE /api/pairs/<user_id>` — удалить пару.
- `POST /api/ping/<user_id>` — отправить напоминание пользователю (VK или TG).
- `GET /api/settings` — получить режим (admin).
- `PUT /api/settings` — обновить режим (admin).
- `POST /api/broadcast/tg/test` — тестовая рассылка (admin).
- `POST /api/broadcast/tg/all` — рассылка всем TG пользователям (admin).

## Локальный запуск

1. Установите зависимости:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Создайте `.env` на основе `.env.example` и заполните переменные.

3. Запуск компонентов:

```bash
python bot.py     # VK-бот
python tg_bot.py  # Telegram-бот
python app.py     # админ‑панель + API
```

Откройте админ‑панель: `http://127.0.0.1:8000/?pass=YOUR_PASSWORD`.

## Запуск через Docker Compose

Файл `docker-compose.yml` поднимает веб‑часть и Telegram‑бота. VK‑бот в Compose не включён — его можно запускать отдельно или добавить ещё один сервис.

1. Подготовьте `.env` (как в локальном запуске).
2. Запустите:

```bash
docker compose up -d --build
```

3. Админ‑панель будет доступна на `http://HOST:8000/?pass=YOUR_PASSWORD`.

## Продакшн‑развёртывание на VPS (Linux)

1. Арендуйте VPS с Ubuntu 22.04/24.04.
2. Подключитесь по SSH:

```bash
ssh root@YOUR_SERVER_IP
```

3. Установите Docker и Compose:

```bash
apt update
apt install -y docker.io docker-compose-plugin git
systemctl enable --now docker
```

4. Клонируйте репозиторий:

```bash
git clone <YOUR_REPO_URL> 14_feb_bot_gf
cd 14_feb_bot_gf
```

5. Создайте `.env` (по `.env.example`) и заполните токены/пароли.

6. Откройте порт 8000 в фаерволе (если используете UFW):

```bash
ufw allow 8000/tcp
```

7. Запустите проект:

```bash
docker compose up -d --build
```

8. Проверьте логи:

```bash
docker compose logs -f
```

Опционально настройте домен и прокси (Nginx) для доступа к панели через HTTPS.

## Поддержка и обслуживание

- Обновление кода:

```bash
git pull
docker compose up -d --build
```

- Резервные копии:

```bash
cp bot.sqlite3 bot.sqlite3.bak
```

- Проверка статуса контейнеров:

```bash
docker compose ps
```

## Замечания

- Все временные окна (`REG_*`, `PREP_*`, `CHAT_*`) зависят от времени сервера.
- Для рассылок Telegram нужен `TG_BOT_TOKEN`, а для тестовых рассылок — `TG_ADMIN_IDS`.
- Сообщения в период общения пересылаются только если пара создана в таблице `pairs`.

