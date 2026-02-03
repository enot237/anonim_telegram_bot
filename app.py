import json
import os
import random
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from dotenv import load_dotenv
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory


def get_env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        raise SystemExit(f"Missing required env var: {name}")
    return value


def get_db_path():
    return get_env("DB_PATH", default="bot.sqlite3")


def connect_db():
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def ensure_schema(conn):
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


def row_to_dict(row):
    data = dict(row)
    profile_json = data.get("profile_json")
    if profile_json:
        try:
            data["profile"] = json.loads(profile_json)
        except json.JSONDecodeError:
            data["profile"] = None
    else:
        data["profile"] = None
    return data


def parse_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_role(req):
    provided = (req.args.get("pass") or "").strip()
    if not provided:
        return None
    manager = get_env("MANAGER_PASS", default="").strip()
    admin = get_env("ADMIN_PASS", default="").strip()
    if admin and provided == admin:
        return "admin"
    if manager and provided == manager:
        return "manager"
    return None


def is_authorized(req):
    return get_role(req) is not None


def require_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_authorized(request):
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)

    return wrapper


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if get_role(request) != "admin":
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)

    return wrapper


def utc_now_iso():
    return datetime.now(tz=timezone.utc).isoformat()


MODE_OPTIONS = {"off", "registration", "prep", "chat", "finished"}


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(conn, key, value):
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value, utc_now_iso()),
    )
    conn.commit()


def normalize_payload(payload, allow_user_id=False):
    allowed = {
        "platform": "text",
        "first_name": "text",
        "last_name": "text",
        "sex": "int",
        "profile_json": "text",
        "status": "text",
        "bio": "text",
        "registration_step": "text",
        "age": "int",
        "looking_for": "text",
        "love_definition": "text",
        "zodiac": "text",
        "date_idea": "text",
        "share": "text",
        "red_flags": "text",
        "pet": "text",
        "important": "text",
        "temperament": "text",
        "registered_at": "text",
        "updated_at": "text",
        "last_message_id": "int",
        "last_message_at": "text",
        "last_message_text": "text",
        "last_peer_id": "int",
    }
    if allow_user_id:
        allowed["user_id"] = "int"

    cleaned = {}
    for key, kind in allowed.items():
        if key not in payload:
            continue
        value = payload.get(key)
        if value is None:
            cleaned[key] = None
            continue
        if kind == "int":
            cleaned[key] = parse_int(value, None)
        else:
            cleaned[key] = str(value)
    return cleaned


def parse_id_list(raw):
    if not raw:
        return []
    parts = [part.strip() for part in raw.replace("\n", ",").replace(" ", ",").split(",")]
    ids = []
    for part in parts:
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        ids.append(value)
    seen = set()
    unique = []
    for value in ids:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def get_tg_admin_ids():
    return parse_id_list(get_env("TG_ADMIN_IDS", default=""))


def tg_send_message(token, chat_id, text, parse_mode="Markdown"):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_obj = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=10) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else ""
        return False, error_body or str(exc)
    except Exception as exc:
        return False, str(exc)

    try:
        result = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return False, "Invalid Telegram response"
    if not result.get("ok"):
        return False, result.get("description", "Unknown Telegram error")
    return True, None


def vk_send_message(token, user_id, text):
    version = get_env("VK_API_VERSION", default="5.199") or "5.199"
    params = {
        "access_token": token,
        "v": version,
        "user_id": user_id,
        "random_id": random.randint(1, 2_000_000_000),
        "message": text,
    }
    data = urllib.parse.urlencode(params).encode("utf-8")
    request_obj = urllib.request.Request(
        "https://api.vk.com/method/messages.send",
        data=data,
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=10) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else ""
        return False, error_body or str(exc)
    except Exception as exc:
        return False, str(exc)

    try:
        result = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return False, "Invalid VK response"
    if "error" in result:
        error_msg = result.get("error", {}).get("error_msg", "VK error")
        return False, error_msg
    return True, None


app = Flask(__name__, static_folder="static", static_url_path="/static")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


@app.get("/api/settings")
@require_admin
def settings_get():
    with connect_db() as conn:
        mode = get_setting(conn, "mode", default="off")
    if mode not in MODE_OPTIONS:
        mode = "off"
    return jsonify({"mode": mode})


@app.put("/api/settings")
@require_admin
def settings_update():
    payload = request.get_json(silent=True) or {}
    mode = (payload.get("mode") or "").strip().lower()
    if mode not in MODE_OPTIONS:
        return jsonify({"error": "Invalid mode"}), 400
    with connect_db() as conn:
        set_setting(conn, "mode", mode)
    return jsonify({"mode": mode})


@app.get("/api/users")
@require_auth
def users_list():
    limit = min(parse_int(request.args.get("limit", 50), 50), 1000)
    offset = max(parse_int(request.args.get("offset", 0), 0), 0)
    role = get_role(request)
    chat_start = get_env("CHAT_START", default="").strip()
    chat_end = get_env("CHAT_END", default="").strip()

    with connect_db() as conn:
        rows = conn.execute(
            """
            SELECT users.id, users.user_id, users.platform, users.first_name, users.last_name, users.sex, users.status, users.bio,
                   users.registration_step, users.age, users.looking_for, users.love_definition, users.zodiac,
                   users.date_idea, users.share, users.red_flags, users.pet, users.important, users.temperament,
                   users.registered_at, users.updated_at, users.last_message_id, users.last_message_at,
                   users.last_message_text, users.last_peer_id, users.profile_json,
                   CASE
                       WHEN pairs.user1_id = users.id THEN pairs.user2_id
                       WHEN pairs.user2_id = users.id THEN pairs.user1_id
                       ELSE NULL
                   END AS partner_id
            FROM users
            LEFT JOIN pairs ON users.id = pairs.user1_id OR users.id = pairs.user2_id
            ORDER BY users.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN sex = 2 THEN 1 ELSE 0 END) AS men,
                SUM(CASE WHEN sex = 1 THEN 1 ELSE 0 END) AS women
            FROM users
            """
        ).fetchone()

    users = [row_to_dict(row) for row in rows]
    return jsonify(
        {
            "items": users,
            "limit": limit,
            "offset": offset,
            "count": len(users),
            "role": role,
            "chat_start": chat_start,
            "chat_end": chat_end,
            "stats": {
                "men": counts[0] if counts and counts[0] is not None else 0,
                "women": counts[1] if counts and counts[1] is not None else 0,
            },
        }
    )


@app.get("/api/users/<int:user_id>")
@require_auth
def users_get(user_id):
    with connect_db() as conn:
        row = conn.execute(
            """
            SELECT users.id, users.user_id, users.platform, users.first_name, users.last_name, users.sex, users.status, users.bio,
                   users.registration_step, users.age, users.looking_for, users.love_definition, users.zodiac,
                   users.date_idea, users.share, users.red_flags, users.pet, users.important, users.temperament,
                   users.registered_at, users.updated_at, users.last_message_id, users.last_message_at,
                   users.last_message_text, users.last_peer_id, users.profile_json,
                   CASE
                       WHEN pairs.user1_id = users.id THEN pairs.user2_id
                       WHEN pairs.user2_id = users.id THEN pairs.user1_id
                       ELSE NULL
                   END AS partner_id
            FROM users
            LEFT JOIN pairs ON users.id = pairs.user1_id OR users.id = pairs.user2_id
            WHERE users.id = ?
            """,
            (user_id,),
        ).fetchone()

    if not row:
        return jsonify({"error": "User not found"}), 404
    return jsonify(row_to_dict(row))


@app.post("/api/users")
@require_auth
def users_create():
    payload = request.get_json(silent=True) or {}
    data = normalize_payload(payload, allow_user_id=True)
    user_id = data.get("user_id")
    if user_id is None:
        return jsonify({"error": "user_id is required"}), 400
    if not data.get("platform"):
        data["platform"] = "vk"

    if "registered_at" not in data:
        data["registered_at"] = utc_now_iso()
    if "updated_at" not in data:
        data["updated_at"] = utc_now_iso()

    columns = list(data.keys())
    values = [data[col] for col in columns]
    placeholders = ", ".join(["?"] * len(columns))

    with connect_db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM users WHERE platform = ? AND user_id = ?",
            (data["platform"], user_id),
        ).fetchone()
        if exists:
            return jsonify({"error": "User already exists"}), 409

        cursor = conn.execute(
            f"INSERT INTO users ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        conn.commit()
        new_id = cursor.lastrowid

        row = conn.execute(
            """
            SELECT users.id, users.user_id, users.platform, users.first_name, users.last_name, users.sex, users.status, users.bio,
                   users.registration_step, users.age, users.looking_for, users.love_definition, users.zodiac,
                   users.date_idea, users.share, users.red_flags, users.pet, users.important, users.temperament,
                   users.registered_at, users.updated_at, users.last_message_id, users.last_message_at,
                   users.last_message_text, users.last_peer_id, users.profile_json,
                   CASE
                       WHEN pairs.user1_id = users.id THEN pairs.user2_id
                       WHEN pairs.user2_id = users.id THEN pairs.user1_id
                       ELSE NULL
                   END AS partner_id
            FROM users
            LEFT JOIN pairs ON users.id = pairs.user1_id OR users.id = pairs.user2_id
            WHERE users.id = ?
            """,
            (new_id,),
        ).fetchone()

    return jsonify(row_to_dict(row)), 201


@app.put("/api/users/<int:user_id>")
@require_auth
def users_update(user_id):
    payload = request.get_json(silent=True) or {}
    data = normalize_payload(payload, allow_user_id=False)
    if not data:
        return jsonify({"error": "No fields to update"}), 400
    data["updated_at"] = utc_now_iso()

    columns = list(data.keys())
    assignments = ", ".join([f"{col} = ?" for col in columns])
    values = [data[col] for col in columns]

    with connect_db() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
        if not exists:
            return jsonify({"error": "User not found"}), 404

        conn.execute(
            f"UPDATE users SET {assignments} WHERE id = ?",
            [*values, user_id],
        )
        conn.commit()

        row = conn.execute(
            """
            SELECT users.id, users.user_id, users.platform, users.first_name, users.last_name, users.sex, users.status, users.bio,
                   users.registration_step, users.age, users.looking_for, users.love_definition, users.zodiac,
                   users.date_idea, users.share, users.red_flags, users.pet, users.important, users.temperament,
                   users.registered_at, users.updated_at, users.last_message_id, users.last_message_at,
                   users.last_message_text, users.last_peer_id, users.profile_json,
                   CASE
                       WHEN pairs.user1_id = users.id THEN pairs.user2_id
                       WHEN pairs.user2_id = users.id THEN pairs.user1_id
                       ELSE NULL
                   END AS partner_id
            FROM users
            LEFT JOIN pairs ON users.id = pairs.user1_id OR users.id = pairs.user2_id
            WHERE users.id = ?
            """,
            (user_id,),
        ).fetchone()

    return jsonify(row_to_dict(row))


@app.delete("/api/users/<int:user_id>")
@require_auth
def users_delete(user_id):
    with connect_db() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone()
        if not exists:
            return jsonify({"error": "User not found"}), 404
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    return jsonify({"ok": True})


def broadcast_tg_to_ids(text, chat_ids):
    token = get_env("TG_BOT_TOKEN", default="").strip()
    if not token:
        return None, {"error": "TG_BOT_TOKEN is not configured"}

    sent = 0
    failed = {}
    for chat_id in chat_ids:
        ok, error = tg_send_message(token, chat_id, text)
        if ok:
            sent += 1
        else:
            failed[str(chat_id)] = error or "send failed"
    return sent, failed


def build_ping_message():
    return (
        "Мероприятие уже началось! 🎉\n"
        "Ваша пара ждёт — напишите сообщение, и бот передаст его собеседнику."
    )


@app.post("/api/ping/<int:user_id>")
@require_auth
def ping_user(user_id):
    with connect_db() as conn:
        row = conn.execute(
            "SELECT user_id, platform FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return jsonify({"error": "User not found"}), 404

    target_id = row[0]
    platform = row[1]
    message = build_ping_message()

    if platform == "tg":
        token = get_env("TG_BOT_TOKEN", default="").strip()
        if not token:
            return jsonify({"error": "TG_BOT_TOKEN is not configured"}), 500
        ok, error = tg_send_message(token, target_id, message)
        if not ok:
            return jsonify({"error": error or "Telegram send failed"}), 500
    elif platform == "vk":
        token = get_env("VK_GROUP_TOKEN", default="").strip()
        if not token:
            return jsonify({"error": "VK_GROUP_TOKEN is not configured"}), 500
        ok, error = vk_send_message(token, target_id, message)
        if not ok:
            return jsonify({"error": error or "VK send failed"}), 500
    else:
        return jsonify({"error": "Unsupported platform"}), 400

    return jsonify({"ok": True, "platform": platform, "user_id": target_id})


@app.post("/api/broadcast/tg/test")
@require_admin
def broadcast_tg_test():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    admin_ids = get_tg_admin_ids()
    if not admin_ids:
        return jsonify({"error": "TG_ADMIN_IDS is empty"}), 400

    sent, failed = broadcast_tg_to_ids(text, admin_ids)
    if sent is None:
        return jsonify(failed), 500

    return jsonify(
        {
            "ok": True,
            "total": len(admin_ids),
            "sent": sent,
            "failed": failed,
        }
    )


@app.post("/api/broadcast/tg/all")
@require_admin
def broadcast_tg_all():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    with connect_db() as conn:
        rows = conn.execute("SELECT user_id FROM users WHERE platform = 'tg'").fetchall()
    chat_ids = [row[0] for row in rows if row and row[0] is not None]
    unique_ids = []
    seen = set()
    for chat_id in chat_ids:
        if chat_id in seen:
            continue
        seen.add(chat_id)
        unique_ids.append(chat_id)

    sent, failed = broadcast_tg_to_ids(text, unique_ids)
    if sent is None:
        return jsonify(failed), 500

    return jsonify(
        {
            "ok": True,
            "total": len(unique_ids),
            "sent": sent,
            "failed": failed,
        }
    )


@app.post("/api/pairs")
@require_auth
def pairs_create():
    payload = request.get_json(silent=True) or {}
    user1_id = parse_int(payload.get("user1_id"), None)
    user2_id = parse_int(payload.get("user2_id"), None)
    if not user1_id or not user2_id:
        return jsonify({"error": "user1_id and user2_id are required"}), 400
    if user1_id == user2_id:
        return jsonify({"error": "Users must be different"}), 400

    with connect_db() as conn:
        rows = conn.execute(
            "SELECT id, platform FROM users WHERE id IN (?, ?)",
            (user1_id, user2_id),
        ).fetchall()
        if len(rows) != 2:
            return jsonify({"error": "Both users must exist"}), 404
        platforms = {row[1] for row in rows}
        if len(platforms) != 1:
            return jsonify({"error": "Users must be on the same platform"}), 400

        existing = conn.execute(
            """
            SELECT user1_id, user2_id
            FROM pairs
            WHERE user1_id IN (?, ?) OR user2_id IN (?, ?)
            """,
            (user1_id, user2_id, user1_id, user2_id),
        ).fetchone()
        if existing:
            return jsonify({"error": "One of the users is already paired"}), 409

        created_at = utc_now_iso()
        conn.execute(
            "INSERT INTO pairs (user1_id, user2_id, created_at) VALUES (?, ?, ?)",
            (user1_id, user2_id, created_at),
        )
        conn.commit()

    return jsonify({"user1_id": user1_id, "user2_id": user2_id, "created_at": created_at}), 201


@app.delete("/api/pairs/<int:user_id>")
@require_auth
def pairs_delete(user_id):
    with connect_db() as conn:
        row = conn.execute(
            "SELECT user1_id, user2_id FROM pairs WHERE user1_id = ? OR user2_id = ?",
            (user_id, user_id),
        ).fetchone()
        if not row:
            return jsonify({"error": "Pair not found"}), 404
        conn.execute(
            "DELETE FROM pairs WHERE user1_id = ? OR user2_id = ?",
            (user_id, user_id),
        )
        conn.commit()
    return jsonify({"ok": True})


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


def main():
    load_dotenv()
    host = get_env("API_HOST", default="127.0.0.1")
    port = int(get_env("API_PORT", default="8000"))
    debug = get_env("API_DEBUG", default="").lower() in {"1", "true", "yes"}

    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
