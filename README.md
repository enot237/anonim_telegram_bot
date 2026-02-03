# VK registration bot (Python + SQLite)

A minimal VK community bot that listens for new messages and registers the sender in a SQLite database. It stores the raw user profile JSON plus a few extracted fields (first_name, last_name, sex) when available.

## Setup

1) Create and configure your VK community token (messages access) and enable Bot Long Poll in your community settings (see VK docs).

2) Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3) Configure environment variables:

```bash
export VK_GROUP_TOKEN="your_group_token"
export VK_GROUP_ID="123456789"
export TG_BOT_TOKEN="your_telegram_token"
export TG_ADMIN_IDS="123456789,987654321"
export VK_FIELDS=""
export VK_API_VERSION=""
export DB_PATH="bot.sqlite3"
export API_HOST="127.0.0.1"
export API_PORT="8000"
export API_DEBUG=""
export MANAGER_PASS="your_manager_password"
export ADMIN_PASS="your_admin_password"
export REG_START="2026-02-05 00:00:00"
export REG_END="2026-02-12 23:59:59"
export PREP_START="2026-02-13 00:00:00"
export PREP_END="2026-02-13 23:59:59"
export CHAT_START="2026-02-14 00:00:00"
export CHAT_END="2026-02-14 23:59:59"
```

- `VK_FIELDS`: comma-separated list of fields for `users.get` (define per VK API docs). If empty, the bot defaults to requesting `sex,bdate`. The bot stores the full response JSON and extracts `first_name`, `last_name`, `sex`, and age (if `bdate` has a year).
- `VK_API_VERSION`: optional API version passed to vk_api.
- `TG_BOT_TOKEN`: Telegram bot token for `tg_bot.py`.
- `TG_ADMIN_IDS`: comma-separated Telegram user IDs used for test broadcasts in the admin panel.
- `DB_PATH`: optional path to the SQLite file.
- `API_HOST`, `API_PORT`, `API_DEBUG`: settings for the API service.
- `MANAGER_PASS`, `ADMIN_PASS`: passwords for simplified admin access (pass via URL `?pass=...`).
- `REG_START`, `REG_END`: registration window (local time).
- `PREP_START`, `PREP_END`: preparation window (local time).
- `CHAT_START`, `CHAT_END`: chat window (local time).

You can also use a `.env` file (see `.env.example`) — both `bot.py` and `app.py` load it automatically.

4) Run the bot:

```bash
python bot.py
```

5) Run the Telegram bot:

```bash
python tg_bot.py
```

6) Run the API + frontend:

```bash
python app.py
```

Open `http://127.0.0.1:8000/?pass=YOUR_PASSWORD` in a browser to view the database.
Admins can send Telegram broadcasts from the panel. Test sends go to `TG_ADMIN_IDS`.

## Docker Compose

1) Ensure your `.env` is filled (same variables as above).

2) Build and start the Telegram bot + web app:

```bash
docker compose up -d --build
```

3) Open `http://127.0.0.1:8000/?pass=YOUR_PASSWORD`.

Compose mounts `./bot.sqlite3` into both services, so the same DB file is shared.

## Database

A `users` table is created automatically. Columns:
- `id` (primary key, autoincrement)
- `user_id` (VK/Telegram user id, unique per platform)
- `platform` (`vk` or `tg`)
- `first_name`, `last_name`, `sex`
- `status` (`pre_registered` or `registered`), `registration_step`, `bio`, `age`
- `looking_for`, `love_definition`, `zodiac`, `date_idea`, `share`, `red_flags`, `pet`, `important`, `temperament`
- `profile_json` (raw JSON response from `users.get`)
- `registered_at`, `updated_at`
- `last_message_id`, `last_message_at`, `last_message_text`, `last_peer_id`

A `pairs` table is created automatically. Columns:
- `user1_id` (internal `users.id`)
- `user2_id` (internal `users.id`)
- `created_at`

## Notes

- The `sex` value is stored exactly as returned by VK; interpret it according to VK API docs.
- If you want to capture additional data, add the desired fields in `VK_FIELDS`.
