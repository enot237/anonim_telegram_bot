import json
import logging
import os
import random
import re
import sqlite3
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

MODE_TTL_SECONDS = 10
MODE_LABELS = {
    "off": "Ожидание запуска",
    "registration": "Регистрация",
    "prep": "Подготовка пар",
    "chat": "Общение",
    "finished": "Завершено",
}
ALLOWED_MODES = set(MODE_LABELS.keys())
_MODE_CACHE = {"value": None, "expires_at": 0.0}


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
            last_peer_id INTEGER,
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT
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


def insert_user(conn, user_id, profile, message, status, bio=None, registration_step=None, platform="tg"):
    first_name, last_name, sex = extract_base_fields(profile)
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
            None,
            now_iso,
            now_iso,
            last_message_id,
            last_message_at,
            last_message_text,
            last_peer_id,
        ),
    )
    conn.commit()


def get_user(conn, user_id, platform="tg"):
    return conn.execute(
        """
        SELECT id, user_id, status, registration_step, age, sex, bio, first_name, last_name,
               looking_for, love_definition, zodiac, date_idea, share, red_flags, pet, important, temperament
        FROM users
        WHERE user_id = ? AND platform = ?
        """,
        (user_id, platform),
    ).fetchone()


def is_registration_command(text):
    return text.strip().lower() == "зарегистрироваться"


def is_start_command(text):
    if not text:
        return False
    cmd = text.strip().split()[0].lower()
    return cmd == "/start" or cmd.startswith("/start@")


def get_mode(conn):
    now = time.monotonic()
    cached = _MODE_CACHE.get("value")
    if cached and now < _MODE_CACHE.get("expires_at", 0):
        return cached
    row = conn.execute("SELECT value FROM settings WHERE key = ?", ("mode",)).fetchone()
    mode = row[0] if row else "off"
    if mode not in ALLOWED_MODES:
        mode = "off"
    _MODE_CACHE["value"] = mode
    _MODE_CACHE["expires_at"] = now + MODE_TTL_SECONDS
    return mode


REVIEW_CONFIRM_TEXT = "Всё корректно, зарегистрироваться"
REVIEW_EDIT_TEXT = "Изменить данные"
REVIEW_BACK_TEXT = "Вернуться к проверке"


EDIT_FIELDS = [
    ("О себе", "bio"),
    ("Возраст", "age"),
    ("Пол", "sex"),
    ("Кого ты ищешь", "looking_for"),
    ("В чем любовь", "love_definition"),
    ("Знак зодиака", "zodiac"),
    ("Время вместе", "date_idea"),
    ("Что разделить", "share"),
    ("Ред флаги", "red_flags"),
    ("Питомец", "pet"),
    ("Важно в собеседнике", "important"),
    ("Темперамент", "temperament"),
]


def build_registration_keyboard():
    return ReplyKeyboardMarkup(
        [["Зарегистрироваться"]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def build_done_keyboard():
    return ReplyKeyboardMarkup(
        [["отлично"]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def build_choice_keyboard(options):
    return ReplyKeyboardMarkup(
        options,
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def build_review_keyboard():
    return ReplyKeyboardMarkup(
        [[REVIEW_CONFIRM_TEXT], [REVIEW_EDIT_TEXT]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def build_edit_keyboard():
    rows = []
    row = []
    for label, _ in EDIT_FIELDS:
        row.append(label)
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([REVIEW_BACK_TEXT])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def normalize_text(text):
    return text.strip().lower().replace("ё", "е")


def is_review_confirm(text):
    return normalize_text(text) == normalize_text(REVIEW_CONFIRM_TEXT)


def is_review_edit(text):
    return normalize_text(text) == normalize_text(REVIEW_EDIT_TEXT)


def is_review_back(text):
    return normalize_text(text) == normalize_text(REVIEW_BACK_TEXT)


def get_edit_step(text):
    key = normalize_text(text)
    mapping = {normalize_text(label): step for label, step in EDIT_FIELDS}
    return mapping.get(key)


def get_step_keyboard(step, user):
    if step == "review":
        return build_review_keyboard()
    if step == "edit_select":
        return build_edit_keyboard()
    base_step = step[5:] if step.startswith("edit_") else step
    if base_step == "sex":
        return build_choice_keyboard([["Мужчина", "Женщина"]])
    if base_step == "sex_confirm":
        sex = user["sex"]
        if sex == 2:
            return build_choice_keyboard([["Да", "Женщина"]])
        if sex == 1:
            return build_choice_keyboard([["Да", "Мужчина"]])
        return build_choice_keyboard([["Мужчина", "Женщина"]])
    if base_step == "temperament":
        return build_choice_keyboard([["Экстраверт", "Амбиверт"], ["Интроверт"]])
    return ReplyKeyboardRemove()


def prompt_for_edit_step(step, user):
    if step == "age":
        return "Сколько вам лет? Укажите числом."
    if step == "sex":
        return "Укажите ваш пол: мужчина или женщина."
    return prompt_for_step(step, user)


def build_review_text(user):
    data = dict(user) if user else {}
    sex_value = data.get("sex")
    sex_text = "—"
    if sex_value == 2:
        sex_text = "Мужчина"
    elif sex_value == 1:
        sex_text = "Женщина"
    age_text = data.get("age") or "—"

    def field(value):
        return value or "—"

    lines = [
        "Проверьте вашу анкету:",
        f"О себе: {field(data.get('bio'))}",
        f"Возраст: {age_text}",
        f"Пол: {sex_text}",
        f"Кого ты ищешь: {field(data.get('looking_for'))}",
        f"В чем любовь: {field(data.get('love_definition'))}",
        f"Знак зодиака: {field(data.get('zodiac'))}",
        f"Время вместе: {field(data.get('date_idea'))}",
        f"Что разделить: {field(data.get('share'))}",
        f"Ред флаги: {field(data.get('red_flags'))}",
        f"Питомец: {field(data.get('pet'))}",
        f"Важно в собеседнике: {field(data.get('important'))}",
        f"Темперамент: {field(data.get('temperament'))}",
    ]
    return "\n".join(lines)


def build_start_message(now, windows, mode):
    reg_start, reg_end = windows["registration"]
    prep_start, prep_end = windows["prep"]
    chat_start, chat_end = windows["chat"]

    reg_range = format_range(reg_start, reg_end)
    prep_range = format_range(prep_start, prep_end)
    chat_range = format_range(chat_start, chat_end)
    mode_label = MODE_LABELS.get(mode, MODE_LABELS["off"])

    lines = [
        "Привет! Это бот для анонимного общения на 14 февраля.",
        "",
        "Как всё будет происходить:",
        f"1) Регистрация ({reg_range}) — отвечаете на вопросы анкеты.",
        f"2) Подготовка пар ({prep_range}) — мы формируем пары.",
        f"3) Общение ({chat_range}) — можно писать своей половинке или другу.",
        "",
        f"Сейчас режим: {mode_label}.",
        "",
        "Желаем найти приятного собеседника или половинку!",
    ]
    if mode == "registration":
        lines.append("Нажмите «Зарегистрироваться» ниже, чтобы начать.")
    elif mode == "off":
        lines.append("Бот еще не включен. Ожидайте запуска.")
    elif mode == "prep":
        lines.append("Сейчас идет подготовка пар. Регистрация закрыта.")
    elif mode == "chat":
        lines.append("Если вы зарегистрированы, можно писать своей паре.")
    elif mode == "finished":
        lines.append("Этап общения завершен. Спасибо за участие!")
    else:
        lines.append("Регистрация открыта в указанные даты.")
    return "\n".join(lines)


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
        "Нажмите кнопку «Зарегистрироваться» ниже.\n"
        "После этого ответьте на несколько вопросов — начнем с короткого сообщения о себе."
    )


def build_status_message(mode, windows):
    reg_start, reg_end = windows["registration"]
    prep_start, prep_end = windows["prep"]
    chat_start, chat_end = windows["chat"]
    reg_range = format_range(reg_start, reg_end)
    prep_range = format_range(prep_start, prep_end)
    chat_range = format_range(chat_start, chat_end)
    mode_label = MODE_LABELS.get(mode, MODE_LABELS["off"])
    return (
        f"Сейчас включен режим: {mode_label}.\n"
        f"Регистрация: {reg_range}.\n"
        f"Подготовка пар: {prep_range}.\n"
        f"Общение: {chat_range}."
    )


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


def build_non_text_retry_message(step, user):
    if step == "review":
        return f"{build_review_text(user)}\n\nВыберите вариант кнопкой ниже."
    if step == "edit_select":
        return "Выберите пункт из списка."
    base_step = step[5:] if step.startswith("edit_") else step
    prompt = (
        prompt_for_edit_step(base_step, user)
        if step.startswith("edit_")
        else prompt_for_step(base_step, user)
    )
    if base_step in {"age", "age_confirm", "sex", "sex_confirm", "temperament"}:
        return prompt
    return f"Напишите ответ текстовым сообщением.\n{prompt}"


def has_non_text_content(message):
    if not message:
        return False
    return any(
        [
            message.photo,
            message.document,
            message.video,
            message.audio,
            message.animation,
            message.sticker,
            message.voice,
            message.video_note,
            message.location,
            message.contact,
        ]
    )


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
                return None, "напишите ответ текстовым сообщением"
            updates[field] = text
    return updates, None


def update_user(conn, user_id, updates, message=None):
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
        [*values, user_id, "tg"],
    )
    conn.commit()


def get_partner_id(conn, user_id, platform="tg"):
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


def tg_profile(user):
    if not user:
        return {}
    return {
        "id": user.id,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "language_code": user.language_code,
        "is_bot": user.is_bot,
    }


def tg_message_dict(message):
    if not message:
        return {}
    return {
        "id": message.message_id,
        "date": int(message.date.timestamp()) if message.date else None,
        "text": message.text or "",
        "peer_id": message.chat_id,
    }


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    text = (update.message.text or "").strip()
    has_non_text = has_non_text_content(update.message)
    sticker = update.message.sticker
    voice = update.message.voice
    video_note = update.message.video_note
    user_id = update.effective_user.id

    conn = sqlite3.connect(get_env("DB_PATH", default="bot.sqlite3"))
    conn.row_factory = sqlite3.Row
    init_db(conn)

    user_row = get_user(conn, user_id, platform="tg")
    exists = user_row is not None
    status = user_row["status"] if user_row is not None else None
    registration_step = user_row["registration_step"] if user_row is not None else None
    is_registered = status == "registered" and not registration_step

    now = datetime.now()
    windows = load_windows()
    mode = get_mode(conn)

    message_data = tg_message_dict(update.message)

    if is_start_command(text):
        reply_markup = ReplyKeyboardRemove()
        if mode == "registration":
            if not exists:
                reply_markup = build_registration_keyboard()
            elif not is_registered:
                steps = build_steps(user_row["age"], user_row["sex"])
                current_step = registration_step or steps[0]
                reply_markup = get_step_keyboard(current_step, user_row)
        await update.message.reply_text(
            build_start_message(now, windows, mode),
            reply_markup=reply_markup,
        )
        return

    if mode == "registration":
        if not exists:
            if is_registration_command(text):
                profile = tg_profile(update.effective_user)
                try:
                    insert_user(
                        conn,
                        user_id,
                        profile,
                        message_data,
                        status="pre_registered",
                        registration_step="bio",
                        platform="tg",
                    )
                    await update.message.reply_text(
                        f"Спасибо! {prompt_for_step('bio', {'age': None, 'sex': None})}",
                        reply_markup=ReplyKeyboardRemove(),
                    )
                    logging.info("Pre-registered user_id=%s (tg)", user_id)
                except sqlite3.IntegrityError:
                    await update.message.reply_text("Вы уже зарегистрированы.")
                return

            await update.message.reply_text(
                build_description(windows),
                reply_markup=build_registration_keyboard(),
            )
            return

        if not is_registered:
            steps = build_steps(user_row["age"], user_row["sex"])
            current_step = registration_step or steps[0]
            if has_non_text:
                await update.message.reply_text(
                    build_non_text_retry_message(current_step, user_row),
                    reply_markup=get_step_keyboard(current_step, user_row),
                )
                return
            if current_step == "review":
                if is_review_confirm(text):
                    update_user(
                        conn,
                        user_id,
                        {"status": "registered", "registration_step": None},
                        message=message_data,
                    )
                    await update.message.reply_text(
                        "Отлично! Вы зарегистрированы.",
                        reply_markup=build_done_keyboard(),
                    )
                    logging.info("Registered user_id=%s (tg)", user_id)
                    return
                if is_review_edit(text):
                    update_user(conn, user_id, {"registration_step": "edit_select"}, message=message_data)
                    await update.message.reply_text(
                        "Что вы хотите изменить?",
                        reply_markup=build_edit_keyboard(),
                    )
                    return
                update_user(conn, user_id, {}, message=message_data)
                refreshed = get_user(conn, user_id, platform="tg")
                await update.message.reply_text(
                    build_review_text(refreshed),
                    reply_markup=build_review_keyboard(),
                )
                return

            if current_step == "edit_select":
                if is_review_back(text):
                    update_user(conn, user_id, {"registration_step": "review"}, message=message_data)
                    refreshed = get_user(conn, user_id, platform="tg")
                    await update.message.reply_text(
                        build_review_text(refreshed),
                        reply_markup=build_review_keyboard(),
                    )
                    return
                selected_step = get_edit_step(text)
                if not selected_step:
                    await update.message.reply_text(
                        "Выберите пункт из списка.",
                        reply_markup=build_edit_keyboard(),
                    )
                    return
                update_user(
                    conn,
                    user_id,
                    {"registration_step": f"edit_{selected_step}"},
                    message=message_data,
                )
                await update.message.reply_text(
                    prompt_for_edit_step(selected_step, user_row),
                    reply_markup=get_step_keyboard(f"edit_{selected_step}", user_row),
                )
                return

            if current_step.startswith("edit_"):
                base_step = current_step[5:]
                if is_review_back(text):
                    update_user(conn, user_id, {"registration_step": "review"}, message=message_data)
                    refreshed = get_user(conn, user_id, platform="tg")
                    await update.message.reply_text(
                        build_review_text(refreshed),
                        reply_markup=build_review_keyboard(),
                    )
                    return
                updates, error = apply_step_answer(user_row, base_step, text)
                if error:
                    await update.message.reply_text(
                        error,
                        reply_markup=get_step_keyboard(current_step, user_row),
                    )
                    return
                updates["registration_step"] = "review"
                update_user(conn, user_id, updates, message=message_data)
                refreshed = get_user(conn, user_id, platform="tg")
                await update.message.reply_text(
                    build_review_text(refreshed),
                    reply_markup=build_review_keyboard(),
                )
                return

            if current_step not in steps:
                current_step = steps[0]

            if is_registration_command(text):
                await update.message.reply_text(
                    prompt_for_step(current_step, user_row),
                    reply_markup=get_step_keyboard(current_step, user_row),
                )
                return

            updates, error = apply_step_answer(user_row, current_step, text)
            if error:
                await update.message.reply_text(
                    error,
                    reply_markup=get_step_keyboard(current_step, user_row),
                )
                return

            next_step = None
            if current_step in steps:
                idx = steps.index(current_step)
                if idx + 1 < len(steps):
                    next_step = steps[idx + 1]

                if next_step is None:
                    updates["registration_step"] = "review"
                    update_user(conn, user_id, updates, message=message_data)
                    await update.message.reply_text(
                        build_review_text(get_user(conn, user_id, platform="tg")),
                        reply_markup=build_review_keyboard(),
                    )
                    return

                updates["registration_step"] = next_step
                update_user(conn, user_id, updates, message=message_data)
                await update.message.reply_text(
                    prompt_for_step(next_step, user_row),
                    reply_markup=get_step_keyboard(next_step, user_row),
                )
                return

        if is_registration_command(text):
            await update.message.reply_text("Вы уже зарегистрированы.")
            return

        await update.message.reply_text(build_status_message(mode, windows), reply_markup=ReplyKeyboardRemove())
        return

    if mode == "chat":
        if not exists or not is_registered:
            await update.message.reply_text(
                build_status_message(mode, windows),
                reply_markup=ReplyKeyboardRemove(),
            )
            return
        partner_id = get_partner_id(conn, user_id, platform="tg")
        if not partner_id:
            await update.message.reply_text("Пара не найдена. Ожидайте, пожалуйста.")
            return
        update_user(conn, user_id, {}, message=message_data)
        if update.message.photo:
            photo_sizes = update.message.photo
            photo = photo_sizes[-1] if photo_sizes else None
            if photo:
                await context.bot.send_photo(
                    chat_id=partner_id,
                    photo=photo.file_id,
                    caption=update.message.caption or None,
                )
            return
        if video_note:
            await update.message.reply_text("Кружок не может быть доставлен.")
            return
        if sticker:
            await context.bot.send_sticker(chat_id=partner_id, sticker=sticker.file_id)
            return
        if voice:
            await context.bot.send_voice(chat_id=partner_id, voice=voice.file_id)
            return
        if text:
            await context.bot.send_message(chat_id=partner_id, text=text)
        return

    if mode == "off":
        await update.message.reply_text(
            build_start_message(now, windows, mode),
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await update.message.reply_text(build_status_message(mode, windows), reply_markup=ReplyKeyboardRemove())


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()

    token = get_env("TG_BOT_TOKEN", required=True)
    app = ApplicationBuilder().token(token).build()

    app.add_handler(
        MessageHandler(
            filters.TEXT
            | filters.Sticker.ALL
            | filters.VOICE
            | filters.VIDEO_NOTE
            | filters.PHOTO
            | filters.Document.ALL
            | filters.VIDEO
            | filters.AUDIO
            | filters.ANIMATION
            | filters.LOCATION
            | filters.CONTACT
            | filters.COMMAND,
            handle_message,
        )
    )

    logging.info("Telegram bot started. Listening for messages...")
    app.run_polling()


if __name__ == "__main__":
    main()
