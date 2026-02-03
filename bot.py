import json
import logging
import os
import random
import sqlite3
import re
from datetime import datetime, timezone

import vk_api
from vk_api.bot_longpoll import VkBotEventType, VkBotLongPoll
from dotenv import load_dotenv


def get_env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        raise SystemExit(f"Missing required env var: {name}")
    return value


def utc_iso(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            platform TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT,
            sex INTEGER,
            profile_json TEXT,
            status TEXT,
            bio TEXT,
            registration_step TEXT,
            age INTEGER,
            looking_for TEXT,
            love_definition TEXT,
            zodiac TEXT,
            date_idea TEXT,
            share TEXT,
            red_flags TEXT,
            pet TEXT,
            important TEXT,
            temperament TEXT,
            registered_at TEXT,
            updated_at TEXT,
            last_message_id INTEGER,
            last_message_at TEXT,
            last_message_text TEXT,
            last_peer_id INTEGER
            ,
            UNIQUE(platform, user_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pairs (
            user1_id INTEGER PRIMARY KEY,
            user2_id INTEGER NOT NULL UNIQUE,
            created_at TEXT,
            CHECK (user1_id != user2_id)
        )
        """
    )
    ensure_user_columns(conn)
    conn.commit()


def ensure_user_columns(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "id" not in columns:
        raise RuntimeError("Users table missing id column. Run the migration.")
    if "platform" not in columns:
        raise RuntimeError("Users table missing platform column. Run the migration.")
    if "status" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN status TEXT")
    if "bio" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN bio TEXT")
    if "registration_step" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN registration_step TEXT")
    if "age" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN age INTEGER")
    if "looking_for" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN looking_for TEXT")
    if "love_definition" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN love_definition TEXT")
    if "zodiac" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN zodiac TEXT")
    if "date_idea" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN date_idea TEXT")
    if "share" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN share TEXT")
    if "red_flags" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN red_flags TEXT")
    if "pet" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN pet TEXT")
    if "important" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN important TEXT")
    if "temperament" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN temperament TEXT")
    conn.commit()


def extract_base_fields(profile):
    if not profile:
        return None, None, None
    return (
        profile.get("first_name"),
        profile.get("last_name"),
        profile.get("sex"),
    )


def extract_age(profile):
    if not profile:
        return None
    bdate = profile.get("bdate")
    if not bdate:
        return None
    parts = bdate.split(".")
    if len(parts) != 3:
        return None
    try:
        day, month, year = map(int, parts)
        today = datetime.now().date()
        age = today.year - year - ((today.month, today.day) < (month, day))
        return age if age > 0 else None
    except ValueError:
        return None


def fetch_profile(api, user_id, fields):
    params = {"user_ids": user_id}
    if fields:
        params["fields"] = fields
    result = api.users.get(**params)
    if not result:
        return None
    return result[0]


def insert_user(conn, user_id, profile, message, status, bio=None, registration_step=None, platform="vk"):
    first_name, last_name, sex = extract_base_fields(profile)
    age = extract_age(profile)
    profile_json = json.dumps(profile or {}, ensure_ascii=True)
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    last_message_id = message.get("id")
    last_message_at = utc_iso(message.get("date"))
    last_message_text = message.get("text")
    last_peer_id = message.get("peer_id")

    conn.execute(
        """
        INSERT INTO users (
            user_id,
            platform,
            first_name,
            last_name,
            sex,
            profile_json,
            status,
            bio,
            registration_step,
            age,
            registered_at,
            updated_at,
            last_message_id,
            last_message_at,
            last_message_text,
            last_peer_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            platform,
            first_name,
            last_name,
            sex,
            profile_json,
            status,
            bio,
            registration_step,
            age,
            now_iso,
            now_iso,
            last_message_id,
            last_message_at,
            last_message_text,
            last_peer_id,
        ),
    )
    conn.commit()


def get_message(event):
    obj = event.object
    if isinstance(obj, dict):
        msg = obj.get("message") or obj
    else:
        msg = getattr(obj, "message", None) or getattr(obj, "object", None)

    if msg is None:
        return None
    if isinstance(msg, dict):
        return msg
    return vars(msg)


def user_exists(conn, user_id):
    row = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row is not None


def get_user(conn, user_id, platform="vk"):
    return conn.execute(
        "SELECT id, user_id, status, registration_step, age, sex, bio FROM users WHERE user_id = ? AND platform = ?",
        (user_id, platform),
    ).fetchone()


def is_registration_command(text):
    return text.strip().lower() == "ок"


def send_message(api, user_id, text, keyboard=None):
    params = {
        "user_id": user_id,
        "message": text,
        "random_id": random.randint(1, 2**31 - 1),
    }
    api.messages.send(**params)


def parse_local_datetime(value, name):
    if not value:
        raise SystemExit(f"Missing required env var: {name}")
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise SystemExit(f"Invalid datetime for {name}: {value}") from exc


def in_window(now, start_dt, end_dt):
    return start_dt <= now <= end_dt


def format_date(dt):
    return dt.strftime("%d.%m.%Y")


def format_datetime(dt):
    return dt.strftime("%d.%m.%Y %H:%M")


def format_range(start_dt, end_dt):
    if start_dt.date() == end_dt.date():
        return format_date(start_dt)
    if start_dt.year == end_dt.year:
        return f"{start_dt:%d.%m}–{end_dt:%d.%m.%Y}"
    return f"{format_date(start_dt)}–{format_date(end_dt)}"


def load_windows():
    reg_start_raw = get_env("REG_START", default=get_env("date_start", None))
    reg_end_raw = get_env("REG_END", default=get_env("date_end", None))
    prep_start_raw = get_env("PREP_START", default=None)
    prep_end_raw = get_env("PREP_END", default=None)
    chat_start_raw = get_env("CHAT_START", default=None)
    chat_end_raw = get_env("CHAT_END", default=None)

    reg_start = parse_local_datetime(reg_start_raw, "REG_START")
    reg_end = parse_local_datetime(reg_end_raw, "REG_END")
    prep_start = parse_local_datetime(prep_start_raw, "PREP_START")
    prep_end = parse_local_datetime(prep_end_raw, "PREP_END")
    chat_start = parse_local_datetime(chat_start_raw, "CHAT_START")
    chat_end = parse_local_datetime(chat_end_raw, "CHAT_END")

    return {
        "registration": (reg_start, reg_end),
        "prep": (prep_start, prep_end),
        "chat": (chat_start, chat_end),
    }


def describe_stage(now, windows):
    reg_start, reg_end = windows["registration"]
    prep_start, prep_end = windows["prep"]
    chat_start, chat_end = windows["chat"]

    reg_range = format_range(reg_start, reg_end)
    prep_range = format_range(prep_start, prep_end)
    chat_range = format_range(chat_start, chat_end)

    if now < reg_start:
        stage = "ожидание регистрации"
        next_stage = f"регистрация ({reg_range})"
    elif in_window(now, reg_start, reg_end):
        stage = f"регистрация ({reg_range})"
        next_stage = f"подготовка пар ({prep_range})"
    elif in_window(now, prep_start, prep_end):
        stage = f"подготовка пар ({prep_range})"
        next_stage = f"общение ({chat_range})"
    elif in_window(now, chat_start, chat_end):
        stage = f"общение ({chat_range})"
        next_stage = "завершение"
    else:
        stage = "завершено"
        next_stage = "—"

    return stage, next_stage


def build_description(windows):
    reg_start, reg_end = windows["registration"]
    prep_start, prep_end = windows["prep"]
    chat_start, chat_end = windows["chat"]

    reg_range = format_range(reg_start, reg_end)
    prep_range = format_range(prep_start, prep_end)
    chat_range = format_range(chat_start, chat_end)

    return (
        "Привет! Это бот для анонимного общения на 14 февраля.\n\n"
        f"Регистрация проходит {reg_range}.\n"
        f"Подготовка пар: {prep_range}.\n"
        f"Общение с половинкой: {chat_range}.\n\n"
        'Чтобы начать регистрацию, напишите "ок".\n'
        "После этого ответьте на несколько вопросов — начнем с короткого сообщения о себе."
    )


def build_status_message(now, windows):
    stage, next_stage = describe_stage(now, windows)
    chat_start, _ = windows["chat"]
    chat_start_text = format_datetime(chat_start)
    return (
        f"Сейчас бот на этапе: {stage}.\n"
        f"Следующий этап: {next_stage}.\n"
        f"Ваше общение с половинкой начнется {chat_start_text}."
    )


def get_partner_id(conn, user_id, platform="vk"):
    row = conn.execute(
        "SELECT id FROM users WHERE user_id = ? AND platform = ?",
        (user_id, platform),
    ).fetchone()
    if not row:
        return None
    internal_id = row[0]
    row = conn.execute(
        "SELECT user2_id FROM pairs WHERE user1_id = ?", (internal_id,)
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT user1_id FROM pairs WHERE user2_id = ?", (internal_id,)
        ).fetchone()
    if not row:
        return None
    partner_internal_id = row[0]
    partner_row = conn.execute(
        "SELECT user_id, platform FROM users WHERE id = ?", (partner_internal_id,)
    ).fetchone()
    if not partner_row:
        return None
    partner_user_id, partner_platform = partner_row
    if partner_platform != platform:
        return None
    return partner_user_id


def update_user(conn, user_id, updates, message=None, platform="vk"):
    if not updates:
        updates = {}
    now_iso = datetime.now(tz=timezone.utc).isoformat()
    data = dict(updates)
    data["updated_at"] = now_iso
    if message:
        data.update(
            {
                "last_message_id": message.get("id"),
                "last_message_at": utc_iso(message.get("date")),
                "last_message_text": message.get("text"),
                "last_peer_id": message.get("peer_id"),
            }
        )
    if not data:
        return
    columns = list(data.keys())
    assignments = ", ".join([f"{col} = ?" for col in columns])
    values = [data[col] for col in columns]
    conn.execute(
        f"UPDATE users SET {assignments} WHERE user_id = ? AND platform = ?",
        [*values, user_id, platform],
    )
    conn.commit()


def parse_age(text):
    match = re.search(r"\d{1,3}", text)
    if not match:
        return None
    age = int(match.group(0))
    if 10 <= age <= 100:
        return age
    return None


def parse_sex(text):
    value = text.strip().lower()
    if value in {"м", "муж", "мужчина", "мужской"}:
        return 2
    if value in {"ж", "жен", "женщина", "женский"}:
        return 1
    return None


def is_yes(text):
    return text.strip().lower() in {"да", "ага", "yes", "y"}


def is_no(text):
    return text.strip().lower() in {"нет", "не", "no", "n"}


def build_steps(age, sex):
    steps = ["bio"]
    steps.append("age_confirm" if age else "age")
    steps.append("sex_confirm" if sex in (1, 2) else "sex")
    steps.extend(
        [
            "looking_for",
            "love_definition",
            "zodiac",
            "date_idea",
            "share",
            "red_flags",
            "pet",
            "important",
            "temperament",
        ]
    )
    return steps


def prompt_for_step(step, user):
    if step == "bio":
        return "Напишите о себе одним сообщением."
    if step == "age":
        return "Сколько вам лет? Укажите числом."
    if step == "age_confirm":
        age = user["age"]
        return f"Ваш возраст {age} лет, верно? Напишите \"да\" или введите свой настоящий возраст."
    if step == "sex":
        return "Укажите ваш пол: мужчина или женщина."
    if step == "sex_confirm":
        sex = user["sex"]
        if sex == 2:
            return "Вы мужчина? Напишите \"да\" или укажите пол."
        if sex == 1:
            return "Вы женщина? Напишите \"да\" или укажите пол."
        return "Укажите ваш пол: мужчина или женщина."
    if step == "looking_for":
        return "Кого ты ищешь?"
    if step == "love_definition":
        return "В чём выражается любовь для тебя?"
    if step == "zodiac":
        return "Знак зодиака?"
    if step == "date_idea":
        return "Как ты бы провел время со второй половинкой?"
    if step == "share":
        return "Что ты готов(а) разделить со второй половинкой?"
    if step == "red_flags":
        return "Ред флаги?"
    if step == "pet":
        return "Домашний питомец?"
    if step == "important":
        return "Что тебе важно в собеседнике?"
    if step == "temperament":
        return "Экстраверт/амбиверт/интроверт."
    return "Напишите ответ одним сообщением."


def apply_step_answer(user, step, text):
    updates = {}
    if step == "bio":
        if not text:
            return None, "Пожалуйста, пришлите одно текстовое сообщение о себе."
        updates["bio"] = text
    elif step == "age":
        age = parse_age(text)
        if not age:
            return None, "Введите возраст числом."
        updates["age"] = age
    elif step == "age_confirm":
        if is_yes(text):
            pass
        else:
            age = parse_age(text)
            if not age:
                return None, "Напишите \"да\" или свой возраст числом."
            updates["age"] = age
    elif step == "sex":
        sex = parse_sex(text)
        if not sex:
            return None, "Укажите пол: мужчина или женщина."
        updates["sex"] = sex
    elif step == "sex_confirm":
        if is_yes(text):
            pass
        else:
            sex = parse_sex(text)
            if sex:
                updates["sex"] = sex
            elif is_no(text) and user["sex"] in (1, 2):
                updates["sex"] = 1 if user["sex"] == 2 else 2
            else:
                return None, "Напишите \"да\" или укажите пол: мужчина/женщина."
    else:
        mapping = {
            "looking_for": "looking_for",
            "love_definition": "love_definition",
            "zodiac": "zodiac",
            "date_idea": "date_idea",
            "share": "share",
            "red_flags": "red_flags",
            "pet": "pet",
            "important": "important",
            "temperament": "temperament",
        }
        field = mapping.get(step)
        if field:
            if not text:
                return None, "Пожалуйста, ответьте одним сообщением."
            updates[field] = text
    return updates, None


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    load_dotenv()

    token = get_env("VK_GROUP_TOKEN", required=True)
    group_id = int(get_env("VK_GROUP_ID", required=True))
    db_path = get_env("DB_PATH", default="bot.sqlite3")
    fields = get_env("VK_FIELDS", default="").strip()
    if not fields:
        fields = "sex,bdate"
    api_version = get_env("VK_API_VERSION", default="").strip() or None
    windows = load_windows()

    if api_version:
        vk_session = vk_api.VkApi(token=token, api_version=api_version)
    else:
        vk_session = vk_api.VkApi(token=token)

    api = vk_session.get_api()
    longpoll = VkBotLongPoll(vk_session, group_id)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    logging.info("Bot started. Listening for messages...")

    for event in longpoll.listen():
        if event.type != VkBotEventType.MESSAGE_NEW:
            continue

        message = get_message(event)
        if not message:
            continue

        user_id = message.get("from_id")
        if not user_id or user_id <= 0:
            continue

        text = (message.get("text") or "").strip()
        user_row = get_user(conn, user_id, platform="vk")
        exists = user_row is not None
        status = user_row["status"] if user_row is not None else None
        status = status or "registered"
        now = datetime.now()
        reg_start, reg_end = windows["registration"]
        chat_start, chat_end = windows["chat"]

        if not exists:
            if is_registration_command(text):
                if not in_window(now, reg_start, reg_end):
                    send_message(api, user_id, "Регистрация завершена.")
                    continue
                try:
                    profile = fetch_profile(api, user_id, fields)
                except Exception as exc:
                    logging.warning("Failed to fetch profile for user_id=%s: %s", user_id, exc)
                    profile = None
                try:
                    insert_user(
                        conn,
                        user_id,
                        profile,
                        message,
                        status="pre_registered",
                        registration_step="bio",
                        platform="vk",
                    )
                    send_message(
                        api,
                        user_id,
                        f"Спасибо! {prompt_for_step('bio', {'age': None, 'sex': None})}",
                    )
                    logging.info("Pre-registered user_id=%s", user_id)
                except sqlite3.IntegrityError:
                    send_message(api, user_id, "Вы уже зарегистрированы.")
                continue

            send_message(api, user_id, build_description(windows))
            continue

        if status != "registered":
            if not in_window(now, reg_start, reg_end):
                send_message(api, user_id, build_status_message(now, windows))
                continue

            steps = build_steps(user_row["age"], user_row["sex"])
            current_step = user_row["registration_step"] or steps[0]
            if current_step not in steps:
                current_step = steps[0]

            if is_registration_command(text):
                send_message(api, user_id, prompt_for_step(current_step, user_row))
                continue

            updates, error = apply_step_answer(user_row, current_step, text)
            if error:
                send_message(api, user_id, error)
                continue

            next_step = None
            if current_step in steps:
                idx = steps.index(current_step)
                if idx + 1 < len(steps):
                    next_step = steps[idx + 1]

            if next_step is None:
                updates["status"] = "registered"
                updates["registration_step"] = None
                update_user(conn, user_id, updates, message=message, platform="vk")
                send_message(api, user_id, "Отлично! Вы зарегистрированы.")
                logging.info("Registered user_id=%s", user_id)
                continue

            updates["registration_step"] = next_step
            update_user(conn, user_id, updates, message=message, platform="vk")
            send_message(api, user_id, prompt_for_step(next_step, user_row))
            continue

        if is_registration_command(text):
            send_message(api, user_id, "Вы уже зарегистрированы.")
            continue

        if in_window(now, chat_start, chat_end):
            partner_id = get_partner_id(conn, user_id, platform="vk")
            if not partner_id:
                send_message(api, user_id, "Пара не найдена. Ожидайте, пожалуйста.")
                continue
            if text:
                send_message(api, partner_id, text)
            continue

        send_message(api, user_id, build_status_message(now, windows))


if __name__ == "__main__":
    main()
