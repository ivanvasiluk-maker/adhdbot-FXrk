# ============================================================
# DB.PY — Все функции работы с БД
# ============================================================

import json
import time
import logging
import os
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import aiosqlite

from sheets_sync import sanitize_event_data

# Logging
log = logging.getLogger("bot")

# Global test switch to unlock features without paywalls
TEST_MODE = os.getenv("TEST_MODE", "").lower() in {"1", "true", "yes", "on", "debug"}

# ============================================================
# 4) DB: schema + CRUD
# ============================================================

USER_FIELDS = [
    "user_id",
    "chat_id",
    "name",
    "trainer_key",
    "input_mode",
    "stage",
    "bucket",
    "analysis_json",
    "plan_json",
    "pending_skill_id",
    "pending_skill_day",
    "today_target",
    "day",
    "created_at",
    "points",
    "level",
    "streak",
    "last_active",
    "plan_overrides_json",
    "trial_days",
    "trial_phase",
    "payment_status",
    "free_mode",
    "paid_until",
    "last_payment_click",
    "is_test_user",
    "fast_forward_enabled",
    "last_morning_checkin_date",
    "last_evening_checkin_date",
    "notifications_enabled",
    "timezone",
    "reactivation_count",
    "pending_plan_change",
    "crisis_count",
    "test_answers",
    "done_count",
    "return_count",
    "analysis_retry_count",
    "has_started_training",
    "last_offer_shown_at",
    "profile_json",
    "last_micro_habit_id",
    "last_micro_habit_date",
    "micro_habit_json",
]

EVENT_NAME_ALIASES = {
    "crisis_open": "crisis_clicked",
    "crisis_message": "crisis_clicked",
    "done": "action_done",
    "return": "action_done",
    "not_done": "action_failed",
    "downscale_done": "action_done",
    "downscale_triggered": "action_downscaled",
    "day1_started": "diagnosis_started",
    "analysis_action_started": "diagnosis_started",
    "trainer_chosen": "trainer_selected",
}


EVENT_EXTRA_COLS = {
    "event_name": "TEXT",
    "event_data": "TEXT",
    "created_at": "TEXT",
    "synced": "INTEGER DEFAULT 0",
    "sync_attempts": "INTEGER DEFAULT 0",
    "last_sync_error": "TEXT",
    # Legacy columns kept for compatibility with existing analytics code.
    "ts": "REAL",
    "event": "TEXT",
    "meta": "TEXT",
    "stage": "TEXT",
}


async def ensure_events_schema(db: aiosqlite.Connection):
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            event_name TEXT,
            event_data TEXT,
            stage TEXT,
            created_at TEXT,
            synced INTEGER DEFAULT 0,
            sync_attempts INTEGER DEFAULT 0,
            last_sync_error TEXT,
            ts REAL,
            event TEXT,
            meta TEXT
        )
        """
    )
    cur = await db.execute("PRAGMA table_info(events)")
    cols = [r[1] for r in await cur.fetchall()]
    for col, ctype in EVENT_EXTRA_COLS.items():
        if col not in cols:
            await db.execute(f"ALTER TABLE events ADD COLUMN {col} {ctype}")

    await db.execute("UPDATE events SET event_name = event WHERE event_name IS NULL AND event IS NOT NULL")
    await db.execute("UPDATE events SET event_data = meta WHERE event_data IS NULL AND meta IS NOT NULL")
    await db.execute("UPDATE events SET created_at = datetime(ts, 'unixepoch') WHERE created_at IS NULL AND ts IS NOT NULL")
    await db.execute("UPDATE events SET synced = 0 WHERE synced IS NULL")
    await db.execute("UPDATE events SET sync_attempts = 0 WHERE sync_attempts IS NULL")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_events_synced ON events(synced, id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_events_user_id ON events(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_events_name ON events(event_name)")


def default_user(uid: int) -> Dict[str, Any]:
    """Создать нового пользователя с дефолтными значениями"""
    return {
        "user_id": uid,
        "chat_id": uid,
        "name": None,
        "trainer_key": "marsha",
        "input_mode": "text",   # text | voice | test
        "stage": "start",
        "bucket": "mixed",
        "analysis_json": None,
        "plan_json": None,
        "pending_skill_id": None,
        "pending_skill_day": None,
        "today_target": None,
        "day": 1,
        "points": 0,
        "level": 1,
        "streak": 0,
        "last_active": 0.0,
        "plan_overrides_json": None,
        "trial_days": 3,
        "trial_phase": "paid" if TEST_MODE else "trial3",
        "payment_status": "paid" if TEST_MODE else "trial",
        "free_mode": 0,
        "paid_until": None,
        "last_payment_click": None,
        "is_test_user": 0,
        "fast_forward_enabled": 0,
        "last_morning_checkin_date": None,
        "last_evening_checkin_date": None,
        "notifications_enabled": 1,
        "timezone": "Europe/Vilnius",
        "reactivation_count": 0,
        "pending_plan_change": None,
        "crisis_count": 0,
        "created_at": time.time(),
        "test_answers": [],  # Временное хранилище для ответов теста
        "done_count": 0,
        "return_count": 0,
        "analysis_retry_count": 0,
        "has_started_training": 0,  # Флаг: 1 если юзер начал день 1
        "last_offer_shown_at": None,
        "profile_json": {},
        "last_micro_habit_id": None,
        "last_micro_habit_date": None,
        "micro_habit_json": None,
    }

async def init_db(db_path: str):
    """Инициализация БД"""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER,
                name TEXT,
                trainer_key TEXT,
                input_mode TEXT,
                stage TEXT,
                bucket TEXT,
                analysis_json TEXT,
                plan_json TEXT,
                pending_skill_id TEXT,
                pending_skill_day INTEGER,
                today_target TEXT,
                day INTEGER,
                created_at REAL,
                points INTEGER,
                level INTEGER,
                streak INTEGER,
                last_active REAL,
                plan_overrides_json TEXT,
                trial_days INTEGER,
                trial_phase TEXT,
                payment_status TEXT,
                free_mode INTEGER,
                paid_until TEXT,
                last_payment_click TEXT,
                is_test_user INTEGER DEFAULT 0,
                fast_forward_enabled INTEGER DEFAULT 0,
                last_morning_checkin_date TEXT,
                last_evening_checkin_date TEXT,
                notifications_enabled INTEGER DEFAULT 1,
                timezone TEXT DEFAULT 'Europe/Vilnius',
                reactivation_count INTEGER DEFAULT 0,
                pending_plan_change TEXT,
                crisis_count INTEGER,
                test_answers TEXT,
                done_count INTEGER,
                return_count INTEGER,
                analysis_retry_count INTEGER,
                has_started_training INTEGER,
                last_offer_shown_at TEXT,
                profile_json TEXT DEFAULT '{}',
                last_micro_habit_id TEXT,
                last_micro_habit_date TEXT,
                micro_habit_json TEXT
            )
            """
        )
        await ensure_events_schema(db)
        await db.commit()

async def get_user(uid: int, db_path: str) -> Dict[str, Any]:
    """Получить пользователя из БД"""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row:
            u = default_user(uid)
            await save_user(u, db_path)
            return u

        cols = [description[0] for description in cur.description] if cur.description else []
        if cols:
            u = dict(zip(cols, row))
        else:
            u = dict(row) if hasattr(row, 'keys') else {}
        
        # Deserialize test_answers if stored as JSON string
        if 'test_answers' in u and u.get('test_answers'):
            try:
                u['test_answers'] = json.loads(u['test_answers']) if isinstance(u['test_answers'], str) else u['test_answers']
            except Exception:
                u['test_answers'] = []
        else:
            u['test_answers'] = []
        return u

async def save_user(u: Dict[str, Any], db_path: str):
    """Сохранить пользователя в БД"""
    cols = USER_FIELDS
    vals = []
    for c in cols:
        v = u.get(c)
        # Serialize lists/dicts to JSON for storage
        if isinstance(v, (list, dict)):
            try:
                v = json.dumps(v, ensure_ascii=False)
            except Exception:
                v = None
        vals.append(v)
    placeholders = ",".join(["?"] * len(cols))
    cols_sql = ",".join(cols)

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            f"INSERT OR REPLACE INTO users ({cols_sql}) VALUES ({placeholders})",
            tuple(vals),
        )
        await db.commit()

# ============================================================
# DB MIGRATION + EVENTS (аналитика) + GAMIFY FIELDS
# ============================================================

EXTRA_USER_COLS = {
    "points": "INTEGER",
    "level": "INTEGER",
    "streak": "INTEGER",
    "last_active": "REAL",
    "plan_overrides_json": "TEXT",   # правки плана после кризиса
    "trial_days": "INTEGER",         # 3 или 7
    "trial_phase": "TEXT",           # "trial3" / "trial7" / "paid" / ...
    "payment_status": "TEXT",        # "trial" / "paid" / "manual_pending" / ...
    "free_mode": "INTEGER",          # 1 если пользователь мягко отказался от оплаты
    "paid_until": "TEXT",
    "last_payment_click": "TEXT",
    "is_test_user": "INTEGER DEFAULT 0",
    "fast_forward_enabled": "INTEGER DEFAULT 0",
    "last_morning_checkin_date": "TEXT",
    "last_evening_checkin_date": "TEXT",
    "notifications_enabled": "INTEGER DEFAULT 1",
    "timezone": "TEXT DEFAULT 'Europe/Vilnius'",
    "reactivation_count": "INTEGER DEFAULT 0",
    "pending_plan_change": "TEXT",   # отложенная правка плана после кризиса
    "crisis_count": "INTEGER",       # лимит в trial
    "test_answers": "TEXT",
    "done_count": "INTEGER",
    "return_count": "INTEGER",
    "analysis_retry_count": "INTEGER",  # сколько раз пользователь сказал "ты меня не понял"
    "has_started_training": "INTEGER",  # 1 если юзер начал день 1
    "pending_skill_id": "TEXT",
    "pending_skill_day": "INTEGER",
    "today_target": "TEXT",
    "last_offer_shown_at": "TEXT",
    "profile_json": "TEXT DEFAULT '{}'",
    "last_micro_habit_id": "TEXT",
    "last_micro_habit_date": "TEXT",
    "micro_habit_json": "TEXT"
}

async def migrate_db(db_path: str):
    """Мигрировать БД структуру"""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("PRAGMA table_info(users)")
        cols = [r[1] for r in await cur.fetchall()]

        for col, ctype in EXTRA_USER_COLS.items():
            if col not in cols:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {ctype}")

        await ensure_events_schema(db)

        await db.commit()

async def log_event(
    user_id: int,
    event_name: str,
    event_data: dict = None,
    stage: str = None,
    db_path: str = "bot.db",
    sheets_webhook_url: str = "",
):
    """Log event to SQLite only; Sheets sync happens asynchronously in the background.

    Supports the legacy call shape log_event(user_id, stage, event, meta, db_path, webhook)
    while accepting the new shape log_event(user_id, event_name, event_data=None, stage=None).
    Never raises into user-facing bot flows.
    """
    try:
        # Backward compatibility for existing calls: (user_id, stage, event, meta, db_path, webhook).
        if isinstance(event_data, str) and (stage is None or isinstance(stage, dict)):
            legacy_stage = event_name
            legacy_event = event_data
            legacy_meta = stage if isinstance(stage, dict) else {}
            stage = legacy_stage
            event_name = legacy_event
            event_data = legacy_meta

        clean_data = sanitize_event_data(event_data or {})
        event_name = EVENT_NAME_ALIASES.get(event_name, event_name)
        if stage and "stage" not in clean_data:
            clean_data["stage"] = stage

        event_data_s = json.dumps(clean_data, ensure_ascii=False)
        ts = time.time()
        created_at = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(db_path) as db:
            await ensure_events_schema(db)
            await db.execute(
                """
                INSERT INTO events(
                    user_id, event_name, event_data, stage, created_at,
                    synced, sync_attempts, last_sync_error, ts, event, meta
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    user_id,
                    event_name,
                    event_data_s,
                    stage,
                    created_at,
                    0,
                    0,
                    None,
                    ts,
                    event_name,
                    event_data_s,
                ),
            )
            await db.commit()
    except Exception as e:
        log.warning("log_event failed: %s", e)



PATTERN_LABELS = {
    "perfectionism_start_block": "идеальный образ результата делает вход слишком дорогим",
    "entry_too_large": "первый шаг ощущается слишком большим",
    "micro_entry_block": "даже подготовка к старту воспринимается как задача",
    "start_avoidance": "сложно войти в действие",
    "anxiety_avoidance": "тревога делает вход в задачу небезопасным",
    "boredom_avoidance": "нет быстрого подкрепления, и мозг теряет интерес",
}

REASON_LABELS = {
    "fear_of_bad_result": "страх сделать плохо или неидеально",
    "task_too_big": "задача воспринимается слишком большой",
    "unclear_first_step": "неясен первый физический шаг",
    "low_energy": "мало ресурса для входа",
    "no_visible_result": "не видно быстрого результата",
}

SKILL_LABELS = {
    "open_only": "открыть задачу без требования работать",
    "task_naming": "назвать задачу одним словом",
    "ninety_sec_start": "90 секунд входа",
    "bad_first_step": "плохой первый шаг",
    "restart_after_break": "возврат после срыва",
}


def label(mapping: dict, value: Optional[str], fallback: str) -> str:
    if not value:
        return fallback
    return mapping.get(value, value)


async def get_user_profile(user_id: int, db_path: str = "bot.db") -> dict:
    user = await get_user(user_id, db_path)
    raw = user.get("profile_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


async def update_user_profile(user_id: int, patch: dict, db_path: str = "bot.db") -> dict:
    profile = await get_user_profile(user_id, db_path)
    profile.update(patch or {})
    profile["updated_at"] = datetime.utcnow().isoformat()

    profile_json = json.dumps(profile, ensure_ascii=False)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE users SET profile_json = ? WHERE user_id = ?",
            (profile_json, user_id),
        )
        await db.commit()
    return profile


def render_short_user_map(profile: dict, name: Optional[str] = None) -> str:
    main_pattern = label(PATTERN_LABELS, profile.get("main_pattern"), "застревание перед действием")
    reason = label(REASON_LABELS, profile.get("avoidance_reason"), "неопределённость / перегруз")
    trigger = profile.get("emotional_trigger") or "напряжение перед стартом"
    skill = label(SKILL_LABELS, profile.get("best_skill"), "маленький вход в задачу")

    return f"""🧭 Твоя предварительная карта

Пока это не диагноз, а рабочая гипотеза по твоим действиям.

Главный паттерн:
{main_pattern}

Что часто запускает избегание:
{reason}

Что может сбивать:
{trigger}

Что уже похоже помогает:
{skill}

Дальше карта будет уточняться по тому, что ты реально пробуешь."""

def gamify_apply(u: dict, delta_points: int, reason: str):
    """Применить геймификацию"""
    u["points"] = int(u.get("points") or 0) + int(delta_points)
    u["level"] = max(1, int(u.get("points") or 0) // 10 + 1)

    now = time.time()
    last = float(u.get("last_active") or 0.0)
    if now - last > 18 * 3600:
        u["streak"] = 1
    else:
        u["streak"] = int(u.get("streak") or 0) + 1
    u["last_active"] = now

def is_paid(u: dict) -> bool:
    """Проверить, платит ли пользователь"""
    if TEST_MODE:
        return True
    return u.get("payment_status") == "paid" or u.get("trial_phase") == "paid"

def should_ping(u: dict, hours: int) -> bool:
    """Проверить, нужно ли пинговать пользователя"""
    try:
        last = float(u.get("last_active") or 0)
    except (TypeError, ValueError):
        last = 0.0
    return time.time() - last > hours * 3600
