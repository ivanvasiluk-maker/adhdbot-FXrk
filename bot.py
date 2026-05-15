# ============================================================
# ADHD SELF-REGULATION TRAINER BOT (REFACTORED)
# Тренеры-коты: Скинни (жёсткий), Марша (мягкая), Бек (аналитик)
# ====================
# СТРУКТУРА:
# - texts.py: все текстовые константы и клавиатуры
# - skills.py: навыки и планы
# - db.py: работа с БД
# - flows.py: основные логические потоки
# ============================================================

import os
import sys
import io
import re
import json
import time
import asyncio
import logging
import threading
import datetime as dt
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, KeyboardButton, ReplyKeyboardMarkup
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from dotenv import load_dotenv

# Import modules
# Keep this as a single import to avoid multiline merge-conflict syntax breaks in deploys.
from texts import *  # noqa: F403,F401
from texts import send_trainer_introduction as send_text_trainer_introduction
from skills import (
    SKILLS_DB,
    get_current_plan,
    build_28_day_plan,
    build_plan,
    propose_plan_override,
    suggest_alternative_skill,
    format_skill,
)
from db import (
    USER_FIELDS, default_user, init_db, migrate_db, get_user, save_user, 
    log_event, gamify_apply, is_paid, EXTRA_USER_COLS
)
from flows import (
    start_day, start_day1, start_day_simple, advance_day, handle_crisis,
    send_trainer_photo_if_any, run_analysis,
    send_weekly_summary, send_progress_report, ai_analyze, ai_analyze_comprehensive,
    _extract_json, clamp_str
)
from nlp_fallback import is_misunderstood, is_too_hard, is_timer_too_hard
from core.engine import (
    get_next_screen as engine_get_next_screen,
    handle_action_result as engine_handle_action_result,
    handle_downscale as engine_handle_downscale,
    should_show_offer as engine_should_show_offer,
)
import sheets_sync as sheets_sync_module

SHEETS_SYNC_ENABLED = getattr(sheets_sync_module, "SHEETS_SYNC_ENABLED", False)
SHEETS_SYNC_INTERVAL_SECONDS = getattr(sheets_sync_module, "SHEETS_SYNC_INTERVAL_SECONDS", 60)
SHEETS_SYNC_BATCH_SIZE = getattr(sheets_sync_module, "SHEETS_SYNC_BATCH_SIZE", 50)

load_dotenv(override=True)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini").strip()
OPENAI_WHISPER_MODEL = os.getenv("OPENAI_WHISPER_MODEL", "whisper-1").strip()
DB_PATH = os.getenv("DB_PATH", "bot.db").strip()
PAYMENT_URL = os.getenv("PAYMENT_URL", "").strip()
PAYMENT_URL_DISCOUNT = os.getenv("PAYMENT_URL_DISCOUNT", "").strip()
PAYMENT_URL_FULL = os.getenv("PAYMENT_URL_FULL", "").strip()
SHEETS_WEBHOOK_URL = os.getenv("SHEETS_WEBHOOK_URL", "").strip()
ADMIN_IDS = {int(x) for x in re.split(r"[,\s]+", os.getenv("ADMIN_IDS", "").strip()) if x.isdigit()}

# Unlock full flow while testing (set TEST_MODE=1)
TEST_MODE = os.getenv("TEST_MODE", "").lower() in {"1", "true", "yes", "on", "debug"}
STARTUP_CHECK = os.getenv("BOT_STARTUP_CHECK", "").lower() in {"1", "true", "yes", "on", "check"}

AI_ANALYSIS_ENABLED = bool(OPENAI_API_KEY)

# Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

log.info("BOT_TOKEN configured: %s", bool(BOT_TOKEN))
log.info("DB_PATH: %s", DB_PATH)

# OpenAI client
openai = None
client = None
if AI_ANALYSIS_ENABLED:
    import importlib.util

    if importlib.util.find_spec("openai") is None:
        log.warning("[AI] OpenAI package is not installed, continuing without AI features")
        AI_ANALYSIS_ENABLED = False
    else:
        import openai as oa

        openai = oa
        try:
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
        except TypeError as e:
            if "proxies" not in str(e) or importlib.util.find_spec("httpx") is None:
                log.warning("[AI] OpenAI client initialization failed: %s", e)
                AI_ANALYSIS_ENABLED = False
            else:
                import httpx

                log.warning("[AI] OpenAI default HTTP client is incompatible with installed httpx; retrying with explicit httpx.Client")
                try:
                    client = openai.OpenAI(api_key=OPENAI_API_KEY, http_client=httpx.Client())
                except Exception as retry_error:
                    log.warning("[AI] OpenAI client initialization retry failed: %s", retry_error)
                    client = None
                    AI_ANALYSIS_ENABLED = False
        except Exception as e:
            log.warning("[AI] OpenAI client initialization failed: %s", e)
            AI_ANALYSIS_ENABLED = False

if AI_ANALYSIS_ENABLED and client:
    log.info("[AI] OpenAI enabled with chat model %s and whisper model %s", OPENAI_CHAT_MODEL, OPENAI_WHISPER_MODEL)
elif not OPENAI_API_KEY:
    log.warning("[AI] OpenAI disabled: OPENAI_API_KEY is missing")
else:
    log.warning("[AI] OpenAI disabled: client initialization failed")


async def ai_micro_reflect(user_text: str, trainer_key: str, client=None, model: str = "gpt-4o-mini") -> str:
    """Короткий отклик на опыт выполнения (1–2 предложения)."""
    user_text = clamp_str(user_text, 600)
    trainer_key = trainer_key or "marsha"

    # Fallback без ИИ
    fallback = {
        "skinny": "Принял. Фиксируем выполнение. Завтра повторим 60–120 сек, без эмоций.",
        "marsha": "Вижу. Спасибо, что поделился. Бережно двигаемся дальше — завтра снова маленький шаг.",
        "beck": "Зафиксировал наблюдение. Это и есть данные для обучения. Завтра повторим и сравним.",
    }
    if not (client and model):
        return fallback.get(trainer_key, fallback["marsha"])

    system = (
        "Ты тренер навыков саморегуляции. Ответь очень коротко (1–2 предложения). "
        "Учитывай стиль: skinny=жестко, marsha=поддержка, beck=логика. "
        "Цель: отразить переживание пользователя и дать крошечный следующий ориентир без давления. "
        "Без эмодзи, без вопросов, без маркеров."
    )
    user = json.dumps({
        "trainer": trainer_key,
        "observation": user_text,
    }, ensure_ascii=False)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.35,
            max_tokens=120,
        )
        content = (resp.choices[0].message.content or "").strip()
        if content:
            return clamp_str(content, 400)
    except Exception as e:
        log.error(f"ai_micro_reflect failed: {e}")
    return fallback.get(trainer_key, fallback["marsha"])

# ============================================================
# ROUTER & HANDLERS
# ============================================================

router = Router()

DOWNSCALE_PATTERN = "initiation_before_tool"
DOWNSCALE_PRIMARY_SKILL = "open_only"
DOWNSCALE_FALLBACK_SKILL = "task_naming"
ACTION_RELATED_STAGES = {
    "training",
    "await_training_target",
    "action_clarification",
    "downscale_action",
    "downscale_name_task",
    "failed_options",
}


def user_is_in_action_loop(u: Dict[str, Any]) -> bool:
    """Пользователь уже после диагностики и находится в тренировочном loop."""
    return bool(u.get("analysis_json") or u.get("plan_json") or u.get("has_started_training")) and u.get("stage") in ACTION_RELATED_STAGES


def _remember_downscale_pattern(u: Dict[str, Any], skill_id: str):
    """Сохранить локальную адаптацию без запуска повторной диагностики."""
    data: Dict[str, Any] = {}
    try:
        if u.get("analysis_json"):
            data = json.loads(u.get("analysis_json") or "{}")
            if not isinstance(data, dict):
                data = {}
    except Exception:
        data = {}
    data["pattern"] = DOWNSCALE_PATTERN
    data["selected_skill"] = skill_id
    u["analysis_json"] = json.dumps(data, ensure_ascii=False)


def _select_downscale_skill(u: Dict[str, Any]) -> str:
    """Выбрать и поставить текущий навык downscale на сегодняшний день."""
    skill_id = DOWNSCALE_PRIMARY_SKILL if DOWNSCALE_PRIMARY_SKILL in SKILLS_DB else DOWNSCALE_FALLBACK_SKILL
    day = int(u.get("day") or 1)
    propose_plan_override(u, day, skill_id)
    u["pending_skill_id"] = None
    u["pending_skill_day"] = None
    _remember_downscale_pattern(u, skill_id)
    return skill_id


async def answer_with_keyboard(m: Message, u: Dict[str, Any], text: str, reply_markup, keyboard_name: str):
    """Send a keyboard only if it respects the <=5 button rule and log it."""
    button_count = keyboard_button_count(reply_markup)
    event_name = "keyboard_shown" if button_count <= 5 else "keyboard_warning"
    await log_event(
        u.get("user_id"),
        u.get("stage", ""),
        event_name,
        {"keyboard": keyboard_name, "button_count": button_count},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    try:
        if button_count > 5:
            log.warning("Keyboard %s has %s buttons; sending text without markup", keyboard_name, button_count)
            await m.answer(text)
            return
        await m.answer(text, reply_markup=reply_markup)
    except Exception as e:
        log.exception("telegram_send_error: %s", e)
        await log_event(
            u.get("user_id"),
            u.get("stage", ""),
            "telegram_send_error",
            {"source": "answer_with_keyboard", "keyboard": keyboard_name},
            DB_PATH,
            SHEETS_WEBHOOK_URL,
        )


async def log_engine_events(u: Dict[str, Any], screen: Dict[str, Any]):
    """Persist events emitted by the UI-independent behavior engine."""
    for event in screen.get("events") or []:
        await log_event(
            u.get("user_id"),
            event.get("stage") or u.get("stage", ""),
            event.get("name", "engine_event"),
            event.get("meta") or {},
            DB_PATH,
            SHEETS_WEBHOOK_URL,
        )


def apply_engine_updates(u: Dict[str, Any], screen: Dict[str, Any]):
    """Apply non-side-effect state updates returned by the behavior engine."""
    for key, value in (screen.get("updates") or {}).items():
        if key.endswith("_delta") or key in {"gamify_reason", "points_delta", "plan_override_day", "selected_skill", "pattern"}:
            continue
        u[key] = value
    if screen.get("next_state"):
        u["stage"] = screen["next_state"]


async def show_route(m: Message, u: Dict[str, Any], source: str):
    """Show the preliminary route only at allowed moments."""
    await log_event(
        u.get("user_id"),
        u.get("stage", ""),
        "route_shown",
        {"source": source},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    await m.answer(month_map_text(u.get("bucket")))


async def show_day3_offer(m: Message, u: Dict[str, Any], source: str):
    """Show paid offer after day 3 completion/summary."""
    u["stage"] = "offer"
    await save_user(u, DB_PATH)
    await log_event(
        u["user_id"],
        "offer",
        "offer_shown",
        {"source": source, "day": int(u.get("day") or 0)},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    await answer_with_keyboard(m, u, day3_offer_text(), kb_pay_choice, "pay_choice")


def should_show_day3_offer(u: Dict[str, Any], day: int) -> bool:
    """Day 3 offer is shown only for unpaid users outside free mode."""
    state = dict(u)
    state["day"] = day
    return engine_should_show_offer(state)


def is_admin(uid: int) -> bool:
    """Admin commands are available only for user IDs listed in ADMIN_IDS."""
    return uid in ADMIN_IDS


def current_skill_id(u: Dict[str, Any]) -> str:
    plan = get_current_plan(u)
    if not plan:
        return ""
    day = int(u.get("day") or 1)
    idx = max(0, min(len(plan) - 1, day - 1))
    return plan[idx]


async def db_health_ok() -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("SELECT 1")
        return True
    except Exception:
        return False




def _parse_event_data(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _event_row_name(row: Dict[str, Any]) -> str:
    return row.get("event_name") or row.get("event") or ""


def _stats_trainer_key(event_data: Dict[str, Any], user: Dict[str, Any] | None = None) -> str:
    key = event_data.get("trainer_key") or event_data.get("trainer") or (user or {}).get("trainer_key") or "unknown"
    return key if key in {"beck", "skinny", "marsha"} else "unknown"


def _stats_skill_id(event_data: Dict[str, Any], user: Dict[str, Any] | None = None) -> str:
    return event_data.get("skill_id") or event_data.get("sid") or event_data.get("skill") or (user or {}).get("pending_skill_id") or ""


async def build_admin_stats_text(db_path: str) -> str:
    today = dt.datetime.now(dt.timezone.utc).date()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        users_rows = [dict(row) for row in await (await db.execute("SELECT * FROM users")).fetchall()]
        event_rows = [dict(row) for row in await (await db.execute("SELECT * FROM events")).fetchall()]

    users_by_id = {int(u["user_id"]): u for u in users_rows if u.get("user_id") is not None}

    def user_created_today(u: Dict[str, Any]) -> bool:
        try:
            created = dt.datetime.fromtimestamp(float(u.get("created_at") or 0), tz=dt.timezone.utc).date()
            return created == today
        except Exception:
            return False

    event_counts = {
        "trainer_selected": 0,
        "diagnosis_completed": 0,
        "analysis_shown": 0,
        "skill_card_shown": 0,
        "action_done": 0,
        "action_failed": 0,
        "action_downscaled": 0,
        "offer_shown": 0,
        "payment_click_20": 0,
        "payment_click_40": 0,
        "payment_declined_soft": 0,
        "crisis_clicked": 0,
    }
    trainer_stats = {
        key: {"users": 0, "action_done": 0, "offer_click": 0}
        for key in ("beck", "skinny", "marsha")
    }
    skill_stats: Dict[str, Dict[str, int]] = {}

    for user in users_rows:
        trainer = user.get("trainer_key") or "marsha"
        if trainer in trainer_stats:
            trainer_stats[trainer]["users"] += 1

    for row in event_rows:
        name = _event_row_name(row)
        data = _parse_event_data(row.get("event_data") or row.get("meta"))
        user = users_by_id.get(int(row.get("user_id") or 0), {})
        if name in event_counts:
            event_counts[name] += 1

        trainer = _stats_trainer_key(data, user)
        if trainer in trainer_stats:
            if name == "action_done":
                trainer_stats[trainer]["action_done"] += 1
            elif name in {"payment_click_20", "payment_click_40"}:
                trainer_stats[trainer]["offer_click"] += 1

        skill_id = _stats_skill_id(data, user)
        if skill_id:
            stats = skill_stats.setdefault(skill_id, {"shown": 0, "done": 0, "failed": 0, "downscaled": 0})
            if name == "skill_card_shown":
                stats["shown"] += 1
            elif name == "action_done":
                stats["done"] += 1
            elif name == "action_failed":
                stats["failed"] += 1
            elif name == "action_downscaled":
                stats["downscaled"] += 1

    top_skills = sorted(
        skill_stats.items(),
        key=lambda item: (item[1]["shown"] + item[1]["done"] + item[1]["failed"] + item[1]["downscaled"], item[0]),
        reverse=True,
    )[:10]

    lines = [
        f"Пользователей всего: {len(users_rows)}",
        f"Новых сегодня: {sum(1 for u in users_rows if user_created_today(u))}",
        f"Выбрали тренера: {event_counts['trainer_selected']}",
        f"Диагностика завершена: {event_counts['diagnosis_completed']}",
        f"Анализ показан: {event_counts['analysis_shown']}",
        f"Навык показан: {event_counts['skill_card_shown']}",
        f"Сделали действие: {event_counts['action_done']}",
        f"Не сделали: {event_counts['action_failed']}",
        f"Downscale: {event_counts['action_downscaled']}",
        f"Дошли до day3: {sum(1 for u in users_rows if int(u.get('day') or 0) >= 3)}",
        f"Offer shown: {event_counts['offer_shown']}",
        f"€20 clicks: {event_counts['payment_click_20']}",
        f"€40 clicks: {event_counts['payment_click_40']}",
        f"Подумаю: {event_counts['payment_declined_soft']}",
        f"Crisis clicked: {event_counts['crisis_clicked']}",
        "",
        "Топ тренеров:",
    ]
    for trainer in ("beck", "skinny", "marsha"):
        stats = trainer_stats[trainer]
        lines.append(f"- {trainer}: {stats['users']} / {stats['action_done']} / {stats['offer_click']}")

    lines.extend(["", "Топ навыков:"])
    if top_skills:
        for skill_id, stats in top_skills:
            lines.append(
                f"- {skill_id} / {stats['shown']} / {stats['done']} / {stats['failed']} / {stats['downscaled']}"
            )
    else:
        lines.append("- нет данных")
    return "\n".join(lines)

async def reset_current_user(uid: int, chat_id: int) -> Dict[str, Any]:
    fresh = default_user(uid)
    fresh["chat_id"] = chat_id
    fresh["stage"] = "ask_name"
    await save_user(fresh, DB_PATH)
    return fresh


async def handle_user_command(m: Message, u: Dict[str, Any], text: str) -> bool:
    """Handle simple user commands; does not require admin access."""
    if not text or not text.startswith("/"):
        return False
    uid = m.from_user.id
    command = text.split(maxsplit=1)[0].lower()

    if command == "/help":
        await log_event(uid, u.get("stage", ""), "help_requested", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(user_help_text())
        return True

    if command == "/progress":
        await log_event(uid, u.get("stage", ""), "progress_requested", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await send_progress_report(m, u, DB_PATH)
        return True

    if command == "/settings":
        await log_event(uid, u.get("stage", ""), "settings_requested", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(settings_text(int(u.get("notifications_enabled") if u.get("notifications_enabled") is not None else 1), u.get("timezone") or "Europe/Vilnius"))
        return True

    if command == "/stop":
        u["notifications_enabled"] = 0
        await save_user(u, DB_PATH)
        await log_event(uid, u.get("stage", ""), "notifications_disabled", {"source": "stop_command"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer("Напоминания отключены. Профиль не удалён. Вернуться можно через /settings или /start_over.")
        return True

    if command == "/start_over":
        fresh = await reset_current_user(uid, m.chat.id)
        await log_event(uid, "onboarding", "start_over", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(
            start_over_confirm_text(),
            reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True),
        )
        return True

    if command == "/crisis":
        u["stage"] = "crisis_choose_mode"
        await save_user(u, DB_PATH)
        await log_event(uid, u.get("stage", ""), "crisis_clicked", {"source": "command"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(m, u, "🆘 Ок. Как удобнее?", kb_crisis_mode, "crisis_mode")
        return True

    return False


async def handle_admin_command(m: Message, u: Dict[str, Any], text: str) -> bool:
    """Handle admin-only test commands. Returns True when command was consumed."""
    uid = m.from_user.id
    command = (text.split(maxsplit=1)[0] if text else "").lower()
    admin_commands = {
        "/testmode_on", "/testmode_off", "/set_day", "/show_offer",
        "/reset_me", "/debug_user", "/health", "/mark_paid", "/mark_free", "/sync_sheets", "/stats",
    }
    if command not in admin_commands:
        return False
    if not is_admin(uid):
        await m.answer("Нет доступа.")
        return True

    if command == "/testmode_on":
        u["is_test_user"] = 1
        u["fast_forward_enabled"] = 1
        await save_user(u, DB_PATH)
        await log_event(uid, u.get("stage", ""), "testmode_on", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer("Тестовый режим включён. Можно проходить дни без ожидания.")
        return True

    if command == "/testmode_off":
        u["is_test_user"] = 0
        u["fast_forward_enabled"] = 0
        await save_user(u, DB_PATH)
        await log_event(uid, u.get("stage", ""), "testmode_off", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer("Тестовый режим выключен.")
        return True

    if command == "/set_day":
        parts = text.split()
        if len(parts) != 2 or not parts[1].isdigit() or int(parts[1]) not in {1, 2, 3}:
            await m.answer("Формат: /set_day 1|2|3")
            return True
        day = int(parts[1])
        u["day"] = day
        u["pending_skill_day"] = None
        u["stage"] = "training"
        await save_user(u, DB_PATH)
        await log_event(uid, "training", "admin_set_day", {"day": day}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(f"День установлен: {day}.")
        return True

    if command == "/show_offer":
        await log_event(uid, u.get("stage", ""), "admin_show_offer", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await show_day3_offer(m, u, "manual_test")
        return True

    if command == "/reset_me":
        fresh = await reset_current_user(uid, m.chat.id)
        await log_event(uid, "admin", "admin_reset_user", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(
            "Сбросил твой профиль до свежего онбординга.\n\n"
            "Как к тебе обращаться? (1 слово)",
            reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True),
        )
        return True

    if command == "/debug_user":
        sid = current_skill_id(u)
        username = getattr(m.from_user, "username", None) or ""
        await m.answer(
            "DEBUG USER\n"
            f"user_id: {uid}\n"
            f"username: @{username if username else '-'}\n"
            f"stage: {u.get('stage')}\n"
            f"day: {u.get('day')}\n"
            f"trainer_key: {u.get('trainer_key')}\n"
            f"bucket: {u.get('bucket')}\n"
            f"current_skill: {sid}\n"
            f"payment_status: {u.get('payment_status')}\n"
            f"paid_until: {u.get('paid_until')}\n"
            f"trial_phase: {u.get('trial_phase')}\n"
            f"last_payment_click: {u.get('last_payment_click')}\n"
            f"free_mode: {u.get('free_mode')}\n"
            f"last_active: {u.get('last_active')}\n"
            f"test_mode: {bool(TEST_MODE or int(u.get('is_test_user') or 0))}"
        )
        return True

    if command == "/health":
        ok = await db_health_ok()
        await log_event(uid, u.get("stage", ""), "health_checked", {"db_ok": ok}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(
            "OK\n"
            f"DB ok {str(ok).lower()}\n"
            f"OpenAI configured {str(bool(OPENAI_API_KEY)).lower()}\n"
            f"Sheets configured {str(bool(SHEETS_WEBHOOK_URL)).lower()}\n"
            f"Sheets sync enabled {str(bool(SHEETS_SYNC_ENABLED)).lower()}\n"
            f"Sheets interval {SHEETS_SYNC_INTERVAL_SECONDS}s batch {SHEETS_SYNC_BATCH_SIZE}\n"
            f"Payments configured {str(bool(PAYMENT_URL_DISCOUNT and PAYMENT_URL_FULL)).lower()}\n"
            f"Testmode {str(bool(TEST_MODE or int(u.get('is_test_user') or 0))).lower()}"
        )
        return True

    if command == "/sync_sheets":
        result = await sheets_sync_module.sync_unsynced_events(DB_PATH, SHEETS_SYNC_BATCH_SIZE)
        await log_event(uid, u.get("stage", ""), "admin_sync_sheets", result, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(
            "Sheets sync\n"
            f"synced: {result.get('synced', 0)}\n"
            f"failed: {result.get('failed', 0)}\n"
            f"error: {result.get('error') or '-'}"
        )
        return True

    if command == "/stats":
        await m.answer(await build_admin_stats_text(DB_PATH))
        return True

    if command == "/mark_paid":
        parts = text.split()
        if len(parts) != 2 or not parts[1].isdigit():
            await m.answer("Формат: /mark_paid <user_id>")
            return True
        target_id = int(parts[1])
        target = await get_user(target_id, DB_PATH)
        target["payment_status"] = "paid"
        target["trial_phase"] = "paid"
        target["free_mode"] = 0
        target["last_payment_click"] = target.get("last_payment_click") or "admin_mark_paid"
        await save_user(target, DB_PATH)
        await log_event(target_id, target.get("stage", ""), "payment_completed", {"source": "admin_mark_paid", "admin_id": uid}, DB_PATH, SHEETS_WEBHOOK_URL)
        await log_event(target_id, target.get("stage", ""), "paid_mode_started", {"source": "admin_mark_paid"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(f"✅ user_id {target_id} помечен как paid.")
        return True

    if command == "/mark_free":
        parts = text.split()
        if len(parts) != 2 or not parts[1].isdigit():
            await m.answer("Формат: /mark_free <user_id>")
            return True
        target_id = int(parts[1])
        target = await get_user(target_id, DB_PATH)
        target["payment_status"] = "free_mode"
        target["trial_phase"] = "trial3"
        target["free_mode"] = 1
        target["paid_until"] = None
        await save_user(target, DB_PATH)
        await log_event(target_id, target.get("stage", ""), "free_mode_started", {"source": "admin_mark_free", "admin_id": uid}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(f"✅ user_id {target_id} переведён в free mode.")
        return True

    return False


def user_timezone(u: Dict[str, Any]):
    try:
        return ZoneInfo(u.get("timezone") or "Europe/Vilnius")
    except Exception:
        return ZoneInfo("Europe/Vilnius")


def local_now_for_user(u: Dict[str, Any]) -> dt.datetime:
    return dt.datetime.now(user_timezone(u))


def in_time_window(now: dt.datetime, start_hour: int, start_minute: int, end_hour: int, end_minute: int) -> bool:
    current = now.time()
    return dt.time(start_hour, start_minute) <= current <= dt.time(end_hour, end_minute)


def user_inactive_over_24h(u: Dict[str, Any], now_ts: float) -> bool:
    try:
        last = float(u.get("last_active") or 0)
    except (TypeError, ValueError):
        last = 0
    return bool(last and now_ts - last > 24 * 3600)


def remember_checkin_state(u: Dict[str, Any], key: str, value: str):
    data: Dict[str, Any] = {}
    try:
        if u.get("analysis_json"):
            data = json.loads(u.get("analysis_json") or "{}")
            if not isinstance(data, dict):
                data = {}
    except Exception:
        data = {}
    data[key] = value
    u["analysis_json"] = json.dumps(data, ensure_ascii=False)


async def ask_today_action(m: Message, u: Dict[str, Any]):
    u["stage"] = "training"
    await save_user(u, DB_PATH)
    await start_day(m, u, int(u.get("day") or 1), DB_PATH, SHEETS_WEBHOOK_URL)


async def send_downscale(m: Message, u: Dict[str, Any], reason: str):
    """Показать уменьшенный action-step внутри текущего тренировочного loop."""
    screen = engine_handle_downscale(u, reason)
    skill_id = screen.get("skill_id") or DOWNSCALE_PRIMARY_SKILL
    day = int(u.get("day") or 1)
    propose_plan_override(u, day, skill_id)
    u["pending_skill_id"] = None
    u["pending_skill_day"] = None
    _remember_downscale_pattern(u, skill_id)
    u["stage"] = screen.get("next_state") or "downscale_action"
    await save_user(u, DB_PATH)
    await log_engine_events(u, screen)
    await answer_with_keyboard(m, u, screen["text"], kb_downscale, "downscale")

@router.message(CommandStart())
async def cmd_start(m: Message):
    uid = m.from_user.id
    u = await get_user(uid, DB_PATH)
    u["chat_id"] = m.chat.id
    await log_event(
        uid,
        "start",
        {
            "telegram_username": getattr(m.from_user, "username", None) or "",
            "telegram_name": getattr(m.from_user, "first_name", None) or getattr(m.from_user, "full_name", "") or "",
            "stage": u.get("stage"),
        },
        db_path=DB_PATH,
    )


    # Новый порядок онбординга:
    # 1. Экраны онбординга
    u["stage"] = "ask_name"
    await save_user(u, DB_PATH)
    await log_event(uid, "onboarding_started", {"stage": u.get("stage")}, db_path=DB_PATH)
    for screen in ONBOARDING_SCREENS:
        await m.answer(screen)
        await asyncio.sleep(0.3)

    # 2. Вопрос имени
    await m.answer(
        "Как к тебе обращаться? (1 слово)",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True),
    )

@router.message()
async def main_flow(m: Message):
    uid = m.from_user.id
    u = await get_user(uid, DB_PATH)
    text = (m.text or "").strip()
    low = text.lower()

    if await handle_admin_command(m, u, text):
        return
    if await handle_user_command(m, u, text):
        return

    morning_answers = {"😐 норм", "😣 тяжело", "🔋 нет сил", "📱 отвлекаюсь", "🚪 не хочу начинать"}
    if u.get("stage") == "morning_checkin" and text in morning_answers:
        remember_checkin_state(u, "last_morning_state", text)
        u["last_active"] = time.time()
        was_reactivation = int(u.get("reactivation_count") or 0) > 0
        u["stage"] = "training"
        await save_user(u, DB_PATH)
        await log_event(uid, "training", "morning_checkin_done", {"state": text}, DB_PATH, SHEETS_WEBHOOK_URL)
        if was_reactivation:
            await log_event(uid, "training", "reactivation_success", {"state": text}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer("Принял. Подберём шаг на сегодня.")
        await ask_today_action(m, u)
        return

    evening_answers = {"✅ сделал", "😐 частично", "❌ не сделал", "↩️ срывался, но возвращался"}
    if u.get("stage") == "evening_checkin" and text in evening_answers:
        remember_checkin_state(u, "last_evening_state", text)
        u["last_active"] = time.time()
        u["stage"] = "training"
        await save_user(u, DB_PATH)
        await log_event(uid, "training", "evening_checkin_done", {"state": text}, DB_PATH, SHEETS_WEBHOOK_URL)
        if text == "✅ сделал":
            await m.answer(trainer_done_response(u.get("trainer_key") or "marsha"))
        elif text == "↩️ срывался, но возвращался":
            await m.answer("Возврат засчитан. Это ключевой навык.")
        elif text == "😐 частично":
            await m.answer("Частично — тоже данные. Завтра уменьшим шаг, если нужно.")
        else:
            await m.answer(trainer_failed_response(u.get("trainer_key") or "marsha"))
        await answer_with_keyboard(m, u, "Что дальше?", kb_training_main, "training_main")
        return

    # Глобальный хук: кризис доступен из любого состояния, но не перебиваем активный кризис-флоу
    if (text == "🆘 Кризис" or "кризис" in low) and u.get("stage") not in {"crisis_choose_mode", "crisis_voice", "crisis_text", "crisis_plan_confirm"}:
        u["stage"] = "crisis_choose_mode"
        await save_user(u, DB_PATH)
        await log_event(u["user_id"], u["stage"], "crisis_open", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(m, u, "🆘 Ок. Как удобнее?", kb_crisis_mode, "crisis_mode")
        return

    # Action-loop clarification/downscale: не запускаем повторную диагностику после старта тренировки
    if user_is_in_action_loop(u):
        if text == "❌ Не сделал" or "не сделал" in low:
            screen = engine_handle_action_result(u, "failed")
            apply_engine_updates(u, screen)
            await save_user(u, DB_PATH)
            await log_engine_events(u, screen)
            await answer_with_keyboard(m, u, screen["text"], kb_failed, "failed")
            return

        if u.get("stage") == "failed_options":
            if text == "😣 Слишком сложно" or is_too_hard(text):
                await send_downscale(m, u, "failed_too_hard")
                return
            if text == "😵 Нет сил" or "нет сил" in low:
                await send_downscale(m, u, "failed_no_energy")
                return
            if text == "📱 Залип" or "залип" in low:
                await send_downscale(m, u, "failed_stuck_phone")
                return
            if text == "🤔 Не понял" or low in {"не понял", "не понимаю", "я не понимаю"}:
                u["stage"] = "training"
                await save_user(u, DB_PATH)
                await log_event(u["user_id"], "training", "dont_understand_clicked", {"source": "failed_options"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await answer_with_keyboard(m, u, simple_explain_text(), kb_microstep, "microstep")
                return
            if is_misunderstood(text):
                u["stage"] = "action_clarification"
                await save_user(u, DB_PATH)
                await answer_with_keyboard(
                    m,
                    u,
                    "Ок. Давай уточним без нового круга.\n\n"
                    "Что именно не так?",
                    kb_action_clarify,
                    "action_clarify",
                )
                return
            await answer_with_keyboard(m, u, "Выбери, что сейчас ближе:", kb_failed, "failed")
            return

        if u.get("stage") == "action_clarification":
            if text == "Слишком сложно" or is_too_hard(text):
                if is_timer_too_hard(text):
                    await log_event(u["user_id"], "training", "too_hard_even_timer", {"text": text[:120]}, DB_PATH, SHEETS_WEBHOOK_URL)
                await log_event(u["user_id"], "training", "clarification_selected", {"choice": "too_hard"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await m.answer(trainer_failed_response(u.get("trainer_key") or "marsha"))
                await send_downscale(m, u, "clarification_too_hard")
                return
            if text == "Не та причина":
                await log_event(u["user_id"], "training", "clarification_selected", {"choice": "wrong_reason"}, DB_PATH, SHEETS_WEBHOOK_URL)
                u["stage"] = "training"
                await save_user(u, DB_PATH)
                await answer_with_keyboard(m, u, "Ок. Причину сейчас не переразбираем. Проверим через действие: какой минимальный вход в задачу возможен?", kb_downscale, "downscale")
                await send_downscale(m, u, "wrong_reason")
                return
            if text == "Не тот навык":
                await log_event(u["user_id"], "training", "clarification_selected", {"choice": "wrong_skill"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await send_downscale(m, u, "wrong_skill")
                return
            if text in {"Я не понимаю", "🤔 Я не понимаю", "🤔 Не понял"}:
                await log_event(u["user_id"], "training", "clarification_selected", {"choice": "dont_understand"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await log_event(u["user_id"], "training", "dont_understand_clicked", {}, DB_PATH, SHEETS_WEBHOOK_URL)
                u["stage"] = "training"
                await save_user(u, DB_PATH)
                await answer_with_keyboard(m, u, simple_explain_text(), kb_microstep, "microstep")
                return
            await answer_with_keyboard(m, u, "Выбери, что именно не так:", kb_action_clarify, "action_clarify")
            return

        if u.get("stage") == "downscale_action":
            if text == "😣 Даже это сложно" or ("даже" in low and "сложно" in low):
                _remember_downscale_pattern(u, DOWNSCALE_FALLBACK_SKILL)
                u["stage"] = "downscale_name_task"
                await save_user(u, DB_PATH)
                await log_event(u["user_id"], "training", "downscale_triggered", {"reason": "even_open_too_hard", "skill": DOWNSCALE_FALLBACK_SKILL}, DB_PATH, SHEETS_WEBHOOK_URL)
                await m.answer(trainer_failed_response(u.get("trainer_key") or "marsha"))
                await answer_with_keyboard(
                    m,
                    u,
                    "Ок. Тогда ещё меньше.\n\n"
                    "Не открывай задачу.\n"
                    "Просто напиши сюда название задачи одним словом.",
                    kb_downscale_name_task,
                    "downscale_name_task",
                )
                return
            if text == "🤔 Зачем так мало?" or "зачем так мало" in low:
                await log_event(u["user_id"], "training", "why_too_small_clicked", {}, DB_PATH, SHEETS_WEBHOOK_URL)
                await answer_with_keyboard(
                    m,
                    u,
                    "Потому что сейчас мы тренируем вход, а не результат.\n\n"
                    "Если вход слишком большой, мозг его блокирует.\n"
                    "Маленький шаг снижает сопротивление.",
                    kb_microstep,
                    "microstep",
                )
                return
            if text in {"💪 Давай действие", "💪 Сделать микрошаг"}:
                await send_downscale(m, u, "microstep_button")
                return
            if text == "✅ Сделал" or text == "✅ Сделал(а)" or ("сделал" in low and "не сделал" not in low):
                await log_event(u["user_id"], "training", "downscale_done", {"stage": "downscale_action", "day": int(u.get("day") or 1)}, DB_PATH, SHEETS_WEBHOOK_URL)
                previous_done = int(u.get("done_count") or 0)
                u["done_count"] = previous_done + 1
                gamify_apply(u, 2, "downscale_done")
                u["stage"] = "waiting_next_day"
                await save_user(u, DB_PATH)
                await m.answer(trainer_done_response(u.get("trainer_key") or "marsha"))
                if previous_done == 0:
                    await show_route(m, u, "first_done")
                if should_show_day3_offer(u, int(u.get("day") or 1)):
                    await show_route(m, u, "day3_summary")
                    await show_day3_offer(m, u, "day3_downscale_done")
                    return
                await answer_with_keyboard(m, u, "Что дальше?", kb_done, "done")
                return

        if u.get("stage") == "downscale_name_task":
            if text == "✅ Написал" or "написал" in low or (text and text != "🆘 Кризис"):
                await log_event(u["user_id"], "training", "downscale_done", {"stage": "downscale_name_task", "day": int(u.get("day") or 1)}, DB_PATH, SHEETS_WEBHOOK_URL)
                previous_done = int(u.get("done_count") or 0)
                u["done_count"] = previous_done + 1
                gamify_apply(u, 2, "downscale_done")
                u["stage"] = "waiting_next_day"
                await save_user(u, DB_PATH)
                await m.answer(trainer_done_response(u.get("trainer_key") or "marsha"))
                if previous_done == 0:
                    await show_route(m, u, "first_done")
                if should_show_day3_offer(u, int(u.get("day") or 1)):
                    await show_route(m, u, "day3_summary")
                    await show_day3_offer(m, u, "day3_downscale_name_done")
                    return
                await answer_with_keyboard(m, u, "Одно слово — это уже контакт с задачей. Что дальше?", kb_done, "done")
                return
        if text in {"💪 Давай действие", "💪 Сделать микрошаг"}:
            await send_downscale(m, u, "microstep_button")
            return
        if is_timer_too_hard(text):
            await log_event(u["user_id"], "training", "too_hard_even_timer", {"text": text[:120]}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer(trainer_failed_response(u.get("trainer_key") or "marsha"))
            await send_downscale(m, u, "timer_too_hard")
            return
        if is_too_hard(text):
            await m.answer(trainer_failed_response(u.get("trainer_key") or "marsha"))
            await send_downscale(m, u, "too_hard_text")
            return
        if text in {"🤔 Я не понимаю", "🤔 Не понял"} or low in {"я не понимаю", "не понял", "не понимаю"}:
            await log_event(u["user_id"], "training", "dont_understand_clicked", {"source": "action_loop"}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, simple_explain_text(), kb_microstep, "microstep")
            return
        if is_misunderstood(text):
            u["stage"] = "action_clarification"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "training", "misunderstood_clicked", {"text": text[:120]}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(
                m,
                u,
                "Ок. Давай уточним без нового круга.\n\n"
                "Что именно не так?",
                kb_action_clarify,
                "action_clarify",
            )
            return

    # Пост-выполнение: только два варианта, без перегруза кнопками
    if u.get("stage") == "waiting_next_day":
        trainer_key = u.get("trainer_key") or "marsha"
        if text == "🔁 Ещё круг" or "еще круг" in low or "ещё круг" in low:
            screen = engine_get_next_screen(u, {"type": "repeat_skill_card"})
            apply_engine_updates(u, screen)
            await save_user(u, DB_PATH)
            await log_engine_events(u, screen)
            await answer_with_keyboard(m, u, screen["text"], kb_skill_card, "skill_card")
            return
        if text == "🌙 На сегодня хватит" or "хватит" in low:
            current_day = int(u.get("day") or 1)
            plan = get_current_plan(u)
            max_day = len(plan) if plan else current_day + 1
            next_day = min(current_day + 1, max_day)
            u["day"] = next_day
            u["pending_skill_id"] = None
            u["pending_skill_day"] = None
            u["today_target"] = None
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            await log_event(
                u["user_id"],
                "training",
                "day_complete",
                {"completed_day": current_day, "next_day": next_day},
                DB_PATH,
                SHEETS_WEBHOOK_URL,
            )
            await log_event(u["user_id"], "training", "done_enough_today", {"completed_day": current_day, "next_day": next_day}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, f"Ок. День {current_day} закрыт. В следующий раз начнём день {next_day}.", kb_training_main, "training_main")
            return
        reply = await ai_micro_reflect(text or "", trainer_key, client, OPENAI_CHAT_MODEL)
        await log_event(u["user_id"], "training", "post_done_reflect", {"len": len(text or "")}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(m, u, trainer_say(trainer_key, reply), kb_done, "done")
        return


    # ask_name
    if u["stage"] == "ask_name":
        if text and text.lower() != "пропустить":
            u["name"] = text[:50]
        await log_event(u["user_id"], "onboarding", "name_provided", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        u["stage"] = "await_trainer"
        await save_user(u, DB_PATH)
        # Показываем всех тренеров
        trainers_intro = (
            "\U0001F408\u200D\u2B1B Тренеры: кто будет вести тебя?\n\n"
            "🤍 Марша — мягкая и поддерживающая. Помогает возвращаться без стыда и не бросать после срывов.\n"
            "🐈‍⬛ Скинни — прямой и требовательный. Даст чёткий маршрут и жёсткие рамки, без лишних разговоров.\n"
            "🧠 Бек — аналитичный и спокойный. Объяснит, что происходит и почему это работает.\n\n"
            "Выбери стиль, который тебе ближе — его можно будет сменить."
        )
        await m.answer(trainers_intro)
        await m.answer("Ок. Выбери тренера:", reply_markup=kb_trainers)
        return

    # ============================================================
    # TRAINER SELECTION
    # ============================================================
    if u["stage"] == "await_trainer":
        low = text.lower().strip()
        chosen = None
        if text == "🐈‍⬛ Скинни (жёстко)" or "скинни" in low:
            chosen = "skinny"
        elif text == "🐈 Марша (мягко)" or "марша" in low:
            chosen = "marsha"
        elif text == "🐈‍🦁 Бек (аналитично)" or "бек" in low:
            chosen = "beck"
        if not chosen:
            await m.answer("Выбери кнопкой 👇", reply_markup=kb_trainers)
            return
        u["trainer_key"] = chosen
        u["stage"] = "notification_consent"
        await save_user(u, DB_PATH)
        await log_event(u["user_id"], "onboarding", "trainer_selected", {"trainer_key": chosen}, DB_PATH, SHEETS_WEBHOOK_URL)
        # Описание и фото тренера
        await send_trainer_photo_if_any(m.chat.id, chosen, BOT_TOKEN)
        await send_text_trainer_introduction(m, u)
        await answer_with_keyboard(m, u, notifications_consent_text(), kb_notifications_consent, "notifications_consent")
        return

    if u.get("stage") == "notification_consent":
        low = (text or "").lower()
        if text == "✅ Ок, можно писать" or "можно" in low or "ок" in low:
            u["notifications_enabled"] = 1
            u["stage"] = "trainer_intro"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "onboarding", "notifications_consent_set", {"notifications_enabled": 1}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Готов начать разбор и перейти к первому дню?", reply_markup=kb_yes_no)
            return
        if text == "🔕 Без напоминаний" or "без" in low or "напомин" in low:
            u["notifications_enabled"] = 0
            u["stage"] = "trainer_intro"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "onboarding", "notifications_consent_set", {"notifications_enabled": 0}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Ок, без напоминаний. Готов начать разбор и перейти к первому дню?", reply_markup=kb_yes_no)
            return
        await answer_with_keyboard(m, u, notifications_consent_text(), kb_notifications_consent, "notifications_consent")
        return

    # ============================================================
    # TRAINER INTRO CONFIRM
    # ============================================================
    if u["stage"] == "trainer_intro":
        low = (text or "").lower()
        if "да" in low:
            # Диагностика: выбор способа
            u["stage"] = "await_input_mode"
            await save_user(u, DB_PATH)
            await m.answer(
                f"{u['name']}, как удобнее пройти диагностику?",
                reply_markup=kb_input_mode
            )
            return
        if "нет" in low:
            u["stage"] = "await_trainer"
            await save_user(u, DB_PATH)
            await m.answer("Выбери другого тренера 👇", reply_markup=kb_trainers)
            return
        await m.answer("Выбери: ✅ Да / ❌ Нет", reply_markup=kb_yes_no)
        return

    # ============================================================
    # INPUT MODE SELECTION
    # ============================================================
    if u["stage"] == "await_input_mode":
        low = text.lower().strip()
        if text == "🧠 Диагностика текстом" or "текст" in low:
            u["input_mode"] = "text"
            u["stage"] = "await_problem_text"
            await save_user(u, DB_PATH)
            await m.answer("Ок. Напиши 2–5 предложений: что сейчас мешает делать важное?", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True))
            return
        if text == "🎙 Диагностика голосом" or "голос" in low:
            u["input_mode"] = "voice"
            u["stage"] = "await_problem_voice"
            await save_user(u, DB_PATH)
            await m.answer("Ок. Пришли голосовое (10–30 сек): что сейчас мешает делать важное?", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Назад")]], resize_keyboard=True))
            return
        if text == "❓ Быстрый тест (5 вопросов)" or "тест" in low:
            u["input_mode"] = "test"
            u["stage"] = "taking_test"
            u["test_answers"] = []
            await save_user(u, DB_PATH)
            first_q = TEST_QUESTIONS[0]
            msg = f"❓ Вопрос 1/5:\n\n{first_q['text']}"
            await m.answer(msg, reply_markup=create_test_question_keyboard(1))
            return
        await m.answer("Выбери кнопкой 👇", reply_markup=kb_input_mode)
        return

    # Legacy stage: не показываем карту автоматически, сразу ведём к первому действию
    if u.get("stage") == "diagnosis_done":
        u["stage"] = "training"
        u["day"] = 1
        await save_user(u, DB_PATH)
        await start_day(m, u, 1, DB_PATH, SHEETS_WEBHOOK_URL)
        return

    # choose_input_mode
    if u["stage"] == "choose_input_mode":
        low = text.lower().strip()
        if text == "🧠 Диагностика текстом" or "текст" in low:
            u["input_mode"] = "text"
            u["stage"] = "await_problem_text"
            await save_user(u, DB_PATH)
            await m.answer("Ок. Напиши 2–5 предложений: что сейчас мешает делать важное?", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True))
            return
        if text == "🎙 Диагностика голосом" or "голос" in low:
            u["input_mode"] = "voice"
            u["stage"] = "await_problem_voice"
            await save_user(u, DB_PATH)
            await m.answer("Ок. Пришли голосовое (10–30 сек): что сейчас мешает делать важное?", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Назад")]], resize_keyboard=True))
            return
        if text == "❓ Быстрый тест (5 вопросов)" or "тест" in low:
            u["input_mode"] = "test"
            u["stage"] = "taking_test"
            u["test_answers"] = []
            await save_user(u, DB_PATH)
            first_q = TEST_QUESTIONS[0]
            msg = f"❓ Вопрос 1/5:\n\n{first_q['text']}"
            await m.answer(msg, reply_markup=create_test_question_keyboard(1))
            return
        await m.answer("Выбери кнопкой 👇", reply_markup=kb_input_mode)
        return

    # await_problem_text
    if u["stage"] == "await_problem_text":
        if m.voice:
            await m.answer("Слушаю голосовое и перевожу в текст…")
            user_text = await whisper_transcribe(m)
            if not user_text:
                await m.answer("Не смог разобрать голосовое. Напиши, пожалуйста, текстом 1–3 предложения.")
                return
            await m.answer(f"Распознал: {clamp_str(user_text, 700)}")
        elif not text or text.lower() == "пропустить":
            user_text = "Прокрастинация/избегание, хочу начать, но откладываю."
        else:
            user_text = text
        u["analysis_json"] = json.dumps({"user_text": clamp_str(user_text, 1000)}, ensure_ascii=False)
        u["stage"] = "run_analysis"
        await save_user(u, DB_PATH)
        await m.answer("Ок. Делаю подробный разбор…")
        await run_analysis(m, u, user_text, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
        return

    # await_problem_voice
    if u["stage"] == "await_problem_voice":
        if text and text.lower() == "назад":
            u["stage"] = "choose_input_mode"
            await save_user(u, DB_PATH)
            await m.answer("Ок. Выбери режим:", reply_markup=kb_input_mode)
            return
        if not m.voice:
            await m.answer("Пришли голосовое 🎙")
            return
        await m.answer("Слушаю голосовое и перевожу в текст…")
        t = await whisper_transcribe(m)
        if not t:
            u["stage"] = "await_problem_text"
            await save_user(u, DB_PATH)
            await m.answer("Не смог разобрать голосовое. Напиши, пожалуйста, текстом 1–3 предложения.")
            return
        await m.answer(f"Распознал: {clamp_str(t, 700)}")
        u["analysis_json"] = json.dumps({"user_text": clamp_str(t, 1000)}, ensure_ascii=False)
        u["stage"] = "run_analysis"
        await save_user(u, DB_PATH)
        await m.answer("Ок. Делаю подробный разбор…")
        await run_analysis(m, u, t, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
        return

    # analysis_contract
    if u.get("stage") == "analysis_contract":
        low = (text or "").lower()

        # Обработка кнопки "Принимаю контракт" и ответов "Да" после подробного текста
        if (
            text == "📜 Принимаю контракт на 4 недели"
            or "принимаю" in low
            or "принимают" in low
            or text == "✅ Да"
            or low.strip() == "да"
        ):
            u["stage"] = "training"
            u["day"] = 1
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "analysis", "day1_started", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer(guarantee_block(u.get("trainer_key")), reply_markup=kb_yes_no)
            # Запуск первого дня сразу
            await start_day(m, u, 1, DB_PATH, SHEETS_WEBHOOK_URL)
            return

        if text == "❌ Нет" or "нет" in low:
            await m.answer("Ок. Вернёмся позже.")
            return

    # analysis_map
    if u.get("stage") == "analysis_map":
        low = (text or "").lower()
        if "принимаю" in low or "принимают" in low:
            u["stage"] = "training"
            u["day"] = 1
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "analysis", "day1_started", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            # Явно запускаем первый день
            await start_day(m, u, 1, DB_PATH, SHEETS_WEBHOOK_URL)
            return
        if "нет" in low:
            await m.answer("Ок. Без гарантии — не стартуем.")
            return

    # confirm_analysis
    if u["stage"] == "confirm_analysis":
        low = text.lower()
        if "давай действие" in low or text == "💪 Давай действие":
            await log_event(u["user_id"], "analysis", "analysis_action_started", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["stage"] = "training"
            u["day"] = 1
            await save_user(u, DB_PATH)
            await start_day(m, u, 1, DB_PATH, SHEETS_WEBHOOK_URL)
            return
        if "подробнее" in low or text == "📚 Подробнее":
            await log_event(u["user_id"], "analysis", "analysis_details_requested", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer(analysis_contract_short(u.get("name") or "друг", u.get("trainer_key"), u.get("bucket")))
            await answer_with_keyboard(m, u, "Что дальше?", kb_analysis_confirm, "analysis")
            return
        if "в точку" in low or (text == "✅ Да, в точку"):
            await log_event(u["user_id"], "analysis", "analysis_accepted", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["stage"] = "analysis_contract"
            await save_user(u, DB_PATH)
            contract_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Подробнее о контракте")], [KeyboardButton(text="📜 Принимаю контракт на 4 недели")]], resize_keyboard=True)
            await answer_with_keyboard(
                m,
                u,
                analysis_contract_short(u.get("name") or "друг", u.get("trainer_key"), u.get("bucket")),
                contract_kb,
                "analysis_contract",
            )
            return
        if "немного" in low or "не так" in low or "не совсем" in low or text in {"🤔 Немного не так", "🤔 Не совсем"}:
            u["stage"] = "analysis_refine"
            await save_user(u, DB_PATH)
            await m.answer(
                "Ок, уточним и пересоберём вывод.\n\n"
                "Ответь коротко (1–2 предложения):\n"
                "1️⃣ Сложнее НАЧАТЬ или УДЕРЖАТЬ?\n"
                "2️⃣ Больше тревоги или больше пустоты/энергии нет?\n"
                "3️⃣ Отвлечения — главная проблема или вторично?"
            )
            return
        await answer_with_keyboard(m, u, "Выбери кнопку 👇", kb_analysis_confirm, "analysis")
        return

    # Подробнее о контракте
    if u.get("stage") == "analysis_contract" and (text == "Подробнее о контракте" or "подробнее о контракте" in text.lower()):
        from texts import contract_full_text
        await answer_with_keyboard(m, u, contract_full_text(u.get("name") or "друг", u.get("trainer_key"), u.get("bucket")), kb_yes_no, "yes_no")
        return

    # analysis_retry_await_clarification
    if u.get("stage") == "analysis_retry_await_clarification":
        if not text:
            await m.answer("Напиши, пожалуйста, что не совпадает с реальностью. (1–3 предложения)")
            return
        u["analysis_json"] = json.dumps({"user_text": clamp_str(text, 1000)}, ensure_ascii=False)
        u["stage"] = "run_analysis"
        await save_user(u, DB_PATH)
        await m.answer("Ок. Переразбор…")
        await run_analysis(m, u, text, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
        return

    # analysis_refine
    if u["stage"] == "analysis_refine":
        if not text:
            await m.answer("Напиши 1–2 предложения, чтобы я пересобрал вывод.")
            return
        # Объединяем исходный текст и уточнение, чтобы модель видела весь контекст
        base_user_text = ""
        try:
            if u.get("analysis_json"):
                prev = json.loads(u.get("analysis_json") or "{}")
                base_user_text = prev.get("user_text", "") or ""
        except Exception:
            base_user_text = ""

        combined_text = base_user_text.strip()
        if combined_text:
            combined_text += "\n\nУточнение пользователя: " + text
        else:
            combined_text = text

        u["raw_text"] = combined_text
        u["stage"] = "run_analysis"
        await save_user(u, DB_PATH)
        await m.answer("Ок. Пересобираю вывод…")
        await run_analysis(m, u, combined_text, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
        return

    # Вопрос перед выдачей навыка
    if u.get("stage") == "await_training_target":
        screen = engine_get_next_screen(u, {"type": "target_submitted", "text": text})
        apply_engine_updates(u, screen)
        await save_user(u, DB_PATH)
        await log_engine_events(u, screen)
        await answer_with_keyboard(m, u, screen["text"], kb_skill_card, "skill_card")
        await m.answer(gamify_status_line(u))
        return

    # TRAINING stage
    if u.get("stage") == "training":
        low = text.lower().strip()
        day = int(u.get("day") or 1)

        if text in {"💪 Давай действие", "💪 Давай тренировать навык"} or ("давай" in low and ("трен" in low or "действ" in low)):
            plan = get_current_plan(u)
            idx = max(0, min(len(plan) - 1, int(u.get("day") or 1) - 1))
            sid = plan[idx]
            skill = SKILLS_DB.get(sid, {})
            detail = skill_detail_text(skill)
            trainer_key = u.get("trainer_key") or "marsha"
            prompt = (
                "Делаем серию коротких подходов: 3–4 раза за сегодня, если есть ресурс. "
                "Каждый подход ≤120 сек. Нажимай эту кнопку, когда готов к новому кругу, "
                "и после попытки отмечай результат кнопкой "
                "'✅ Сделал(а)' или '↩️ Вернулся(лась)'."
            )
            await log_event(u["user_id"], "training", "repeat_practice", {"day": day, "sid": sid}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, trainer_say(trainer_key, f"{detail}\n\n{prompt}"), kb_training_main, "training_main")
            return

        if text in {"📚 Подробнее", "ℹ️ Подробнее", "ℹ️ Подробнее про навык"} or "подробнее" in low:
            plan = get_current_plan(u)
            idx = max(0, min(len(plan) - 1, int(u.get("day") or 1) - 1))
            sid = plan[idx]
            skill = SKILLS_DB.get(sid, {})
            msg = skill_detail_text(skill)
            await log_event(u["user_id"], "training", "details_clicked", {"skill_id": sid}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, msg, kb_more_clarify, "detail")
            return

        if text in {"🤔 Я не понимаю", "🤔 Не понял"} or low in {"я не понимаю", "не понял", "не понимаю"}:
            await log_event(u["user_id"], "training", "dont_understand_clicked", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, simple_explain_text(), kb_microstep, "microstep")
            return

        if text == "Ещё" or text == "Еще" or low in {"ещё", "еще"}:
            await answer_with_keyboard(m, u, "Ещё действия:", kb_more_actions, "more_actions")
            return

        if text == "⬅️ Назад" or low == "назад":
            await answer_with_keyboard(m, u, "Ок. Возвращаемся к действию.", kb_training_main, "training_main")
            return

        if text in {"🗺 Показать маршрут", "🗺 Маршрут"} or "маршрут" in low:
            await log_event(u["user_id"], "training", "route_requested", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await show_route(m, u, "button")
            await answer_with_keyboard(m, u, "Что дальше?", kb_training_main, "training_main")
            return

        if text == "👍 Понял(а), продолжаем":
            trainer_key = u.get("trainer_key") or "marsha"
            await log_event(u["user_id"], "training", "doubt_understood", {"trainer": trainer_key}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, trainer_say(trainer_key, PRAISE.get(trainer_key, "Идём дальше!")), kb_training_main, "training_main")
            return

        if text == "📚 Подробнее почему это работает" or "подробнее почему" in low:
            await log_event(u["user_id"], "training", "why_requested", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, skeptic_text(), kb_skeptic, "skeptic")
            return

        if text == "Ты меня не понял" or "не понял" in low:
            await log_event(u["user_id"], "training", "dont_understand_clicked", {"source": "misunderstood_text"}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, simple_explain_text(), kb_microstep, "microstep")
            return

        if text == "🆘 Кризис" or "кризис" in low:
            u["stage"] = "crisis_choose_mode"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], u["stage"], "crisis_open", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, "🆘 Ок. Как удобнее?", kb_crisis_mode, "crisis_mode")
            return

        if text in {"📊 Мой прогресс", "📊 Прогресс"} or "мой прогресс" in low or "прогресс" in low:
            await send_progress_report(m, u, DB_PATH)
            return

        if text == "✅ Сделал(а)" or ("сделал" in low and "не сделал" not in low):
            screen = engine_handle_action_result(u, "done")
            previous_done = int(u.get("done_count") or 0)
            u["done_count"] = previous_done + 1
            gamify_apply(u, 2, "done")
            apply_engine_updates(u, screen)
            await save_user(u, DB_PATH)
            await log_engine_events(u, screen)
            await m.answer(screen["text"])
            if previous_done == 0:
                await show_route(m, u, "first_done")
            if should_show_day3_offer(u, day):
                await show_route(m, u, "day3_summary")
                await show_day3_offer(m, u, "day3_done")
                return
            await answer_with_keyboard(m, u, "Что дальше?", kb_done, "done")
            return

        if text == "↩️ Вернулся(лась)" or "вернулся" in low:
            screen = engine_handle_action_result(u, "return")
            u["return_count"] = int(u.get("return_count") or 0) + 1
            gamify_apply(u, 1, "return")
            apply_engine_updates(u, screen)
            await save_user(u, DB_PATH)
            await log_engine_events(u, screen)
            await m.answer(trainer_say(u.get("trainer_key") or "marsha", screen["text"]))
            try:
                await m.answer(trainer_say(u.get("trainer_key") or "marsha", PRAISE.get(u.get("trainer_key") or "marsha", "")))
            except Exception:
                pass
            if day == 7:
                await send_weekly_summary(m, u, DB_PATH)
            if should_show_day3_offer(u, day):
                await show_route(m, u, "day3_summary")
                await show_day3_offer(m, u, "day3_return")
                return
            if not TEST_MODE and day >= 7 and u.get("trial_phase") in ("trial3", "trial7", None):
                await answer_with_keyboard(m, u, "Выбирай вариант оплаты:", kb_pay_choice, "pay_choice")
                u["stage"] = "offer"
                await save_user(u, DB_PATH)
                return
            await answer_with_keyboard(m, u, "Что дальше?", kb_done, "done")
            return

        if text in {"❓ Сомневаюсь", "❓ Сомневаюсь, работает ли"} or "сомневаюсь" in low:
            await log_event(u["user_id"], "training", "skeptic_clicked", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, skeptic_text(), kb_skeptic, "skeptic")
            return

        if "не пошло" in low or "не подходит" in low or "не работает" in low or text == "🔁 Заменить навык" or "заменить" in low:
            await log_event(u["user_id"], "training", "skill_replace_requested", {"day": day, "reason": text or "button"}, DB_PATH, SHEETS_WEBHOOK_URL)

            plan = get_current_plan(u)
            if not plan:
                await m.answer(trainer_say(u.get("trainer_key") or "marsha", "План пуст — нет что заменить. Сообщи поддержке."))
                return

            idx = max(0, min(len(plan) - 1, day - 1))
            current_sid = plan[idx]
            current_skill = SKILLS_DB.get(current_sid, {})
            track = current_skill.get("track") or u.get("bucket") or "mixed"
            new_sid = suggest_alternative_skill(track, current_sid) or current_sid
            if new_sid == current_sid:
                # попытка выбрать любой другой по треку
                alt = [k for k, v in SKILLS_DB.items() if v.get("track") == track and k != current_sid]
                if alt:
                    new_sid = alt[0]

            plan[idx] = new_sid
            u["plan_json"] = json.dumps(plan, ensure_ascii=False)
            await save_user(u, DB_PATH)

            skill_msg = format_skill(new_sid, u.get("trainer_key") or "marsha") if new_sid in SKILLS_DB else "Выбран новый навык."
            await m.answer(trainer_say(u.get("trainer_key") or "marsha", f"Меняю на {SKILLS_DB[new_sid]['name']}" if new_sid in SKILLS_DB else "Меняю навык."))
            await answer_with_keyboard(m, u, skill_msg, kb_training_main, "training_main")
            return

        await answer_with_keyboard(m, u, "Выбери действие:", kb_training_main, "training_main")
        return

    # crisis_choose_mode
    if u.get("stage") == "crisis_choose_mode":
        low = (text or "").lower().strip()

        # Если сразу прислал голосовое — обрабатываем без лишних шагов
        if m.voice:
            t = await whisper_transcribe(m)
            if t:
                await handle_crisis(m, u, t, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
                return
            await m.answer("Не смог разобрать голос. Напиши 1–3 предложения.")
            u["stage"] = "crisis_text"
            await save_user(u, DB_PATH)
            return

        if text == "⬅️ Назад" or "назад" in low:
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Ок. Возвращаемся в тренировку.", kb_training_main, "training_main")
            return
        if text == "🎙 Кризис голосом" or "голос" in low:
            u["stage"] = "crisis_voice"
            await save_user(u, DB_PATH)
            await m.answer("🎙 Запиши голосом: что происходит и что мешает прямо сейчас?")
            return
        if text == "✍️ Кризис текстом" or "текст" in low:
            u["stage"] = "crisis_text"
            await save_user(u, DB_PATH)
            await m.answer("✍️ Напиши: что происходит и что мешает прямо сейчас? (1–3 предложения)")
            return
        if text:
            # Любой текст без выбора — сразу кризис-текст
            await handle_crisis(m, u, text, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
            return
        await answer_with_keyboard(m, u, "Выбери кнопкой 👇", kb_crisis_mode, "crisis_mode")
        return

    if u.get("stage") == "crisis_text":
        if text and text.lower().strip() in {"⬅️ назад", "назад"}:
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Ок. Возвращаемся в тренировку.", kb_training_main, "training_main")
            return
        if not text:
            await m.answer("Напиши 1–3 предложения.")
            return
        await handle_crisis(m, u, text, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
        return

    if u.get("stage") == "crisis_voice":
        if text and text.lower().strip() in {"⬅️ назад", "назад"}:
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Ок. Возвращаемся в тренировку.", kb_training_main, "training_main")
            return
        if not m.voice:
            await m.answer("Пришли голосовое 🎙")
            return
        t = await whisper_transcribe(m)
        if not t:
            await m.answer("Не смог разобрать. Напиши текстом 1–3 предложения.")
            u["stage"] = "crisis_text"
            await save_user(u, DB_PATH)
            return
        await handle_crisis(m, u, t, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
        return

    if u.get("stage") == "crisis_plan_confirm":
        low = text.lower().strip()
        if text == "✅ Да" or "да" in low:
            pending = json.loads(u.get("pending_plan_change") or "{}") if u.get("pending_plan_change") else {}
            day_num = pending.get("day_num")
            sid = pending.get("skill_id")
            if day_num and sid:
                propose_plan_override(u, int(day_num), sid)
                u["pending_plan_change"] = None
                await save_user(u, DB_PATH)
                await log_event(u["user_id"], u.get("stage", ""), "plan_change_accept", {"day": day_num, "skill": sid}, DB_PATH, SHEETS_WEBHOOK_URL)
                await m.answer("✅ Ок. Я обновил план. Завтра будет эта версия.")
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Возвращаемся в тренировку.", kb_training_main, "training_main")
            return
        if text == "❌ Нет" or "нет" in low:
            u["pending_plan_change"] = None
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], u.get("stage", ""), "plan_change_reject", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, "Ок. План не меняю. Возвращаемся.", kb_training_main, "training_main")
            return
        await m.answer("Выбери: ✅ Да / ❌ Нет", reply_markup=kb_yes_no)
        return

    # OFFER stage
    if u.get("stage") == "offer":
        low = text.lower().strip()
        if text == "7 дней — €20" or "7 дней" in low or "€20" in low or "20" == low:
            await log_event(u["user_id"], "offer", "payment_click_20", {"payment_click": "20"}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["payment_status"] = "pending_20"
            u["last_payment_click"] = "20"
            await save_user(u, DB_PATH)
            if PAYMENT_URL_DISCOUNT:
                await m.answer("Ок. 7 дней сопровождения по ссылке 👇")
                await m.answer(" ", reply_markup=payment_inline_20(PAYMENT_URL_DISCOUNT))
            else:
                await log_event(u["user_id"], "offer", "payment_error", {"error_type": "payment_url_missing", "payment_click": "20"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await m.answer(payment_20_stub_text())
            return
        if text == "Месяц — €40" or "месяц" in low or "€40" in low or "40" == low:
            await log_event(u["user_id"], "offer", "payment_click_40", {"payment_click": "40"}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["payment_status"] = "pending_40"
            u["last_payment_click"] = "40"
            await save_user(u, DB_PATH)
            if PAYMENT_URL_FULL:
                await m.answer("Ок. Месяц тренировки по ссылке 👇")
                await m.answer(" ", reply_markup=payment_inline_40(PAYMENT_URL_FULL))
            else:
                await log_event(u["user_id"], "offer", "payment_error", {"error_type": "payment_url_missing", "payment_click": "40"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await m.answer(payment_40_stub_text())
            return
        if text == "Подумаю" or "подумаю" in low:
            await log_event(u["user_id"], "offer", "payment_declined_soft", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await log_event(u["user_id"], "offer", "free_mode_started", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["free_mode"] = 1
            u["payment_status"] = "free_mode"
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            await m.answer(payment_declined_soft_text())
            await answer_with_keyboard(m, u, "Выбери действие:", kb_training_main, "training_main")
            return
        if text == "Что входит?" or "что входит" in low:
            await m.answer(payment_includes_text())
            await answer_with_keyboard(m, u, "Выбери вариант:", kb_pay_choice, "pay_choice")
            return
        await answer_with_keyboard(m, u, "Выбирай кнопкой 👇", kb_pay_choice, "pay_choice")
        return

    # Если дошли до сюда — неизвестный этап, выводим stage для отладки
    stage = str(u.get('stage')).replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('`', '\\`')
    if stage != "post_done_reflection":
        await m.answer(f"Неизвестный этап (stage): {stage}. Напиши /start чтобы начать заново или обратись к поддержке.", parse_mode=None)

# ============================================================
# CALLBACKS
# ============================================================

@router.callback_query(F.data.in_({"yes", "no", "noop"}))
async def on_callbacks(c: CallbackQuery):
    uid = c.from_user.id
    u = await get_user(uid, DB_PATH)
    if c.data == "noop":
        await c.answer()
        return
    if u.get("stage") == "confirm_analysis":
        if c.data == "yes":
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            # Показываем первый навык сразу после онбординга (через callback)
            await start_day(m=c.message, u=u, day=1, db_path=DB_PATH, sheets_webhook=SHEETS_WEBHOOK_URL)
        else:
            u["stage"] = "await_problem_text"
            await save_user(u, DB_PATH)
            await c.message.answer("Ок. Тогда уточни: что больше всего мешает? (2–3 предложения)")
        await c.answer()
        return
    await c.answer()

@router.callback_query(F.data.startswith("test_q"))
async def on_test_answer(c: CallbackQuery):
    uid = c.from_user.id
    u = await get_user(uid, DB_PATH)
    try:
        parts = c.data.split("_")
        if len(parts) < 3:
            await c.answer("Ошибка в данных")
            return
        q_num = int(parts[1][1:])
        bucket_answer = "_".join(parts[2:])
        test_answers = u.get("test_answers") or []
        test_answers.append(bucket_answer)
        u["test_answers"] = test_answers
        await save_user(u, DB_PATH)
        if len(test_answers) < len(TEST_QUESTIONS):
            next_q_num = len(test_answers) + 1
            next_q = next((x for x in TEST_QUESTIONS if x["id"] == next_q_num), None)
            if next_q:
                await c.message.edit_text(f"❓ Вопрос {next_q_num}/5:\n\n{next_q['text']}", reply_markup=create_test_question_keyboard(next_q_num))
            await c.answer()
        else:
            resolved_bucket = resolve_bucket_from_test(test_answers)
            u["bucket"] = resolved_bucket
            u["test_answers"] = []
            u["stage"] = "test_complete_show_analysis"
            await save_user(u, DB_PATH)
            await show_comprehensive_analysis(c.message, u)
            await c.answer()
    except Exception as e:
        log.error(f"Error in test callback: {e}")
        await c.answer("Ошибка обработки ответа")

async def show_comprehensive_analysis(m: Message, u: Dict[str, Any]):
    bucket = u.get("bucket") or "mixed"
    user_text = ""
    if u.get("analysis_json"):
        try:
            analysis_data = json.loads(u.get("analysis_json") or "{}")
            user_text = analysis_data.get("user_text", "")
        except:
            pass
    if not user_text:
        user_text = f"У меня проблемы с {bucket}"
    comp = await ai_analyze_comprehensive(user_text, u.get("trainer_key", "marsha"), client, OPENAI_CHAT_MODEL)
    if comp.get("analysis_fallback"):
        await log_event(u["user_id"], "analysis", "openai_error", {"error_type": "analysis_fallback", "error_source": "show_comprehensive_analysis"}, DB_PATH, SHEETS_WEBHOOK_URL)
    u["analysis_json"] = json.dumps(comp, ensure_ascii=False)
    u["bucket"] = comp.get("bucket", bucket)
    plan_ids = build_28_day_plan(u["bucket"])
    if comp.get("analysis_fallback") and "open_only" in SKILLS_DB:
        plan_ids[0] = "open_only"
    u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
    u["day"] = 1
    u["stage"] = "confirm_analysis"
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "analysis", "diagnosis_completed", {"bucket": u.get("bucket")}, DB_PATH, SHEETS_WEBHOOK_URL)
    await log_event(u["user_id"], "analysis", "analysis_shown", {"bucket": u.get("bucket")}, DB_PATH, SHEETS_WEBHOOK_URL)
    fallback_notice = ""
    if comp.get("analysis_fallback"):
        fallback_notice = "Ок, начнём с базового паттерна: сложно войти в задачу.\nДадим самый маленький шаг.\n\n"
    msg = f"{fallback_notice}{comp.get('short_summary', 'Похоже на тебя?')}\n\nЭто похоже на тебя?"
    await answer_with_keyboard(m, u, msg, kb_analysis_confirm, "analysis")

# ============================================================
# WHISPER TRANSCRIBE
# ============================================================

async def whisper_transcribe(m: Message) -> Optional[str]:
    if not (AI_ANALYSIS_ENABLED and client):
        log.warning("[AI] Whisper disabled: OpenAI client or API key is not configured")
        try:
            await log_event(m.from_user.id, "voice", "whisper_error", {"error_type": "not_configured", "error_source": "whisper_transcribe"}, DB_PATH, SHEETS_WEBHOOK_URL)
        except Exception:
            pass
        return None
    if not m.voice:
        return None
    try:
        file = await m.bot.get_file(m.voice.file_id)
        fp = await m.bot.download_file(file.file_path)
        if hasattr(fp, "seek"):
            fp.seek(0)
        data = fp.read()
        if not data:
            log.warning("[AI] Whisper got empty Telegram voice payload")
            return None
        bio = io.BytesIO(data)
        bio.name = f"voice_{m.voice.file_unique_id}.ogg"
        tr = await asyncio.to_thread(
            client.audio.transcriptions.create,
            model=OPENAI_WHISPER_MODEL,
            file=bio,
            language="ru",
        )
        text = getattr(tr, "text", None)
        if not text:
            try:
                text = tr["text"]
            except Exception:
                text = None
        text = (text or "").strip()
        if not text:
            log.warning("[AI] Whisper returned empty transcription")
        return text or None
    except Exception as e:
        log.exception("whisper error: %s", e)
        try:
            await log_event(m.from_user.id, "voice", "whisper_error", {"error_type": type(e).__name__, "error_source": "whisper_transcribe"}, DB_PATH, SHEETS_WEBHOOK_URL)
        except Exception:
            pass
        return None

# ============================================================
# BACKGROUND TASKS
# ============================================================

async def send_background_keyboard(bot: Bot, u: Dict[str, Any], text: str, reply_markup, keyboard_name: str):
    button_count = keyboard_button_count(reply_markup)
    await log_event(
        u.get("user_id"),
        u.get("stage", ""),
        "keyboard_shown" if button_count <= 5 else "keyboard_warning",
        {"keyboard": keyboard_name, "button_count": button_count, "source": "background"},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    try:
        await bot.send_message(
            u["chat_id"],
            text,
            reply_markup=reply_markup if button_count <= 5 else None,
        )
    except Exception as e:
        log.exception("telegram_send_error: %s", e)
        await log_event(
            u.get("user_id"),
            u.get("stage", ""),
            "telegram_send_error",
            {"source": "send_background_keyboard", "keyboard": keyboard_name},
            DB_PATH,
            SHEETS_WEBHOOK_URL,
        )


async def background_checkins(bot: Bot):
    """Proactive morning/evening check-ins with per-day anti-spam guards."""
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT * FROM users")
                rows = await cur.fetchall()

            now_ts = time.time()
            for row in rows:
                u = dict(row)
                if int(u.get("notifications_enabled") if u.get("notifications_enabled") is not None else 1) != 1:
                    continue
                if not u.get("chat_id"):
                    continue
                if u.get("stage") not in {"training", "await_training_target", "waiting_next_day"}:
                    continue

                now_local = local_now_for_user(u)
                today = now_local.date().isoformat()

                if (
                    in_time_window(now_local, 8, 0, 10, 30)
                    and u.get("last_morning_checkin_date") != today
                ):
                    if user_inactive_over_24h(u, now_ts):
                        count = int(u.get("reactivation_count") or 0)
                        if count >= 3:
                            continue
                        count += 1
                        u["reactivation_count"] = count
                        u["last_morning_checkin_date"] = today
                        if count < 3:
                            u["stage"] = "morning_checkin"
                        await save_user(u, DB_PATH)
                        await log_event(u["user_id"], u.get("stage", ""), "reactivation_sent", {"count": count}, DB_PATH, SHEETS_WEBHOOK_URL)
                        if count < 3:
                            await send_background_keyboard(bot, u, reactivation_text(count), kb_morning_checkin, "morning_checkin")
                        else:
                            await bot.send_message(u["chat_id"], reactivation_text(count))
                        continue

                    u["last_morning_checkin_date"] = today
                    u["stage"] = "morning_checkin"
                    await save_user(u, DB_PATH)
                    await log_event(u["user_id"], "morning_checkin", "morning_checkin_sent", {}, DB_PATH, SHEETS_WEBHOOK_URL)
                    await send_background_keyboard(
                        bot,
                        u,
                        morning_checkin_text(u.get("name") or "друг"),
                        kb_morning_checkin,
                        "morning_checkin",
                    )
                    continue

                if (
                    in_time_window(now_local, 19, 0, 22, 30)
                    and u.get("last_evening_checkin_date") != today
                ):
                    u["last_evening_checkin_date"] = today
                    u["stage"] = "evening_checkin"
                    await save_user(u, DB_PATH)
                    await log_event(u["user_id"], "evening_checkin", "evening_checkin_sent", {}, DB_PATH, SHEETS_WEBHOOK_URL)
                    await send_background_keyboard(bot, u, evening_checkin_text(), kb_evening_checkin, "evening_checkin")

        except Exception as e:
            log.warning("background_checkins failed: %s", e)
            await log_event(0, "background", "db_error", {"error_type": type(e).__name__, "error_source": "background_checkins"}, DB_PATH, SHEETS_WEBHOOK_URL)

        await asyncio.sleep(900)

# ============================================================
# MAIN
# ============================================================



def start_sheets_sync_background_task(db_path: str):
    """Start Sheets sync if the module exposes the loop; never crash polling startup."""
    loop_fn = getattr(sheets_sync_module, "sheets_sync_loop", None)
    if not callable(loop_fn):
        log.error("Sheets sync loop is unavailable; bot will continue without Sheets background sync")
        return None
    return asyncio.create_task(loop_fn(db_path))


async def main() -> int:
    try:
        if not BOT_TOKEN:
            raise RuntimeError("BOT_TOKEN is empty; set the BOT_TOKEN environment variable before starting the bot")

        bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
        dp = Dispatcher()
        dp.include_router(router)
        await init_db(DB_PATH)
        await migrate_db(DB_PATH)

        if STARTUP_CHECK:
            await bot.session.close()
            log.info("Startup check completed successfully; polling was not started")
            return 0

        asyncio.create_task(background_checkins(bot))
        start_sheets_sync_background_task(DB_PATH)
        log.info("Bot started")
        await dp.start_polling(bot)
        return 0
    except asyncio.exceptions.CancelledError:
        log.info("Polling cancelled, shutting down...")
        return 0
    except KeyboardInterrupt:
        log.info("Bot stopped by user (KeyboardInterrupt)")
        return 0
    except Exception as e:
        log.exception("Startup failed: %s", e)
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
