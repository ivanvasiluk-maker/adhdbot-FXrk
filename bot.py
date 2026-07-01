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
import random
import time
import asyncio
import logging
import threading
import uuid
import datetime as dt
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
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
    CORE_LAUNCH_WEEK_SKILL_IDS,
    get_current_plan,
    build_28_day_plan,
    build_plan,
    propose_plan_override,
    suggest_alternative_skill,
    format_skill,
    variants_for_core_skill,
)
from db import (
    USER_FIELDS, default_user, init_db, migrate_db, get_user, save_user, 
    log_event, gamify_apply, is_paid, EXTRA_USER_COLS,
    get_user_profile, update_user_profile, render_short_user_map, label, SKILL_LABELS,
    user_model_event, user_model_events_from_signal,
    record_development_avatar_event, development_map_event_patch,
    render_development_mirror_reports,
    ensure_user_day, close_user_day, create_skill_attempt, attempt_count_for_day,
    record_action_event, get_action_metrics, save_current_task, update_current_task_step,
    record_user_feedback, user_feedback_count, recent_user_feedback,
)
from flows import (
    start_day, start_day1, start_day_simple, advance_day, handle_crisis,
    send_trainer_photo_if_any, run_analysis,
    send_weekly_summary, send_progress_report, ai_analyze, ai_analyze_comprehensive,
    format_comprehensive_analysis, normalize_analysis, safe_analysis_memory, _extract_json, clamp_str,
    live_analysis_profile_patch, render_analysis_details_by_trainer, build_analysis_result
)
from nlp_fallback import is_misunderstood, is_too_hard, is_timer_too_hard
from core.engine import (
    get_next_screen as engine_get_next_screen,
    handle_action_result as engine_handle_action_result,
    handle_downscale as engine_handle_downscale,
    should_show_offer as engine_should_show_offer,
    build_day_core_updates as engine_build_day_core_updates,
    core_round_count_today as engine_core_round_count_today,
)
import sheets_sync as sheets_sync_module

SHEETS_SYNC_ENABLED = getattr(sheets_sync_module, "SHEETS_SYNC_ENABLED", False)
SHEETS_SYNC_INTERVAL_SECONDS = getattr(sheets_sync_module, "SHEETS_SYNC_INTERVAL_SECONDS", 60)
SHEETS_SYNC_BATCH_SIZE = getattr(sheets_sync_module, "SHEETS_SYNC_BATCH_SIZE", 50)

load_dotenv(override=True)

# ============================================================
# CONFIG
# ============================================================

def env_bool(name: str, default: str = "") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on", "debug", "check"}


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini").strip()
OPENAI_WHISPER_MODEL = os.getenv("OPENAI_WHISPER_MODEL", "whisper-1").strip()
DB_PATH = os.getenv("DB_PATH", "bot.db").strip()
PAYMENT_URL = os.getenv("PAYMENT_URL", "https://your-payment-link").strip()
PAYMENT_URL_DISCOUNT = os.getenv("PAYMENT_URL_DISCOUNT", "").strip()
PAYMENT_URL_FULL = os.getenv("PAYMENT_URL_FULL", "").strip()
PAYMENT_URL_MONTH_1498 = os.getenv("PAYMENT_URL_MONTH_1498", "").strip()
PAYMENT_MONTH_URL = os.getenv("PAYMENT_MONTH_URL", "").strip()
PAYMENT_TEST_URL = os.getenv("PAYMENT_TEST_URL", "").strip()
PAYMENT_ACCEPT_ANY = env_bool("PAYMENT_ACCEPT_ANY")
ENABLE_PAYMENTS = env_bool("ENABLE_PAYMENTS")
SHEETS_WEBHOOK_URL = os.getenv("SHEETS_WEBHOOK_URL", "").strip()

# Unlock full flow while testing (set TEST_MODE=1)
TEST_MODE = env_bool("TEST_MODE")
IS_TEST_MODE = TEST_MODE
TEST_CHEAT_CODE = os.getenv("TEST_CHEAT_CODE", "SKILLER_TEST_1498").strip()
STARTUP_CHECK = env_bool("BOT_STARTUP_CHECK")
MAX_CRISIS_MATCHES_PER_DAY = 3
CRISIS_WAITING_INPUT = "crisis_waiting_input"
SAFETY_MODES = {"none", "inactive", "triage", "active", "support"}
SAFETY_RISK_VALUES = {"unknown", "no", "yes", "uncertain"}
SAFETY_CONTACT_VALUES = {"not_asked", "offered", "sent_message", "called", "unavailable", "aftercare"}
DEFAULT_EMERGENCY_NUMBER_BY_COUNTRY = {}
try:
    EMERGENCY_NUMBER_BY_COUNTRY = {
        **DEFAULT_EMERGENCY_NUMBER_BY_COUNTRY,
        **json.loads(os.getenv("EMERGENCY_NUMBER_BY_COUNTRY", "{}") or "{}"),
    }
except Exception:
    EMERGENCY_NUMBER_BY_COUNTRY = DEFAULT_EMERGENCY_NUMBER_BY_COUNTRY

AI_ANALYSIS_ENABLED = bool(OPENAI_API_KEY)

# Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

log.info("BOT_TOKEN configured: %s", bool(BOT_TOKEN))
log.info("DB_PATH: %s", DB_PATH)
if PAYMENT_ACCEPT_ANY:
    log.warning("PAYMENT_ACCEPT_ANY is enabled: test payment confirmations can grant paid access; disable it before production.")
if STARTUP_CHECK:
    log.info("BOT_STARTUP_CHECK is enabled: startup will validate init and exit before Telegram polling.")

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
MAX_SKILL_REPLACEMENTS_BEFORE_REDIAGNOSIS = 3
SUCCESS_ACTIONS = {"DONE", "RETURNED", "CRISIS_COMPLETED"}
NO_SUCCESS_ACTIONS = {"SKIP", "CHANGE_SKILL", "EASIER", "MORE_INFO", "NOT_UNDERSTAND"}

ACTION_RELATED_STAGES = {
    "training",
    "action_clarification",
    "downscale_action",
    "downscale_name_task",
    "failed_options",
    "stuck_reason_text",
    "skip_options",
}

FREE_AFTER_DAY_3 = {
    "daily_skills": 1,
    "rounds_per_day": 3,
    "downscales_per_day": 1,
    "skill_replacements_per_day": 0,
    "crisis": False,
    "short_map": True,
    "full_map": False,
    "daily_summary": True,
}

kb_day_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💪 Сделать следующий шаг")],
        [KeyboardButton(text="⚡ Я застрял"), KeyboardButton(text="🆘 Кризис прокрастинации")],
        [KeyboardButton(text="🧭 Моя карта")],
        [KeyboardButton(text="🌙 Закрыть день")],
    ],
    resize_keyboard=True,
)

kb_short_map_repeat = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📖 Полная карта")],
        [KeyboardButton(text="💪 Давай действие")],
        [KeyboardButton(text="🌙 Закрыть день")],
    ],
    resize_keyboard=True,
)

kb_short_mode_main = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💪 Сделать следующий шаг")],
        [KeyboardButton(text="⚡ Я застрял"), KeyboardButton(text="🧭 Моя карта")],
        [KeyboardButton(text="🌙 Закрыть день")],
    ],
    resize_keyboard=True,
)

kb_day_pause_confirm = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Закрыть день")],
        [KeyboardButton(text="⏸ Просто пауза"), KeyboardButton(text="↩️ Вернуться к навыку")],
    ],
    resize_keyboard=True,
)

kb_closed_day_continue = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Да, ещё один короткий шаг")],
        [KeyboardButton(text="🌙 Нет, оставить день закрытым")],
    ],
    resize_keyboard=True,
)

kb_feedback_instruction_clarity = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟢 Да, очень понятно")],
        [KeyboardButton(text="🟡 В целом понятно")],
        [KeyboardButton(text="🔴 Не понял(а), что от меня хотят")],
        [KeyboardButton(text="🎙️ Напишу или скажу сам(а)")],
    ],
    resize_keyboard=True,
)

kb_feedback_validation = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟢 Да, очень похоже")],
        [KeyboardButton(text="🟡 Частично понял")],
        [KeyboardButton(text="🔴 Нет, мимо")],
        [KeyboardButton(text="🎙️ Хочу объяснить, что было не так")],
    ],
    resize_keyboard=True,
)

kb_feedback_validation_after_missed = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔄 Сменить навык"), KeyboardButton(text="💪 Вернуться к шагу")],
        [KeyboardButton(text="🌙 Закрыть подход")],
    ],
    resize_keyboard=True,
)

kb_feedback_day_value = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🧩 Маленький конкретный шаг")],
        [KeyboardButton(text="🧠 Разбор, почему я застрял")],
        [KeyboardButton(text="🤍 Поддержка без стыда")],
        [KeyboardButton(text="🔄 Возможность сменить навык")],
        [KeyboardButton(text="😐 Пока ничего")],
        [KeyboardButton(text="🎙️ Напишу сам(а)")],
    ],
    resize_keyboard=True,
)

kb_feedback_day_none_reason = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🤷 Не понял, как пользоваться")],
        [KeyboardButton(text="🧊 Не почувствовал пользы")],
        [KeyboardButton(text="😬 Было слишком много текста")],
        [KeyboardButton(text="🐌 Было скучно / медленно")],
        [KeyboardButton(text="🧠 Совет не подошёл")],
        [KeyboardButton(text="🎙️ Опишу сам(а)")],
    ],
    resize_keyboard=True,
)

kb_feedback_product_score = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=str(i)) for i in range(0, 6)],
        [KeyboardButton(text=str(i)) for i in range(6, 11)],
    ],
    resize_keyboard=True,
)

kb_feedback_product_low = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🤷 Не понимаю, как бот работает")],
        [KeyboardButton(text="🧠 Не чувствую персонализации")],
        [KeyboardButton(text="🐌 Слишком много текста")],
        [KeyboardButton(text="😐 Пока не помогает начать")],
        [KeyboardButton(text="💳 Не понимаю, за что платить")],
        [KeyboardButton(text="🎙️ Опишу сам(а)")],
    ],
    resize_keyboard=True,
)

kb_feedback_product_high = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🧩 Маленькие шаги")],
        [KeyboardButton(text="🧠 Разбор застреваний")],
        [KeyboardButton(text="🤍 Поддержку без стыда")],
        [KeyboardButton(text="🗺 Личную карту")],
        [KeyboardButton(text="🔄 Смену навыков")],
        [KeyboardButton(text="🎙️ Напишу сам(а)")],
    ],
    resize_keyboard=True,
)

kb_feedback_offer = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🧠 Не понимаю разницу режимов")],
        [KeyboardButton(text="💳 Дорого")],
        [KeyboardButton(text="🕒 Хочу сначала попробовать дольше")],
        [KeyboardButton(text="🤷 Пока не вижу пользы")],
        [KeyboardButton(text="📦 Не хватает конкретной функции")],
        [KeyboardButton(text="🎙️ Напишу сам(а)")],
    ],
    resize_keyboard=True,
)

kb_active_skill = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Сделал"), KeyboardButton(text="🟡 Не вышло")],
        [KeyboardButton(text="🤷 Не моё"), KeyboardButton(text="🔄 Сменить навык")],
        [KeyboardButton(text="⚡ Я застрял"), KeyboardButton(text="🧠 Почему этот навык")],
        [KeyboardButton(text="🌙 Закрыть подход")],
    ],
    resize_keyboard=True,
)

kb_first_day_skill = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💪 Начать тренировку")],
        [KeyboardButton(text="🤷 Не моё"), KeyboardButton(text="🔄 Выбрать другой навык")],
        [KeyboardButton(text="🧠 Почему этот навык"), KeyboardButton(text="⚡ Я уже застрял")],
    ],
    resize_keyboard=True,
)

kb_skill_result_feedback = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Сделал — стало легче")],
        [KeyboardButton(text="😐 Сделал — но легче не стало")],
        [KeyboardButton(text="🚪 Сделал — начал задачу")],
        [KeyboardButton(text="🟡 Не получилось"), KeyboardButton(text="🤷 Не мой навык")],
        [KeyboardButton(text="😣 Слишком сложно"), KeyboardButton(text="🔄 Нужен другой вход")],
        [KeyboardButton(text="⏳ Не успел попробовать")],
    ],
    resize_keyboard=True,
)

kb_success_no_extra = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💪 Продолжить тренировку")],
        [KeyboardButton(text="🌙 На сегодня достаточно")],
        [KeyboardButton(text="🗣️ Что помогло?")],
    ],
    resize_keyboard=True,
)

kb_extra_microstep_done = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Сделал(а)")],
        [KeyboardButton(text="🌙 Закрыть подход")],
    ],
    resize_keyboard=True,
)

kb_skill_change_reason = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="😬 Слишком тревожно / страшно")],
        [KeyboardButton(text="🔋 Нет сил")],
        [KeyboardButton(text="📱 Меня уносит в телефон / другое")],
        [KeyboardButton(text="🧠 Слишком много всего")],
        [KeyboardButton(text="🤷 Не понимаю, зачем это делать")],
        [KeyboardButton(text="🎙️ Опишу ситуацию сам(а)")],
        [KeyboardButton(text="↩️ Оставить текущий навык")],
    ],
    resize_keyboard=True,
)

kb_skill_change_meaning = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="это важно для денег / работы")],
        [KeyboardButton(text="это важно для человека")],
        [KeyboardButton(text="это освобождает меня позже")],
        [KeyboardButton(text="я делаю это из страха, а не по своему выбору")],
        [KeyboardButton(text="пока не понимаю")],
    ],
    resize_keyboard=True,
)

kb_map_with_trainer = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎭 Сменить тренера")],
        [KeyboardButton(text="💪 Давай действие"), KeyboardButton(text="🌙 Закрыть день")],
    ],
    resize_keyboard=True,
)


def is_free_after_day3(u: Dict[str, Any]) -> bool:
    return int(u.get("free_mode") or 0) == 1 and calendar_program_day(u) >= 3 and not is_paid(u)


def offer_shown_today(u: Dict[str, Any]) -> bool:
    raw = u.get("last_offer_shown_at")
    if not raw:
        return False
    try:
        shown = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        today = dt.datetime.now(ZoneInfo(str(u.get("timezone") or "Europe/Vilnius"))).date()
        return shown.astimezone(ZoneInfo(str(u.get("timezone") or "Europe/Vilnius"))).date() == today
    except Exception:
        return False


def short_daily_map_text(profile: Dict[str, Any], skill_map: Optional[Dict[str, Any]] = None) -> str:
    node = (
        profile.get("main_hypothesis")
        or profile.get("avoidance_trigger")
        or profile.get("main_pattern")
        or "данных пока мало — проверяем маленький вход"
    )
    helps = []
    if int(profile.get("downscale_count") or 0):
        helps.append("уменьшить вход")
    confirmed_or_promising = [x for x in ((skill_map or {}).get("skills") or []) if x.get("status") in {"promising", "confirmed"}]
    if confirmed_or_promising:
        helps.append(f"начать через «{_skill_label(str(confirmed_or_promising[0].get('skill_id')))}»")
    helps.append("начать без требования качества")
    helps.append("сделать первый шаг на 2 минуты")
    helps = list(dict.fromkeys(helps))
    next_test = "2 минуты плохого черновика"
    return (
        "🧭 Коротко по карте сегодня:\n\n"
        "Главный узел:\n"
        f"— {node}\n\n"
        "Что проверяем по навыкам:\n"
        f"{skill_map_lines(skill_map or {}, 2)}\n\n"
        "Следующий тест:\n"
        f"— {next_test}\n\n"
        "Показать полную карту?"
    )


async def send_user_map(m: Message, u: Dict[str, Any], source: str):
    profile = await get_user_profile(u["user_id"], DB_PATH)
    skill_map = await build_skill_map_data(u, profile)
    profile["_skill_map"] = skill_map
    today = local_date_for_user(u)
    already_shown = u.get("profile_map_shown_date") == today and int(u.get("profile_map_shown_count") or 0) > 0
    full_requested = source == "full_map"
    if is_free_after_day3(u) and full_requested:
        await log_event(u["user_id"], u.get("stage", ""), "full_map_limited_in_free_mode", {"source": source}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(
            m,
            u,
            short_daily_map_text(profile, skill_map)
            + "\n\nПолная динамическая карта доступна в полном режиме. Короткая карта остаётся бесплатной.",
            kb_short_mode_main,
            u.get("stage") or "training_main",
        )
        return
    if already_shown and not full_requested:
        txt = short_daily_map_text(profile, skill_map)
        markup = kb_short_map_repeat
    else:
        txt = render_short_user_map(profile, u.get("name"))
        trainer = TRAINERS.get(u.get("trainer_key") or "marsha", TRAINERS["marsha"])
        txt = trainer_wrap(u, txt, "map")
        txt += f"\n\nТекущий тренер: {trainer.get('emoji', '')} {trainer.get('display_name') or trainer.get('name')}"
        markup = kb_map_with_trainer
    prev_date = u.get("profile_map_shown_date")
    u["profile_map_shown_date"] = today
    u["profile_map_shown_count"] = (int(u.get("profile_map_shown_count") or 0) + 1) if prev_date == today else 1
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], u.get("stage", ""), "profile_map_requested", {"source": source, "short": already_shown and not full_requested}, DB_PATH, SHEETS_WEBHOOK_URL)
    await answer_with_keyboard(m, u, txt, markup, u.get("stage") or "training_main")

VOICE_FREE_TEXT_STAGES = {
    "ask_name",
    "await_trainer",
    "notification_consent",
    "trainer_intro",
    "await_input_mode",
    "choose_input_mode",
    "await_training_target",
    "morning_checkin",
    "evening_checkin",
    "downscale_name_task",
    "after_action_note",
    "stuck_reason_text",
    "misunderstood_problem_await",
    "misunderstood_explain_await",
    "analysis_need_more",
    "analysis_retry_await_clarification",
    "analysis_refine",
    "crisis_text",
    "crisis_effect_await",
}


def infer_morning_checkin_answer(raw: str) -> str:
    low = (raw or "").lower()
    if any(x in low for x in ("залип", "телефон", "youtube", "ютуб", "telegram", "телеграм", "соцсет", "скрол")):
        return "📱 Залипаю"
    if any(x in low for x in ("нет сил", "устал", "устала", "энерг", "выжат", "разбит")):
        return "😵 Нет сил"
    if any(x in low for x in ("тревог", "страш", "паник", "пережива")):
        return "😬 Тревога"
    if any(x in low for x in ("слишком", "больш", "много", "перегруз", "сложно")):
        return "🌀 Всё слишком большое"
    if any(x in low for x in ("не могу начать", "начать", "старт", "запуск", "приступ")):
        return "🚪 Не могу начать"
    return ""


def infer_evening_checkin_answer(raw: str) -> str:
    low = (raw or "").lower()
    if any(x in low for x in ("срыв", "сорвал", "сорвался", "возвращ", "вернул")):
        return "↩️ срывался, но возвращался"
    if any(x in low for x in ("частично", "немного", "чуть", "наполовину", "кое-что")):
        return "😐 частично"
    if any(x in low for x in ("не сделал", "не сделала", "не получилось", "ничего", "провал")):
        return "❌ не сделал"
    if any(x in low for x in ("сделал", "сделала", "получилось", "готово", "выполнил", "выполнила")):
        return "✅ сделал"
    return ""


async def transcribe_voice_for_current_prompt(m: Message, u: Dict[str, Any]) -> Optional[str]:
    """Allow voice answers in free-text prompts without changing their state handlers."""
    if not m.voice or u.get("stage") not in VOICE_FREE_TEXT_STAGES:
        return None
    await m.answer("Слушаю голосовое и перевожу в текст…")
    voice_text = await whisper_transcribe(m)
    if not voice_text:
        await m.answer("Не смог разобрать голосовое. Можно ответить текстом или выбрать кнопку.")
        return ""
    await log_event(
        u.get("user_id"),
        u.get("stage", ""),
        "voice_answer_transcribed",
        {"len": len(voice_text)},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    await m.answer(f"Распознал: {clamp_str(voice_text, 700)}")
    return voice_text


DIAGNOSIS_INPUT_STAGES = {
    "await_input_mode",
    "choose_input_mode",
    "await_problem_text",
    "await_problem_voice",
    "taking_test",
    "run_analysis",
    "analysis_need_more",
}

ACTIVE_CRISIS_STAGES = {
    "crisis_stabilize",
    "crisis_choose_mode",
    CRISIS_WAITING_INPUT,
    "crisis_voice",
    "crisis_text",
    "crisis_plan_confirm",
    "crisis_tool_select",
    "crisis_action_await",
    "crisis_effect_await",
}



kb_safety_triage = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟥 Да"), KeyboardButton(text="🟨 Не уверен(а)"), KeyboardButton(text="🟩 Нет")],
        [KeyboardButton(text="👤 Да, один(одна)"), KeyboardButton(text="👥 Рядом есть люди")],
    ],
    resize_keyboard=True,
)

kb_safety_urgent = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟦 Написать человеку сейчас"), KeyboardButton(text="📞 Позвонить человеку")],
        [KeyboardButton(text="🚶 Перейти туда, где есть люди"), KeyboardButton(text="🧹 Убрать опасные предметы подальше")],
        [KeyboardButton(text="🟨 Я не знаю, кому написать")],
    ],
    resize_keyboard=True,
)

kb_safety_message_sent = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Да, отправил(а)"), KeyboardButton(text="⏳ Отправляю сейчас")],
        [KeyboardButton(text="❌ Не могу")],
    ],
    resize_keyboard=True,
)

kb_safety_contact_choices = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Близкий человек"), KeyboardButton(text="Партнёр")],
        [KeyboardButton(text="Коллега"), KeyboardButton(text="Сосед")],
        [KeyboardButton(text="Родственник")],
    ],
    resize_keyboard=True,
)

kb_safety_support_check = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Да"), KeyboardButton(text="Нет"), KeyboardButton(text="Не знаю")]],
    resize_keyboard=True,
)

kb_safety_crisis_actions = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👥 Я рядом с людьми")],
        [KeyboardButton(text="💬 Я написал живому человеку")],
        [KeyboardButton(text="📞 Мне нужна срочная помощь")],
        [KeyboardButton(text="🟥 Мне всё ещё небезопасно")],
    ],
    resize_keyboard=True,
)

kb_safety_message_followup = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👥 Я уже не один(одна)")],
        [KeyboardButton(text="🟥 Мне всё ещё небезопасно")],
        [KeyboardButton(text="↩️ Стало безопаснее")],
    ],
    resize_keyboard=True,
)

kb_safety_aftercare = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌙 Закрыть день без оценки")],
        [KeyboardButton(text="💬 Остаться в поддержке")],
        [KeyboardButton(text="🧭 Вернуться к задаче позже")],
    ],
    resize_keyboard=True,
)

SAFETY_TRIAGE_TEXT = (
    "Сейчас не режим продуктивности. Я хочу помочь тебе сделать ближайший безопасный шаг.\n\n"
    "Ответь кнопкой:\n"
    "1. Есть риск, что ты можешь навредить себе в ближайшие минуты?\n"
    "🟥 Да\n"
    "🟨 Не уверен(а)\n"
    "🟩 Нет\n\n"
    "2. Ты сейчас один(одна)?\n"
    "👤 Да, один(одна)\n"
    "👥 Рядом есть люди\n\n"
    "SKILLER не ведёт срочную помощь. Если тебе нужна срочная живая помощь, выходи за пределы бота "
    "и обращайся к людям или профильным службам вне SKILLER."
)

SAFETY_MESSAGE_TEMPLATE = (
    "Мне сейчас очень плохо и страшно оставаться одному(одной). Можешь побыть со мной на связи или "
    "позвонить мне в ближайшие 10 минут? Мне не нужен совет, мне важно не быть одному(одной)."
)

SAFETY_ACTIVE_TEXT = (
    "Сейчас важнее не разбираться с задачами, а сделать ближайшие 10 минут безопаснее."
)

SAFETY_MESSAGE_FOLLOWUP_TEXT = (
    "Хорошо. Останься на связи с этим человеком.\n\n"
    "Сейчас не нужно решать ничего про работу, отношения или будущее."
)

SAFETY_AFTERCARE_TEXT = (
    "Хорошо. Сегодня не нужно возвращаться к задаче.\n\n"
    "Можно просто остаться в спокойном режиме."
)

SAFETY_SUPPORT_PROTOCOL = (
    "Короткий протокол на 10 минут:\n\n"
    "1. Поставь обе ноги на пол.\n"
    "2. Сделай три медленных выдоха, длиннее вдоха.\n"
    "3. Назови 5 предметов вокруг.\n"
    "4. Выпей воды, если можешь.\n"
    "5. Выбери одного живого человека, которому можно написать.\n"
    "6. Не принимай сейчас решений о себе, работе, отношениях или будущем.\n\n"
    "Стало хотя бы на 5% безопаснее или спокойнее?"
)


def safety_mode(u: Dict[str, Any]) -> str:
    mode = str(u.get("safety_mode") or "none")
    if mode == "inactive":
        return "none"
    return mode if mode in SAFETY_MODES else "none"


def emergency_number_for_user(u: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> str:
    return ""


def safety_resume_snapshot(u: Dict[str, Any], source: str) -> str:
    return json.dumps({
        "source": source,
        "stage": u.get("stage"),
        "current_step": u.get("current_step"),
        "today_target": u.get("today_target"),
        "current_skill": u.get("current_skill"),
        "pending_skill_id": u.get("pending_skill_id"),
        "pending_skill_day": u.get("pending_skill_day"),
        "day": u.get("day"),
        "day_core_skill_id": u.get("day_core_skill_id"),
    }, ensure_ascii=False)


def safety_signal_details(text: str, explicit: bool = False) -> Dict[str, Any]:
    low = (text or "").lower().strip()
    high_markers = (
        "хочу умереть", "хочу покончить", "покончить с собой", "сделаю с собой", "наврежу себе",
        "навредить себе", "не хочу жить", "лучше бы меня не было", "не вижу выхода",
        "не могу оставаться один", "не могу оставаться одна", "боюсь, что сорвусь", "боюсь что сорвусь",
        "есть план", "таблет", "нож", "оруж", "сейчас сделаю", "прощайте", "суицид", "самоуб",
    )
    acute_markers = (
        "мне очень плохо", "не справляюсь", "отчаяние", "паника", "паническая", "ничего не ел",
        "ничего не ела", "не ел", "не ела", "не спал", "не спала", "не могу остановиться",
        "страшно одному", "страшно одной", "страшно оставаться одному", "страшно оставаться одной", "всё навалилось", "все навалилось", "накрывает",
        "не могу дышать",
    )
    loneliness = any(x in low for x in ("один", "одна", "одному", "одной", "никого рядом", "страшно одному", "страшно одной"))
    no_sleep_food = any(x in low for x in ("не спал", "не спала", "не ел", "не ела", "ничего не ел", "ничего не ела"))
    despair = any(x in low for x in ("отчаяние", "не вижу выхода", "лучше бы меня не было"))
    self_harm = crisis_safety_check(low).get("self_harm") or any(x in low for x in high_markers)
    acute = any(x in low for x in acute_markers)
    high = bool(self_harm and (loneliness or no_sleep_food or despair or crisis_safety_check(low).get("means_plan_intent"))) or any(x in low for x in high_markers)
    triggered = explicit or high or acute or bool(self_harm)
    return {"triggered": triggered, "high": high, "acute": acute, "self_harm": bool(self_harm), "loneliness": loneliness, "no_sleep_food": no_sleep_food, "despair": despair}


def has_crisis_safety_signal(text: str, stage: str) -> bool:
    """SKILLER no longer opens a separate crisis/emergency product flow."""
    return False


async def start_safety_interceptor(m: Message, u: Dict[str, Any], text: str, source: str, explicit: bool = False) -> bool:
    details = safety_signal_details(text, explicit=explicit)
    if not details.get("triggered"):
        return False
    if safety_mode(u) == "none":
        u["safety_resume_context"] = safety_resume_snapshot(u, source)
    u["safety_mode"] = "active" if details.get("high") else "triage"
    u["safety_last_risk"] = "yes" if details.get("high") else "unknown"
    u["safety_contact_status"] = u.get("safety_contact_status") or "not_asked"
    u["stage"] = "safety_mode"
    set_current_state(u, STATE_SAFETY_LOCK, close_action=True)
    await save_user(u, DB_PATH)
    await bot_record_action_event(u, "crisis_started", metadata={"source": source, **details})
    await log_event(u["user_id"], "safety", "safety_interceptor_started", {"source": source, **details}, DB_PATH, SHEETS_WEBHOOK_URL)
    await m.answer(SAFETY_TRIAGE_TEXT, reply_markup=kb_safety_triage)
    if details.get("high"):
        await m.answer(SAFETY_ACTIVE_TEXT, reply_markup=kb_safety_crisis_actions)
    return True


async def show_safety_urgent(m: Message, u: Dict[str, Any], reason: str = ""):
    u["safety_mode"] = "active"
    if u.get("safety_last_risk") != "no":
        u["safety_last_risk"] = "yes" if reason != "uncertain" else "uncertain"
    u["stage"] = "safety_mode"
    set_current_state(u, STATE_SAFETY_LOCK, close_action=True)
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "safety", "safety_urgent_shown", {"reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
    await m.answer(SAFETY_ACTIVE_TEXT, reply_markup=kb_safety_crisis_actions)


async def show_safety_support(m: Message, u: Dict[str, Any], reason: str = ""):
    u["safety_mode"] = "support"
    u["safety_last_risk"] = "no"
    u["stage"] = "safety_mode"
    set_current_state(u, STATE_SAFETY_LOCK, close_action=True)
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "safety", "safety_support_shown", {"reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
    await m.answer(SAFETY_SUPPORT_PROTOCOL, reply_markup=kb_safety_support_check)


async def complete_safety_day(m: Message, u: Dict[str, Any]):
    await mark_day_closed(u, "safety_aftercare_done")
    u["safety_mode"] = "none"
    u["safety_resume_context"] = None
    u["stage"] = "waiting_next_day"
    await save_user(u, DB_PATH)
    await bot_record_action_event(u, "crisis_resolved_or_paused", metadata={"source": "safety_day_closed"})
    await log_event(u["user_id"], "safety", "safety_day_closed", {}, DB_PATH, SHEETS_WEBHOOK_URL)
    await answer_with_keyboard(m, u, "Ок. На сегодня достаточно. Никаких новых навыков сейчас.", kb_day_core_stop, "waiting_next_day")




def active_safety_allowed_buttons() -> set[str]:
    return {
        "👥 Я рядом с людьми",
        "💬 Я написал живому человеку",
        "📞 Мне нужна срочная помощь",
        "🟥 Мне всё ещё небезопасно",
        "👥 Я уже не один(одна)",
        "↩️ Стало безопаснее",
        "🌙 Закрыть день без оценки",
        "💬 Остаться в поддержке",
        "🧭 Вернуться к задаче позже",
        # Legacy crisis-only buttons from older keyboards remain safe to swallow in the same lock.
        "🟦 Написать человеку сейчас",
        "📞 Позвонить человеку",
        "🚶 Перейти туда, где есть люди",
        "🧹 Убрать опасные предметы подальше",
        "🟨 Я не знаю, кому написать",
        "✅ Да, отправил(а)",
        "⏳ Отправляю сейчас",
        "❌ Не могу",
        "Близкий человек",
        "Партнёр",
        "Коллега",
        "Сосед",
        "Родственник",
    }


async def repeat_active_safety_screen(m: Message, u: Dict[str, Any]) -> None:
    if u.get("safety_contact_status") == "aftercare":
        await m.answer(SAFETY_AFTERCARE_TEXT, reply_markup=kb_safety_aftercare)
    elif u.get("safety_contact_status") == "sent_message":
        await m.answer(SAFETY_MESSAGE_FOLLOWUP_TEXT, reply_markup=kb_safety_message_followup)
    else:
        await m.answer(SAFETY_ACTIVE_TEXT, reply_markup=kb_safety_crisis_actions)


async def handle_safety_callback(c: CallbackQuery, u: Dict[str, Any], data: str) -> bool:
    """Prevent stale inline callbacks from bypassing an active safety block."""
    mode = safety_mode(u)
    if mode == "none":
        return False
    await log_event(u["user_id"], "safety", "safety_blocked_callback", {"data": (data or "")[:80]}, DB_PATH, SHEETS_WEBHOOK_URL)
    if mode == "active":
        await repeat_active_safety_screen(c.message, u)
    elif mode == "urgent":
        await show_safety_urgent(c.message, u, "blocked_callback")
    elif mode == "support":
        await c.message.answer("Сейчас ещё режим поддержки, не продуктивности.", reply_markup=kb_safety_aftercare)
    else:
        await c.message.answer(SAFETY_TRIAGE_TEXT, reply_markup=kb_safety_triage)
    await c.answer()
    return True


async def handle_safety_mode(m: Message, u: Dict[str, Any], text: str) -> bool:
    mode = safety_mode(u)
    if mode == "none":
        return False
    low = (text or "").lower().strip()
    # While safety_mode=active, no productivity UI is allowed: skills, map, trainers, offer,
    # progress, productive day closing, or skill switching are all swallowed.
    if mode == "active" and text not in active_safety_allowed_buttons():
        await log_event(u["user_id"], "safety", "safety_blocked_non_crisis_input", {"text": text[:80]}, DB_PATH, SHEETS_WEBHOOK_URL)
        await repeat_active_safety_screen(m, u)
        return True

    # Old productivity buttons are intentionally swallowed while any safety mode is active.
    if text not in active_safety_allowed_buttons() and (should_route_action_request(text, low, u) or any(x in low for x in ("полный режим", "оффер", "моя карта", "прогресс", "тренер", "карта", "закрыть день", "до завтра", "сменить навык", "навык"))):
        await log_event(u["user_id"], "safety", "safety_blocked_productivity_button", {"text": text[:80]}, DB_PATH, SHEETS_WEBHOOK_URL)
        if mode == "active":
            await repeat_active_safety_screen(m, u)
        elif mode == "urgent":
            await show_safety_urgent(m, u, "blocked_productivity")
        elif mode == "support":
            await m.answer("Сейчас ещё режим поддержки, не продуктивности.", reply_markup=kb_safety_aftercare)
        else:
            await m.answer(SAFETY_TRIAGE_TEXT, reply_markup=kb_safety_triage)
        return True

    if mode == "triage":
        if text == "🟥 Да":
            await show_safety_urgent(m, u, "risk_yes")
            return True
        if text == "🟨 Не уверен(а)":
            await show_safety_urgent(m, u, "uncertain")
            return True
        if text == "🟩 Нет":
            await show_safety_support(m, u, "risk_no")
            return True
        if text == "👤 Да, один(одна)":
            u["safety_last_risk"] = "uncertain"
            await show_safety_urgent(m, u, "alone")
            return True
        if text == "👥 Рядом есть люди":
            await m.answer("Хорошо. Оставайся ближе к людям и ответь на вопрос про риск.", reply_markup=kb_safety_triage)
            return True
        await m.answer(SAFETY_TRIAGE_TEXT, reply_markup=kb_safety_triage)
        return True

    if mode in {"urgent", "active"}:
        if text == "👥 Я рядом с людьми":
            await m.answer(SAFETY_ACTIVE_TEXT, reply_markup=kb_safety_crisis_actions)
            return True
        if text == "👥 Я уже не один(одна)":
            await m.answer(SAFETY_MESSAGE_FOLLOWUP_TEXT, reply_markup=kb_safety_message_followup)
            return True
        if text in {"💬 Я написал живому человеку", "✅ Да, отправил(а)"}:
            u["safety_contact_status"] = "sent_message"
            await save_user(u, DB_PATH)
            await m.answer(SAFETY_MESSAGE_FOLLOWUP_TEXT, reply_markup=kb_safety_message_followup)
            return True
        if text == "📞 Мне нужна срочная помощь":
            await m.answer("В SKILLER нет срочного сценария. Выходи за пределы бота и обращайся к живой помощи вне SKILLER.", reply_markup=kb_safety_crisis_actions)
            return True
        if text == "🟥 Мне всё ещё небезопасно":
            await m.answer(SAFETY_ACTIVE_TEXT, reply_markup=kb_safety_crisis_actions)
            return True
        if text == "↩️ Стало безопаснее":
            # Stay in safety_mode=active: do not reopen productivity until explicit return-later.
            u["safety_mode"] = "active"
            u["safety_last_risk"] = "no"
            u["safety_contact_status"] = "aftercare"
            await save_user(u, DB_PATH)
            await m.answer(SAFETY_AFTERCARE_TEXT, reply_markup=kb_safety_aftercare)
            return True
        if text in {"🛑 На сегодня достаточно", "🌙 Закрыть день без оценки"}:
            await complete_safety_day(m, u)
            return True
        if text == "💬 Остаться в поддержке":
            u["safety_contact_status"] = "aftercare"
            await save_user(u, DB_PATH)
            await m.answer(SAFETY_AFTERCARE_TEXT, reply_markup=kb_safety_aftercare)
            return True
        if text in {"↩️ Вернуться к задаче позже", "🧭 Вернуться к задаче позже"}:
            if u.get("safety_last_risk") != "no":
                await m.answer("Пока не возвращаемся к задаче: риск не отмечен как 'нет'. Остаёмся в поддержке.", reply_markup=kb_safety_aftercare)
                return True
            u["safety_mode"] = "inactive"
            u["return_mode"] = "later"
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "safety", "safety_user_chose_resume_later", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, "Ок. Вернуться можно позже явным действием. Сейчас без давления.", kb_training_main, "training_main")
            return True
        if text == "🟦 Написать человеку сейчас":
            u["safety_contact_status"] = "offered"
            await save_user(u, DB_PATH)
            await m.answer(f"Скопируй и отправь:\n\n{SAFETY_MESSAGE_TEMPLATE}")
            await m.answer("Удалось отправить сообщение?", reply_markup=kb_safety_message_sent)
            return True
        if text == "📞 Позвонить человеку":
            u["safety_contact_status"] = "called"
            await save_user(u, DB_PATH)
            await m.answer("Позвони живому человеку сейчас. Если не отвечает — выбирай другого доступного человека вне бота.", reply_markup=kb_safety_contact_choices)
            return True
        if text == "🚶 Перейти туда, где есть люди":
            await m.answer("Перейди в место, где рядом есть люди: кухня, подъезд, улица, ресепшен, сосед, магазин. Потом выбери следующий безопасный шаг.", reply_markup=kb_safety_crisis_actions)
            return True
        if text == "🧹 Убрать опасные предметы подальше":
            await m.answer("Отойди от опасных предметов или положи их за дверь / в другую комнату / рядом с другим человеком. Потом выбери контакт с живым человеком.", reply_markup=kb_safety_crisis_actions)
            return True
        if text == "🟨 Я не знаю, кому написать" or text == "❌ Не могу":
            u["safety_contact_status"] = "unavailable"
            await save_user(u, DB_PATH)
            await m.answer("Выбери самый доступный вариант. Не нужен идеальный человек — нужен живой контакт.", reply_markup=kb_safety_contact_choices)
            return True
        if text == "✅ Да, отправил(а)":
            u["safety_contact_status"] = "sent_message"
            await save_user(u, DB_PATH)
            await show_safety_support(m, u, "message_sent")
            return True
        if text == "⏳ Отправляю сейчас":
            await m.answer("Ок. Отправь сейчас. Я подожду здесь. Потом нажми ✅ Да, отправил(а).", reply_markup=kb_safety_message_sent)
            return True
        if text in {"Близкий человек", "Партнёр", "Коллега", "Сосед", "Родственник"}:
            await m.answer(f"Да. Выбери: {text}. Напиши или позвони сейчас.\n\n{SAFETY_MESSAGE_TEMPLATE}", reply_markup=kb_safety_message_sent)
            return True
        await show_safety_urgent(m, u, "urgent_repeat")
        return True

    if mode == "support":
        if text in {"Да", "Нет", "Не знаю"}:
            if text == "Да":
                await m.answer("Хорошо. Не возвращаемся автоматически к задаче. Выбери, что безопаснее дальше.", reply_markup=kb_safety_aftercare)
            else:
                await m.answer("Тогда остаёмся в поддержке. Повтори протокол ещё 10 минут или выбери живой контакт.", reply_markup=kb_safety_aftercare)
            return True
        if text in {"🛑 На сегодня достаточно", "🌙 Закрыть день без оценки"}:
            await complete_safety_day(m, u)
            return True
        if text == "💬 Остаться в поддержке":
            if mode == "active":
                u["safety_contact_status"] = "aftercare"
                await save_user(u, DB_PATH)
                await m.answer(SAFETY_AFTERCARE_TEXT, reply_markup=kb_safety_aftercare)
            else:
                await show_safety_support(m, u, "stay_support")
            return True
        if text in {"↩️ Вернуться к задаче позже", "🧭 Вернуться к задаче позже"}:
            if u.get("safety_last_risk") != "no":
                await m.answer("Пока не возвращаемся к задаче: риск не отмечен как 'нет'. Остаёмся в поддержке.", reply_markup=kb_safety_aftercare)
                return True
            u["safety_mode"] = "inactive"
            u["return_mode"] = "later"
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "safety", "safety_user_chose_resume_later", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, "Ок. Вернуться можно позже явным действием. Сейчас без давления.", kb_training_main, "training_main")
            return True
        await m.answer(SAFETY_SUPPORT_PROTOCOL, reply_markup=kb_safety_support_check)
        return True
    return False


def should_open_global_crisis(text: str, stage: str) -> bool:
    """Do not route user text into a separate crisis/emergency scenario."""
    return False


def user_is_in_action_loop(u: Dict[str, Any]) -> bool:
    """Пользователь уже после первой карты и находится в тренировочном loop."""
    return bool(u.get("analysis_json") or u.get("plan_json") or u.get("has_started_training")) and u.get("stage") in ACTION_RELATED_STAGES






def mark_day_core_round_done(u: Dict[str, Any]) -> int:
    """Increment today's fixed core-skill round counter and return the new value."""
    sid = current_skill_id(u)
    updates = engine_build_day_core_updates(u, sid) if sid else {}
    u.update(updates)
    current = engine_core_round_count_today(u)
    u["day_core_round_count"] = current + 1
    return int(u["day_core_round_count"])


def replace_day_core_skill(u: Dict[str, Any], skill_id: str):
    """Explicit replacement: swap today's core skill and reset its daily rounds."""
    if skill_id:
        u.update(engine_build_day_core_updates(u, skill_id, reset_rounds=True))


def clear_day_core_lock(u: Dict[str, Any]):
    """Admin/test helper: allow a new core skill without waiting for local midnight."""
    for key in (
        "day_core_skill_id",
        "day_core_skill_date",
        "current_core_skill_id",
        "current_skill_variant_id",
        "current_core_skill_date",
        "pending_skill_id",
        "pending_skill_day",
        "last_offer_shown_at",
    ):
        u[key] = None
    for key in (
        "day_core_round_count",
        "current_skill_completed_count",
        "daily_replacement_count",
        "daily_skill_done",
        "today_closed",
        "offer_shown_today",
        "skill_limit_reached",
        "crisis_free_count",
    ):
        u[key] = 0


def set_last_explanation_context(u: Dict[str, Any], ctx_type: str, title: str, reason: str, evidence: Optional[List[str]] = None, next_step: str = ""):
    u["last_explanation_context"] = json.dumps({
        "type": ctx_type,
        "title": title,
        "reason": reason,
        "evidence": evidence or [],
        "next_step": next_step,
    }, ensure_ascii=False)


def render_last_explanation_context(u: Dict[str, Any]) -> str:
    raw = u.get("last_explanation_context") or ""
    try:
        ctx = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        ctx = {}
    if not ctx:
        return (
            "Пока нечего раскрывать: я ещё не сохранил контекст последнего шага. "
            "Напиши, что именно объяснить: навык, карту, кризисный разбор или предложение."
        )
    ctx_type = str(ctx.get("type") or "").strip()
    title = str(ctx.get("title") or "последний шаг").strip()
    if ctx_type == "skill":
        lines = [f"Почему я предлагаю {title}:"]
    elif ctx_type == "hypothesis":
        lines = [f"Почему такая гипотеза: {title}"]
    elif ctx_type == "offer":
        lines = [f"Почему я показываю это предложение: {title}"]
    elif ctx_type == "map":
        lines = [f"Как читать карту: {title}"]
    elif ctx_type == "crisis":
        lines = [f"Почему такой кризисный шаг: {title}"]
    else:
        lines = [f"📚 Подробнее: {title}"]
    if ctx.get("reason"):
        lines += ["", str(ctx.get("reason"))]
    evidence = ctx.get("evidence") or []
    if evidence:
        lines += [""] + [f"— {x}" for x in evidence if x]
    if ctx.get("next_step"):
        lines += ["", str(ctx.get("next_step"))]
    return "\n".join(lines)


def _skill_label(skill_id: Optional[str], fallback: str = "маленький вход") -> str:
    return label(SKILL_LABELS, skill_id, fallback) if skill_id else fallback



SKILL_STATUS_WORDING = {
    "proposed": "Пока проверяем, помогает ли этот вход.",
    "tested_once": "Шаг был сделан, эффект пока не подтверждён.",
    "started_task": "Шаг помог начать задачу; проверяем, станет ли легче повторять.",
    "promising": "Есть первый сигнал, что этот вход тебе помогает.",
    "confirmed": "Этот вход часто помогает тебе вернуться к задаче.",
    "not_helpful": "Этот навык пока не дал эффекта. Не будем повторять его автоматически.",
}


def skill_status_wording(status: str) -> str:
    return SKILL_STATUS_WORDING.get(str(status or "proposed"), SKILL_STATUS_WORDING["proposed"])


def _skill_status_from_counts(attempt_count: int, completed_count: int, stuck_count: int, effect_rating: int, helpful_count: int = 0, started_count: int = 0) -> str:
    if helpful_count >= 2:
        return "confirmed"
    if helpful_count >= 1 or effect_rating > 0:
        return "promising"
    if attempt_count >= 2 and completed_count == 0 and stuck_count >= 2:
        return "not_helpful"
    if started_count >= 1:
        return "started_task"
    if completed_count >= 1 or attempt_count >= 1:
        return "tested_once"
    return "proposed"


async def build_skill_map_data(u: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    """Single source of truth for map/offer/day-close/skill-card skill facts."""
    user_id = int(u.get("user_id") or 0)
    rows = []
    if user_id:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT skill_id, event_type, metadata, created_at FROM action_events WHERE user_id=? AND skill_id IS NOT NULL AND skill_id != '' ORDER BY id",
                (user_id,),
            )
            rows = await cur.fetchall()
    records: Dict[str, Dict[str, Any]] = {}

    def rec(skill_id: Any) -> Dict[str, Any]:
        sid = str(skill_id or "").strip() or "open_only"
        item = records.setdefault(sid, {
            "skill_id": sid,
            "status": "proposed",
            "attempt_count": 0,
            "completed_count": 0,
            "stuck_count": 0,
            "effect_rating": 0,
            "helpful_count": 0,
            "started_count": 0,
            "last_result": "proposed",
        })
        return item

    proposed_ids = [
        u.get("current_core_skill_id"), u.get("current_skill_variant_id"), u.get("daily_skill_id"),
        profile.get("recommended_core_skill"), profile.get("recommended_variant"), profile.get("best_variant"),
    ]
    for sid in proposed_ids + _profile_list(profile.get("successful_skills")) + _profile_list(profile.get("failed_skills")) + _profile_list(profile.get("completed_skills_effect_unknown")):
        if sid:
            rec(sid)

    helpful_ids = set(_profile_list(profile.get("successful_skills")) + [str(profile.get("best_skill") or ""), str(profile.get("last_successful_skill") or "")])
    failed_ids = set(_profile_list(profile.get("failed_skills")) + [str(profile.get("failed_skill") or ""), str(profile.get("worst_skill") or "")])
    unknown_done_ids = set(_profile_list(profile.get("completed_skills_effect_unknown")))

    for row in rows:
        sid, event_type, metadata, _created_at = row
        item = rec(sid)
        event_type = str(event_type or "")
        if event_type in {"attempt_started", "attempt_completed_self_reported", "skill_result_reported", "action_failed", "step_reduced", "too_hard_reported", "no_energy_reported", "slip_reported"}:
            item["attempt_count"] += 1
        if event_type in {"attempt_completed_self_reported", "returned_after_slip"}:
            item["completed_count"] += 1
            item["last_result"] = "completed"
        if event_type in {"action_failed", "step_reduced", "too_hard_reported", "no_energy_reported", "slip_reported"}:
            item["stuck_count"] += 1
            item["last_result"] = "stuck"
        try:
            meta = json.loads(metadata or "{}") if isinstance(metadata, str) else {}
        except Exception:
            meta = {}
        rating = int(meta.get("effect_rating") or meta.get("rating") or 0) if isinstance(meta, dict) else 0
        item["effect_rating"] = max(int(item.get("effect_rating") or 0), rating)
        if event_type == "skill_result_reported" and isinstance(meta, dict):
            result_status = str(meta.get("result_status") or "")
            if rating > 0 or result_status == "done_relief":
                item["helpful_count"] = int(item.get("helpful_count") or 0) + 1
                item["last_result"] = "helpful"
            elif result_status == "done_started_task":
                item["started_count"] = int(item.get("started_count") or 0) + 1
                item["last_result"] = "started_task"
            elif result_status in {"done_no_relief", "not_completed", "not_my_skill", "too_hard", "needs_other_entry", "not_tried"}:
                item["last_result"] = result_status

    for sid in helpful_ids:
        if sid:
            item = rec(sid)
            item["completed_count"] = max(int(item["completed_count"]), 1)
            item["effect_rating"] = max(int(item["effect_rating"]), 1)
            item["helpful_count"] = max(int(item.get("helpful_count") or 0), 1)
            item["last_result"] = "helpful"
    for sid in unknown_done_ids:
        if sid:
            item = rec(sid)
            item["completed_count"] = max(int(item["completed_count"]), 1)
            item["last_result"] = "completed_effect_unknown"
    for sid in failed_ids:
        if sid:
            item = rec(sid)
            item["stuck_count"] = max(int(item["stuck_count"]), 1)
            item["last_result"] = "not_helpful_signal"

    for item in records.values():
        item["attempt_count"] = max(int(item["attempt_count"]), int(item["completed_count"]), int(item["stuck_count"]))
        item["status"] = _skill_status_from_counts(int(item["attempt_count"]), int(item["completed_count"]), int(item["stuck_count"]), int(item["effect_rating"]), int(item.get("helpful_count") or 0), int(item.get("started_count") or 0))
        item["status_text"] = skill_status_wording(item["status"])
        item["title"] = _skill_label(item["skill_id"], item["skill_id"])
    ordered = sorted(records.values(), key=lambda x: (x["status"] == "proposed", -int(x["completed_count"]), -int(x["attempt_count"]), x["title"]))
    return {"skills": ordered, "by_id": {item["skill_id"]: item for item in ordered}}


def skill_map_lines(skill_map: Dict[str, Any], limit: int = 4) -> str:
    skills = (skill_map or {}).get("skills") or []
    if not skills:
        return "— пока нет проверенных навыков: первый вход только выбираем"
    return "\n".join(
        f"— {_skill_label(item.get('skill_id'), item.get('skill_id'))}: {skill_status_wording(item.get('status'))}"
        for item in skills[:limit]
    )


def current_skill_status_note(u: Dict[str, Any], skill_map: Dict[str, Any]) -> str:
    sid = current_skill_id(u) or u.get("daily_skill_id") or u.get("current_skill_variant_id")
    item = ((skill_map or {}).get("by_id") or {}).get(str(sid or ""))
    if not item:
        return skill_status_wording("proposed")
    return skill_status_wording(item.get("status"))

def _profile_pick(profile: Dict[str, Any], keys: List[str], fallback: str) -> str:
    for key in keys:
        value = profile.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _preferred_activation_from_profile(profile: Dict[str, Any]) -> str:
    raw = str(profile.get("preferred_activation") or "").strip()
    best_skill = str(profile.get("best_skill") or profile.get("last_successful_skill") or "")
    if raw == "body_doubling" or best_skill == "body_doubling_plan":
        return (
            "👥 в коворкинге\n"
            "👥 рядом с человеком\n"
            "👥 на коротком созвоне\n"
            "👥 при ощущении присутствия другого"
        )
    if raw == "small_visible_step" or best_skill in {"open_only", "visible_next_step", "ninety_sec_start", "task_naming"}:
        return "🧩 когда первый шаг маленький и видимый"
    if raw == "phone_away" or best_skill == "phone_far_3min":
        return "📵 когда отвлечения убраны из зоны доступа"
    return "🧩 когда вход маленький и заранее видимый"


def _avoidance_trigger_from_profile(profile: Dict[str, Any]) -> str:
    raw = str(profile.get("avoidance_trigger") or profile.get("avoidance_reason") or profile.get("emotional_trigger") or "")
    mapping = {
        "task_too_big": "перегруз перед стартом",
        "entry_too_large": "перегруз перед стартом",
        "unclear_first_step": "не видно первого шага",
        "fear_of_bad_result": "страх ошибки или неидеального результата",
        "shame_or_anxiety": "напряжение и самокритика перед входом",
        "low_energy": "мало энергии на вход",
        "fatigue_or_overload": "усталость и перегруз",
        "distraction_or_restlessness": "отвлечения и дерганый фокус",
        "too_many_options": "слишком много вариантов",
        "perfectionism_start_block": "желание начать идеально",
    }
    return mapping.get(raw, raw or "перегруз перед стартом")


def _start_pattern_from_profile(profile: Dict[str, Any]) -> str:
    raw = str(profile.get("main_pattern") or profile.get("avoidance_pattern") or profile.get("specific_pattern") or "")
    mapping = {
        "entry_too_large": "когда вход выглядит слишком большим",
        "micro_entry_block": "когда даже микрошаг кажется лишним усилием",
        "start_avoidance": "когда задача открытая и без понятного края",
        "perfectionism_start_block": "когда можно сделать неидеально",
        "attention_fragmentation": "когда вокруг много переключений",
        "anxiety_avoidance": "когда результат заранее кажется рискованным",
    }
    return mapping.get(raw, raw or "когда задача без чёткого первого шага")


def _return_pattern_from_profile(u: Dict[str, Any], profile: Dict[str, Any]) -> str:
    raw = str(profile.get("return_pattern") or "")
    if raw == "strong_return_skill":
        return "возвращаешься после выпадения — это сильный рабочий сигнал"
    count = int(u.get("return_count") or profile.get("return_count") or 0)
    if count >= 2:
        return "часто возвращаешься после выпадения, если вход не раздувать"
    if count == 1:
        return "можешь вернуться после выпадения, если шаг короткий"
    return "пока нужен мягкий возврат без самокритики"


def _downscale_pattern_from_profile(profile: Dict[str, Any]) -> str:
    raw = str(profile.get("downscale_pattern") or "")
    if raw == "entry_too_large":
        return "лучше работает уменьшение входа, а не давление на себя"
    if raw == "needs_smaller_step":
        return "нужен шаг меньше исходного, без обещания продолжать"
    if int(profile.get("downscale_count") or 0) > 0:
        return "помогает уменьшить шаг до минимального контакта с задачей"
    return "проверяем, какой минимальный шаг не вызывает сопротивления"


def _best_skills_text(profile: Dict[str, Any]) -> str:
    best = profile.get("best_skill") or profile.get("last_successful_skill")
    if best:
        return f"🧩 {_skill_label(best)}"
    return "🧩 Маленький вход\n🧩 Видимый первый шаг"


def _profile_skill_list_text(profile: Dict[str, Any], key: str, fallback: str = "пока собираем") -> str:
    items = _profile_list(profile.get(key))
    if not items:
        return fallback
    return "\n".join(f"— {_skill_label(str(item))}" for item in items[:4])


def _trainer_mode_label(key: str) -> str:
    trainer = TRAINERS.get(str(key), {})
    return str(trainer.get("name") or key or "режим")


def _trainer_history_label(item: str) -> str:
    raw = str(item or "")
    path = raw.split("@", 1)[0]
    if "->" not in path:
        return raw
    left, right = path.split("->", 1)
    return f"{_trainer_mode_label(left)} → {_trainer_mode_label(right)}"


def _trainer_mode_map_text(profile: Dict[str, Any]) -> str:
    current = profile.get("trainer_current_mode") or profile.get("preferred_trainer_mode") or ""
    count = int(profile.get("trainer_switch_count") or 0)
    if not current and not count:
        return ""
    name = _trainer_mode_label(str(current))
    history = _profile_list(profile.get("trainer_switch_history"))
    history_text = f"; пробовали: {', '.join(_trainer_history_label(x) for x in history[:2])}" if history else ""
    return f"— режим поддержки: сейчас {name}, смен было {count}/{TRAINER_SWITCH_LIMIT}{history_text}"


def _working_map_behavior_records_text(profile: Dict[str, Any]) -> str:
    main = profile.get("main_hypothesis") or "рабочая гипотеза уточняется"
    trainer_line = _trainer_mode_map_text(profile)
    trainer_block = f"\n\nЧто видно по режиму тренера:\n{trainer_line}" if trainer_line else ""
    return (
        "Что проверяли действиями:\n"
        f"— исходная гипотеза: {main}\n\n"
        "Статусы навыков:\n"
        f"{skill_map_lines(profile.get('_skill_map') or {}, 5)}"
        f"{trainer_block}"
    )


def build_profile_map_summary(u: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    done_count = int(profile.get("action_done_count") or u.get("done_count") or 0)
    downscale_count = int(profile.get("downscale_count") or 0)
    return_count = int(u.get("return_count") or profile.get("return_count") or 0)
    preferred_activation = _preferred_activation_from_profile(profile)
    worst_skill = profile.get("worst_skill") or profile.get("failed_skill") or ""
    slip_pattern = profile.get("slip_pattern") or profile.get("return_pattern") or ("strong_return_skill" if return_count >= 2 else "return_needs_small_step")
    summary = {
        "main_pattern": str(profile.get("main_pattern") or profile.get("avoidance_pattern") or "start_avoidance"),
        "start_pattern_text": _start_pattern_from_profile(profile),
        "avoidance_trigger": _avoidance_trigger_from_profile(profile),
        "best_skills_text": skill_map_lines(profile.get("_skill_map") or {}, 3) if profile.get("_skill_map") else _best_skills_text(profile),
        "downscale_pattern": _downscale_pattern_from_profile(profile),
        "preferred_activation": preferred_activation,
        "return_pattern": _return_pattern_from_profile(u, profile),
        "slip_pattern": slip_pattern,
        "done_count": done_count,
        "downscale_count": downscale_count,
        "return_count": return_count,
        "best_skill": profile.get("best_skill") or profile.get("last_successful_skill") or "open_only",
        "failed_skill": profile.get("failed_skill") or "",
        "worst_skill": worst_skill,
        "energy_pattern": profile.get("energy_pattern") or ("low_start_energy" if downscale_count else "unknown"),
        "attention_pattern": profile.get("attention_pattern") or "unknown",
        "side_skill_interest": profile.get("side_skill_interest") or "unknown",
        "failed_reason_count": int(profile.get("failed_reason_count") or profile.get("action_failed_count") or 0),
        "effect_notes": profile.get("last_effect_note") or profile.get("last_after_action_note") or "",
        "effect_tags": profile.get("effect_tags") or [],
        "attention_escape_count": int(profile.get("attention_escape_count") or (1 if profile.get("attention_pattern") == "scroll_autopilot" else 0)),
        "shame_signal": profile.get("shame_signal") or ("shame_self_attack" if profile.get("main_pattern") == "shame_self_attack" else ""),
        "body_doubling_signal": profile.get("body_doubling_signal") or ("body_doubling" if profile.get("preferred_activation") == "body_doubling" else ""),
        "energy_signal": profile.get("energy_signal") or profile.get("energy_pattern") or "",
        "best_variant": profile.get("best_variant") or profile.get("best_skill") or profile.get("last_successful_skill") or "open_only",
        "main_hypothesis": profile.get("main_hypothesis") or "",
        "secondary_hypotheses": _profile_list(profile.get("secondary_hypotheses")),
        "confirmed_signals": _profile_list(profile.get("confirmed_signals")),
        "successful_skills": _profile_list(profile.get("successful_skills")),
        "failed_skills": _profile_list(profile.get("failed_skills")),
        "skill_map": profile.get("_skill_map") or {},
        "skill_map_text": skill_map_lines(profile.get("_skill_map") or {}, 5) if profile.get("_skill_map") else "",
        "behavior_records_text": _working_map_behavior_records_text(profile),
        "system_day_opened": _profile_list(profile.get("system_day_opened")),
        "system_day_useful": _profile_list(profile.get("system_day_useful")),
        "system_day_already": _profile_list(profile.get("system_day_already")),
        "last_system_day_id": profile.get("last_system_day_id") or "",
        "crisis_pattern": profile.get("last_crisis_pattern") or profile.get("crisis_pattern") or "",
        "crisis_skill": profile.get("last_crisis_skill") or profile.get("crisis_skill") or "",
        "crisis_effect": profile.get("last_crisis_effect") or profile.get("crisis_effect") or "",
        "most_common_crisis_pattern": profile.get("most_common_crisis_pattern") or "",
        "most_effective_crisis_skill": profile.get("most_effective_crisis_skill") or "",
        "crisis_count": int(profile.get("crisis_count") or u.get("crisis_count") or 0),
        "crisis_success_rate": profile.get("crisis_success_rate") or 0,
        "trainer_current_mode": profile.get("trainer_current_mode") or u.get("trainer_key") or "",
        "trainer_switch_count": int(profile.get("trainer_switch_count") or 0),
        "trainer_fit_signal": profile.get("trainer_fit_signal") or "",
    }
    system_lines = [x for x in (_system_day_signals_text(summary), _crisis_map_signals_text(summary)) if x]
    summary["system_day_signals"] = "\n".join(system_lines)
    if "рядом с человеком" in preferred_activation or "коворкинге" in preferred_activation or "созвоне" in preferred_activation:
        summary["preferred_activation_code"] = "body_doubling"
    elif "отвлечения" in preferred_activation:
        summary["preferred_activation_code"] = "phone_away"
    else:
        summary["preferred_activation_code"] = "small_visible_step"
    return summary


async def record_profile_signal(user_id: int, stage: str, patch: Dict[str, Any], *, source: str):
    safe_patch = {k: v for k, v in patch.items() if v not in (None, "")}
    if not safe_patch:
        return
    current_profile = await get_user_profile(user_id, DB_PATH)
    map_patch = development_map_event_patch(current_profile, safe_patch, source)
    model_events = user_model_events_from_signal(user_id, safe_patch, source)
    model_patch = {"user_model_events": model_events} if model_events else {}
    await update_user_profile(user_id, {**safe_patch, **map_patch, **model_patch}, DB_PATH, source=source)
    event_meta = {"source": source, **safe_patch}
    await log_event(user_id, stage, "profile_signal_detected", event_meta, DB_PATH, SHEETS_WEBHOOK_URL)
    await log_event(user_id, stage, "profile_map_updated", event_meta, DB_PATH, SHEETS_WEBHOOK_URL)




def effect_tags_from_note(note: str) -> List[str]:
    low = (note or "").lower()
    tags: List[str] = []
    if any(x in low for x in ("легче", "отпуст", "выдох", "спокой")):
        tags.append("relief")
    if any(x in low for x in ("меньше трев", "не так страш", "страшно меньше", "тревоги меньше")):
        tags.append("anxiety_down")
    if any(x in low for x in ("получ", "смог", "могу", "увер")):
        tags.append("confidence_up")
    if any(x in low for x in ("ясн", "понял", "видно", "понятнее")):
        tags.append("clarity_up")
    return tags or ["effect_noted"]


def after_action_note_saved_text(trainer_key: str) -> str:
    if trainer_key == "skinny":
        return "Записал. Это данные."
    if trainer_key == "beck":
        return "Записал. Это пойдёт в карту: что меняется после микрошагов."
    if trainer_key == "marsha":
        return "Записала. Хорошо, что ты это заметил — такие маленькие сдвиги важны."
    return "Записал.\nЭто важный сигнал для карты."


def analysis_need_more_expanded_text(previous_text: str, answer: str) -> str:
    base = (previous_text or "").strip()
    low = (answer or "").lower()
    if "страх" in low or "ошиб" in low:
        story = (
            "Пользователь уточнил, что вход чаще ломает страх ошибки. "
            "Значит перед действием появляется риск сделать неправильно, написать плохо или выглядеть глупо. "
            "Гипотеза: проблема не в лени, а в цене ошибки. Проверить нужно плохой черновик без отправки."
        )
    elif "перегруз" in low:
        story = (
            "Пользователь уточнил, что вход чаще ломает перегруз. "
            "Задача ощущается слишком большой, поэтому старт превращается в гору. "
            "Гипотеза: нужен резак задачи и первый физический шаг, а не полный план."
        )
    elif "отвлеч" in low or "📱" in answer:
        story = (
            "Пользователь уточнил, что вход чаще ломают отвлечения. "
            "Внимание уходит в быстрые награды: телефон, сообщения, лента или вкладки. "
            "Гипотеза: нужен контейнер внимания и барьер перед уходом."
        )
    elif "вариант" in low or "🌀" in answer:
        story = (
            "Пользователь уточнил, что вход чаще ломает слишком много вариантов. "
            "Мозгу приходится выбирать перед действием, и старт зависает. "
            "Гипотеза: нужен один видимый следующий шаг и уменьшение выбора."
        )
    elif "смысл" in low:
        story = (
            "Пользователь уточнил, что вход чаще ломает отсутствие смысла. "
            "Задача не цепляется за понятную причину, поэтому действие теряет вес. "
            "Гипотеза: нужно связать задачу с ближайшим полезным результатом и сделать маленький вход."
        )
    else:
        story = (
            f"Пользователь уточнил, что вход чаще ломает: {answer}. "
            "Это уже рабочий сигнал: сбой появляется до действия, а не после него. "
            "Гипотеза: нужно проверить маленький безопасный вход и записать эффект."
        )
    return clamp_str(f"{base}\n\n{story}" if base else story, 1500)


def _analysis_main_hypothesis(comp: Dict[str, Any]) -> str:
    analysis_result = comp.get("analysis_result") if isinstance(comp.get("analysis_result"), dict) else {}
    if analysis_result.get("core_hypothesis"):
        return str(analysis_result.get("core_hypothesis"))
    pattern = str(comp.get("live_pattern") or comp.get("main_pattern") or comp.get("specific_pattern") or "")
    if pattern == "perfectionism_visibility_fear" or "оцен" in pattern or "ошиб" in pattern:
        return "страх ошибки или оценки"
    if pattern == "attention_escape":
        return "уход внимания в быстрые награды"
    if pattern == "shame_self_attack":
        return "самокритика после срыва"
    if pattern == "low_energy_overload":
        return "низкий ресурс и перегруз"
    if pattern == "body_doubling_helpful":
        return "внешний контакт помогает запуску"
    return "вход в задачу становится слишком большим"


def _analysis_secondary_hypotheses(comp: Dict[str, Any]) -> List[str]:
    analysis_result = comp.get("analysis_result") if isinstance(comp.get("analysis_result"), dict) else {}
    if isinstance(analysis_result.get("secondary_hypotheses"), list) and analysis_result.get("secondary_hypotheses"):
        return [str(x) for x in analysis_result.get("secondary_hypotheses") if x][:6]
    main = _analysis_main_hypothesis(comp)
    checks = [
        "насколько помогает уменьшение шага",
        "есть ли проблемы с удержанием внимания после старта",
        "помогает ли присутствие других людей",
        "насколько сильно мешает самокритика после откладывания",
    ]
    if main != "страх ошибки или оценки":
        checks.insert(0, "есть ли страх ошибки или оценки")
    return checks[:5]


def working_map_profile_patch(comp: Dict[str, Any]) -> Dict[str, Any]:
    analysis_result = comp.get("analysis_result") if isinstance(comp.get("analysis_result"), dict) else {}
    signals = comp.get("analysis_signals") if isinstance(comp.get("analysis_signals"), dict) else {}
    facts = [str(x) for x in (analysis_result.get("evidence_signals") or signals.get("facts", [])) if x][:8]
    return {
        "main_hypothesis": _analysis_main_hypothesis(comp),
        "secondary_hypotheses": _analysis_secondary_hypotheses(comp),
        "confirmed_signals": facts,
        "recommended_core_skill": analysis_result.get("recommended_core_skill") or "",
        "recommended_variant": analysis_result.get("recommended_variant") or "",
        "failed_skills": [],
        "successful_skills": [],
    }


def preliminary_development_map_from_analysis(comp: Dict[str, Any]) -> str:
    """Build the preliminary development map shown after user confirms analysis."""
    comp = comp if isinstance(comp, dict) else {}
    assumptions: List[str] = []
    main = _analysis_main_hypothesis(comp)
    if main:
        assumptions.append(main)
    analysis_result = comp.get("analysis_result") if isinstance(comp.get("analysis_result"), dict) else {}
    for item in analysis_result.get("evidence_signals") or []:
        if item and len(assumptions) < 3:
            assumptions.append(str(item))
    if comp.get("avoidance_behavior") and len(assumptions) < 3:
        assumptions.append(str(comp.get("avoidance_behavior")))

    secondary_checks = _analysis_secondary_hypotheses(comp)
    # Keep the required product checks visible even when AI produced custom checks.
    required_checks = [
        "помогает ли уменьшение шага",
        "помогает ли плохой черновик",
        "помогает ли присутствие других людей",
    ]
    checks = list(required_checks)
    for item in secondary_checks:
        if item not in checks:
            checks.append(item)
    return preliminary_development_map_text(assumptions, checks)


def working_map_text(comp: Dict[str, Any], trainer_key: str) -> str:
    analysis_result = comp.get("analysis_result") if isinstance(comp.get("analysis_result"), dict) else {}
    maps = analysis_result.get("working_map_by_trainer") if isinstance(analysis_result.get("working_map_by_trainer"), dict) else {}
    scripted = maps.get(trainer_key) or maps.get("marsha")
    if scripted:
        return str(scripted)
    main = _analysis_main_hypothesis(comp)
    secondary = _analysis_secondary_hypotheses(comp)
    secondary_text = "\n".join(f"🔹 {item}" for item in secondary)
    if trainer_key == "skinny":
        checks = "\n".join(f"✔ {item}" for item in secondary[:4])
        return (
            "🗺 Что проверяем\n\n"
            f"Пока вижу:\n\n✔ {main}\n\n"
            f"Хочу проверить:\n\n{checks}\n\n"
            "Пока это гипотезы.\n\n"
            "Несколько дней собираем данные.\n\n"
            "Буду спрашивать:\n\n"
            "— что сработало\n"
            "— что не сработало\n"
            "— где развалилось\n\n"
            "Потом соберём нормальную карту."
        )
    if trainer_key == "beck":
        return (
            "🗺 Рабочая карта\n\n"
            "Пока я вижу несколько возможных узлов.\n\n"
            "Основная гипотеза:\n\n"
            f"🔹 {main}\n\n"
            "Дополнительно хочу проверить:\n\n"
            f"{secondary_text}\n\n"
            "Пока это не выводы.\n\n"
            "Это рабочая карта.\n\n"
            "Ближайшие дни мы будем смотреть:\n\n"
            "— какие навыки реально помогают\n"
            "— где становится легче\n"
            "— где всё ещё ломается вход\n"
            "— что работает именно у тебя\n\n"
            "Поэтому я буду иногда спрашивать:\n\n"
            "✔ что получилось\n"
            "✔ что не получилось\n"
            "✔ что было самым трудным\n"
            "✔ что неожиданно помогло\n\n"
            "Через несколько дней карта станет точнее."
        )
    return (
        "🗺 Предварительная карта\n\n"
        "Пока я вижу несколько мест,\n"
        "где тебе может быть особенно тяжело.\n\n"
        "Сейчас больше всего внимания привлекает:\n\n"
        f"🌱 {main}\n\n"
        "Но я пока не уверена,\n"
        "что это единственная причина.\n\n"
        "Поэтому ближайшие дни\n"
        "мы будем аккуратно смотреть:\n\n"
        "— какие шаги даются легче\n"
        "— где становится тяжело\n"
        "— что помогает возвращаться\n"
        "— как ты реагируешь на срывы\n\n"
        "Я буду иногда спрашивать:\n\n"
        "что получилось,\n"
        "что не получилось,\n"
        "что помогло,\n"
        "а что нет.\n\n"
        "Не для отчёта.\n\n"
        "А чтобы постепенно собрать карту,\n"
        "которая будет подходить именно тебе."
    )


def _append_unique_profile_value(current: Any, value: str, limit: int = 8) -> List[str]:
    items = current if isinstance(current, list) else []
    normalized = [str(x) for x in items if x]
    if value and value not in normalized:
        normalized.append(value)
    return normalized[-limit:]


async def record_working_map_skill_result(user_id: int, key: str, skill_id: Optional[str]):
    if not skill_id:
        return
    profile = await get_user_profile(user_id, DB_PATH)
    await update_user_profile(user_id, {key: _append_unique_profile_value(profile.get(key), str(skill_id))}, DB_PATH)


TRAINER_SWITCH_LIMIT = 2
TRAINER_SWITCH_STAGES = {
    "confirm_analysis",
    "analysis_details",
    "working_map",
    "analysis_rebuilt",
    "training",
    "waiting_next_day",
    "skill_card",
    "done",
    "profile_map",
    "day_menu",
}


def trainer_key_from_text(text: str) -> Optional[str]:
    low = (text or "").lower()
    if "скин" in low or "skinny" in low or "🐈‍⬛" in text:
        return "skinny"
    if "бек" in low or "beck" in low or "🐈‍🦁" in text or "🧠" in text:
        return "beck"
    if "марш" in low or "marsha" in low or "🐈" in text:
        return "marsha"
    return None


def trainer_mode_preview_text(current_key: str, switch_count: int, comp: Optional[Dict[str, Any]] = None) -> str:
    current = TRAINERS.get(current_key, TRAINERS["marsha"])
    mode_lines = (
        "🤍 Марша — мягко\n"
        "🐈‍⬛ Скинни — чётко\n"
        "🧠 Бек — с объяснениями"
    )
    snippets = ""
    if isinstance(comp, dict) and comp.get("analysis_result"):
        parts = []
        for key in ("skinny", "marsha", "beck"):
            trainer = TRAINERS.get(key, TRAINERS["marsha"])
            sample = format_comprehensive_analysis(comp, trainer_key=key)
            sample = clamp_str(" ".join(sample.split()), 170)
            parts.append(f"{trainer['emoji']} {trainer['name']}: {sample}")
        snippets = "\n\nКак будет звучать этот же разбор:\n" + "\n\n".join(parts)
    return (
        f"Твой текущий тренер: {current['emoji']} {current['name']}.\n"
        "Можно сменить стиль поддержки в любой момент.\n"
        "Задача, карта и прогресс сохранятся.\n\n"
        f"{mode_lines}"
        f"{snippets}\n\n"
        "Выбери режим. Смена попадёт в карту как факт выбора стиля, не как доказательство, что этот тренер помогает."
    )


def _trainer_switch_pending(u: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = json.loads(u.get("pending_plan_change") or "{}") if u.get("pending_plan_change") else {}
        return data if isinstance(data, dict) and data.get("type") == "trainer_switch" else {}
    except Exception:
        return {}


def trainer_switch_return_stage(u: Dict[str, Any]) -> str:
    pending = _trainer_switch_pending(u)
    stage = str(pending.get("return_stage") or "training")
    return stage if stage in TRAINER_SWITCH_STAGES else "training"


def trainer_switch_count(profile: Dict[str, Any]) -> int:
    try:
        return max(0, int(profile.get("trainer_switch_count") or 0))
    except (TypeError, ValueError):
        return 0


async def open_trainer_switch(m: Message, u: Dict[str, Any], source: str):
    profile = await get_user_profile(u["user_id"], DB_PATH)
    pending = {"type": "trainer_switch", "return_stage": u.get("stage") or "training", "source": source}
    u["pending_plan_change"] = json.dumps(pending, ensure_ascii=False)
    u["stage"] = "trainer_switch"
    await save_user(u, DB_PATH)
    await update_user_profile(u["user_id"], {
        "trainer_modes_viewed": True,
        "trainer_modes_view_count": int(profile.get("trainer_modes_view_count") or 0) + 1,
        "trainer_current_mode": u.get("trainer_key") or "marsha",
    }, DB_PATH)
    await log_event(
        u["user_id"],
        "trainer",
        "trainer_switch_opened",
        {"source": source, "trainer_key": u.get("trainer_key") or "marsha", "switch_count": trainer_switch_count(profile)},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    try:
        comp = json.loads(u.get("analysis_json") or "{}")
        if not isinstance(comp, dict):
            comp = {}
    except Exception:
        comp = {}
    await answer_with_keyboard(
        m,
        u,
        trainer_mode_preview_text(u.get("trainer_key") or "marsha", trainer_switch_count(profile), comp),
        kb_trainer_switch,
        "trainer_switch",
    )


def trainer_mode_changed_text(new_key: str, switch_count: int) -> str:
    if new_key == "marsha":
        return "🤍 Теперь с тобой Марша.\nБудем идти мягче: без давления, с опорой на возвращение после срывов."
    if new_key == "skinny":
        return "🐈‍⬛ Теперь с тобой Скинни.\nБудем идти короче и конкретнее: один шаг, одна проверка, без лишних переговоров."
    return "🧠 Теперь с тобой Бек.\nБудем смотреть на механизм: что запускает избегание и какой шаг его обходит."


def trainer_switch_open_day_text(new_key: str) -> str:
    trainer = TRAINERS.get(new_key, TRAINERS["marsha"])
    style_tail = {
        "marsha": "мягче",
        "skinny": "короче и конкретнее",
        "beck": "с объяснением механизма",
    }.get(new_key, "в новом стиле")
    return f"Теперь с тобой {trainer.get('display_name') or trainer.get('name')}. Текущий подход остаётся тем же, но дальше я буду вести {style_tail}."


def trainer_switch_closed_day_text(new_key: str) -> str:
    trainer = TRAINERS.get(new_key, TRAINERS["marsha"])
    return f"Стиль сохранён. Завтра тебя будет вести {trainer.get('display_name') or trainer.get('name')}."


def trainer_already_active_text(current_key: str) -> str:
    trainer = TRAINERS.get(current_key, TRAINERS["marsha"])
    return f"{trainer.get('display_name') or trainer.get('name')} уже активен. Оставляем текущий стиль."


async def return_after_trainer_switch(m: Message, u: Dict[str, Any], return_stage: str, switched: bool = False):
    u["stage"] = return_stage
    u["pending_plan_change"] = None
    await save_user(u, DB_PATH)
    trainer_key = u.get("trainer_key") or "marsha"
    if return_stage in {"confirm_analysis", "analysis_rebuilt"}:
        try:
            comp = json.loads(u.get("analysis_json") or "{}")
        except Exception:
            comp = {}
        msg = format_comprehensive_analysis(comp if isinstance(comp, dict) else {}, trainer_key=trainer_key)
        await answer_with_keyboard(m, u, msg + "\n\nЭто похоже на тебя?", kb_analysis_confirm, "analysis")
        return
    if return_stage == "analysis_details":
        try:
            comp = json.loads(u.get("analysis_json") or "{}")
        except Exception:
            comp = {}
        await answer_with_keyboard(m, u, render_analysis_details_by_trainer(comp if isinstance(comp, dict) else {}, trainer_key), kb_analysis_confirm, "analysis_details")
        return
    if return_stage == "working_map":
        try:
            comp = json.loads(u.get("analysis_json") or "{}")
        except Exception:
            comp = {}
        await answer_with_keyboard(m, u, working_map_text(comp if isinstance(comp, dict) else {}, trainer_key), kb_working_map, "working_map")
        return
    if return_stage == "skill_card":
        sid = current_skill_id(u) or "open_only"
        skill = dict(SKILLS_DB.get(sid) or SKILLS_DB.get("open_only") or next(iter(SKILLS_DB.values())))
        skill.setdefault("skill_id", sid)
        mark_action_card_active(u)
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, format_skill_card(u, skill, current_task_label(u)), action_keyboard(), "skill_card")
        return
    if return_stage == "done":
        await answer_with_keyboard(m, u, "Ок. Режим обновлён. Что дальше?", kb_done, "done")
        return
    if u.get("current_state") in ACTIVE_ACTION_STATES and return_stage in ACTION_RELATED_STAGES | {"training", "waiting_next_day"}:
        await answer_with_keyboard(m, u, "Ок. Тренер обновлён. Текущий подход на месте — можно отметить результат или сменить шаг.", action_keyboard(), "skill_card")
        return
    await answer_with_keyboard(m, u, "Ок. Режим обновлён. Возвращаемся к дню.", kb_training_main, "training_main")


async def handle_trainer_switch_choice(m: Message, u: Dict[str, Any], text: str):
    profile = await get_user_profile(u["user_id"], DB_PATH)
    return_stage = trainer_switch_return_stage(u)
    low = (text or "").lower().strip()
    if text in {"⬅️ Назад", "↩️ Оставить текущего тренера"} or low in {"назад", "оставить текущего тренера"}:
        await log_event(u["user_id"], "trainer", "trainer_switch_cancelled", {"return_stage": return_stage}, DB_PATH, SHEETS_WEBHOOK_URL)
        await return_after_trainer_switch(m, u, return_stage)
        return
    new_key = trainer_key_from_text(text)
    if not new_key:
        await answer_with_keyboard(m, u, trainer_mode_preview_text(u.get("trainer_key") or "marsha", trainer_switch_count(profile)), kb_trainer_switch, "trainer_switch")
        return
    old_key = u.get("trainer_key") or "marsha"
    if new_key == old_key:
        await log_event(u["user_id"], "trainer", "trainer_switch_same_selected", {"trainer_key": old_key, "return_stage": return_stage}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(trainer_already_active_text(old_key))
        await return_after_trainer_switch(m, u, return_stage)
        return
    count = trainer_switch_count(profile)
    history = _profile_list(profile.get("trainer_switch_history"))
    history.append(f"{old_key}->{new_key}@day{u.get('day') or 1}:{return_stage}")
    count += 1
    u["trainer_key"] = new_key
    await save_user(u, DB_PATH)
    await log_event(
        u["user_id"],
        "trainer",
        "trainer_switched",
        {"from_trainer": old_key, "to_trainer": new_key, "count": count, "return_stage": return_stage},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    updated_profile = await update_user_profile(u["user_id"], {
        "trainer_switch_count": count,
        "trainer_switch_history": history[-TRAINER_SWITCH_LIMIT:],
        "trainer_previous_mode": old_key,
        "trainer_current_mode": new_key,
        "trainer_fit_signal": f"selected_{new_key}",
    }, DB_PATH)
    u["profile_json"] = json.dumps(updated_profile, ensure_ascii=False)
    if day_closed_today(u, profile):
        u["stage"] = "waiting_next_day"
        u["pending_plan_change"] = None
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, trainer_switch_closed_day_text(new_key), kb_day_core_stop, "waiting_next_day")
        return
    await m.answer(trainer_switch_open_day_text(new_key))
    await return_after_trainer_switch(m, u, return_stage, switched=True)


def trainer_template(trainer_key: str, template_key: str) -> str:
    trainer = TRAINERS.get(trainer_key or "marsha", TRAINERS["marsha"])
    templates = trainer.get("response_templates") or {}
    return templates.get(template_key) or TRAINERS["marsha"].get("response_templates", {}).get(template_key) or "Смотрим, что происходит."


def trainer_style_line(trainer_key: str, scenario: str = "general") -> str:
    key = trainer_key if trainer_key in TRAINERS else "marsha"
    style = {
        "marsha": {
            "general": "Мягко: это не про оценку, а про следующий маленький шаг.",
            "stuck": "Мягко снизим стыд: застревание — данные, не провал.",
            "change": "Бережно сменим вход: усилие уже было, теперь подберём размер точнее.",
            "map": "Карта — без самокритики: смотрим, что помогает возвращаться.",
            "continue": "Можно продолжить маленько, без долга и без героизма.",
            "close": "Закрываем день спокойно: маленькое усилие уже считается.",
            "offer": "Полный режим — как поддержка без стыда, не как давление.",
            "curator": "С куратором можно идти мягче: меньше одиночества, больше опоры.",
        },
        "skinny": {
            "general": "Коротко: один шаг, один результат.",
            "stuck": "Фиксируем стопор. Уменьшаем шаг. Делаем один вход.",
            "change": "Навык не зашёл. Меняем вход. Без драматизации.",
            "map": "Карта: что работает, что нет, следующий тест.",
            "continue": "Если продолжаем — только один короткий подход.",
            "close": "День закрыт. Данные сохранены. Без добивания.",
            "offer": "Полный режим — структура, карта, следующий тест.",
            "curator": "Куратор — внешний контроль и короткий план.",
        },
        "beck": {
            "general": "Гипотеза: мысль → эмоция → избегание → последствия. Проверяем следующий маленький эксперимент.",
            "stuck": "Разберём механизм: какая мысль усилила эмоцию и какое избегание включилось.",
            "change": "Гипотеза обновлена: прежний вход не совпал с механизмом стопора.",
            "map": "Карта — это рабочая модель: паттерн, гипотеза, проверка, результат.",
            "continue": "Проверяем добровольный эксперимент: даст ли следующий шаг больше контроля.",
            "close": "Закрытие дня — фиксация данных: что сработало, где было избегание, что проверим дальше.",
            "offer": "Полный режим — больше данных для точной модели и проверки гипотез.",
            "curator": "Куратор помогает проверять гипотезы регулярнее и точнее.",
        },
    }[key]
    return style.get(scenario) or style["general"]


def trainer_wrap(u: Dict[str, Any], text: str, scenario: str = "general") -> str:
    return f"{trainer_style_line((u or {}).get('trainer_key') or 'marsha', scenario)}\n\n{text}"


def analysis_loading_text(trainer_key: str) -> str:
    return trainer_template(trainer_key, "check_barrier")


def should_show_today_progress(u: Dict[str, Any], profile: Dict[str, Any]) -> bool:
    done_today = int(u.get("day_core_round_count") or 0)
    if done_today < 2:
        return False
    today = local_date_for_user(u)
    shown = int(profile.get("daily_progress_shown_count") or 0) if profile.get("daily_progress_shown_date") == today else 0
    return shown < 2 and random.random() < 0.4


async def record_today_progress_shown(u: Dict[str, Any], profile: Dict[str, Any]):
    today = local_date_for_user(u)
    shown = int(profile.get("daily_progress_shown_count") or 0) if profile.get("daily_progress_shown_date") == today else 0
    await record_profile_signal(
        u["user_id"],
        "training",
        {"daily_progress_shown_date": today, "daily_progress_shown_count": shown + 1},
        source="daily_progress",
    )



def current_task_title(u: Dict[str, Any], fallback: str = "сегодняшняя задача") -> str:
    title = str(u.get("current_task_title") or u.get("today_target") or "").strip()
    if not title or title == "__target_not_selected__":
        return fallback
    return title


def current_task_label(u: Dict[str, Any]) -> str:
    return current_task_title(u, "сегодняшняя задача")


def task_needs_physical_step(title: str) -> bool:
    low = (title or "").lower().strip()
    if not low or low == "пропустить":
        return False
    action_verbs = ("открыть", "написать", "собрать", "проверить", "создать", "исправить", "прочитать", "отправить", "позвонить")
    if any(low.startswith(v) for v in action_verbs):
        return False
    return len(low.split()) <= 3


def returning_to_task_text(u: Dict[str, Any]) -> str:
    title = current_task_title(u, "задаче")
    step = str(u.get("current_next_physical_step") or "").strip()
    if step:
        return f"Возвращаемся к {title}.\nВ прошлый раз ты выбрал минимальный вход:\n“{step}”."
    return f"Возвращаемся к {title}."

def _metrics_from_profile(u: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    raw = profile.get("_action_metrics") if isinstance(profile, dict) else None
    if isinstance(raw, dict) and "today" in raw and "period" in raw:
        return raw
    return {
        "today": {
            "micro_approaches": int(profile.get("action_done_count_today") or 0),
            "slips": int(profile.get("slip_count_today") or 0),
            "returns_after_slip": int(profile.get("return_count_today") or 0),
            "step_reductions": int(profile.get("downscale_count_today") or 0),
        },
        "period": {
            "micro_approaches": int(profile.get("action_done_count") or u.get("done_count") or 0),
            "slips": int(profile.get("slip_count") or 0),
            "returns_after_slip": int(profile.get("return_count") or u.get("return_count") or 0),
            "step_reductions": int(profile.get("downscale_count") or 0),
        },
    }


def action_metrics_text(metrics: Dict[str, Dict[str, int]]) -> str:
    today = metrics.get("today", {})
    period = metrics.get("period", {})
    return (
        "Сегодня:\n"
        f"— микро-подходов: {int(today.get('micro_approaches') or 0)}\n"
        f"— отмеченных залипаний: {int(today.get('slips') or 0)}\n"
        f"— возвратов после залипания: {int(today.get('returns_after_slip') or 0)}\n"
        f"— упрощений шага: {int(today.get('step_reductions') or 0)}\n\n"
        "За период:\n"
        f"— микро-подходов: {int(period.get('micro_approaches') or 0)}\n"
        f"— отмеченных залипаний: {int(period.get('slips') or 0)}\n"
        f"— возвратов после залипания: {int(period.get('returns_after_slip') or 0)}\n"
        f"— упрощений шага: {int(period.get('step_reductions') or 0)}\n\n"
        "Это не оценка твоей продуктивности. Это отметки о том, как ты пробовал(а) входить в задачу."
    )

def done_flow_text(include_system_line: bool = False, u: Optional[Dict[str, Any]] = None, profile: Optional[Dict[str, Any]] = None) -> str:
    text = "Есть.\n\nМикро-подход засчитан."
    if u is not None:
        profile = profile or {}
        text += "\n\n" + action_metrics_text(_metrics_from_profile(u, profile))
    if include_system_line:
        text += "\n\nЭто данные."
    return text


def today_progress_text(u: Dict[str, Any], profile: Dict[str, Any]) -> str:
    return action_metrics_text(_metrics_from_profile(u, profile))


def day_finish_summary_text(u: Dict[str, Any], profile: Dict[str, Any]) -> str:
    metrics = _metrics_from_profile(u, profile)
    today = metrics.get("today", {})
    starts = int(today.get("micro_approaches") or 0)
    returns = int(today.get("returns_after_slip") or 0)
    downscales = int(today.get("step_reductions") or 0)
    slips = int(today.get("slips") or 0)

    if downscales <= 2:
        best_signal = "Пока есть 1–2 сигнала, что уменьшение шага может помогать. Нужно ещё несколько попыток, чтобы это проверить."
    elif downscales:
        best_signal = "Есть несколько отметок, что упрощение шага помогает входить в задачу мягче."
    elif returns:
        best_signal = "Возврат после залипания важнее идеального выполнения без сбоев."
    elif slips:
        best_signal = "Залипание отмечено как место, где нужен маршрут возврата, а не самокритика."
    else:
        best_signal = "Данных пока немного: сегодняшние отметки только начинают уточнять карту."

    return (
        "🌙 День закрыт.\n\n"
        f"{action_metrics_text(metrics)}\n\n"
        "Самый полезный сигнал:\n"
        f"{best_signal}\n\n"
        "До завтра. Новый навык откроется после смены календарного дня."
    )


def _today_profile_counter_patch(profile: Dict[str, Any], counter_key: str, date_key: str) -> Dict[str, Any]:
    today = _today_iso()
    current = int(profile.get(counter_key) or 0) if profile.get(date_key) == today else 0
    return {date_key: today, counter_key: current + 1}



def mark_pending_return_after_disruption(u: Dict[str, Any], reason: str):
    u["pending_return_after_disruption"] = 1
    u["pending_return_reason"] = reason
    u["pending_return_date"] = local_date_for_user(u)


def clear_pending_return_after_disruption(u: Dict[str, Any]):
    u["pending_return_after_disruption"] = 0
    u["pending_return_reason"] = None
    u["pending_return_date"] = None


def has_pending_return_after_disruption(u: Dict[str, Any]) -> bool:
    return (
        int(u.get("pending_return_after_disruption") or 0) == 1
        and (u.get("pending_return_date") or local_date_for_user(u)) == local_date_for_user(u)
    )


def return_after_stuck_text() -> str:
    return (
        "Это важный возврат.\n\n"
        "Ты не “идеально поработал”.\n"
        "Но ты вышел из залипания и сделал маленькое действие.\n\n"
        "Записываю:\n"
        "↩️ возврат после залипания +1"
    )


async def record_return_after_disruption_if_needed(u: Dict[str, Any], profile: Dict[str, Any], source: str) -> bool:
    if not has_pending_return_after_disruption(u):
        return False
    reason = u.get("pending_return_reason") or "disruption"
    u["return_count"] = int(u.get("return_count") or profile.get("return_count") or 0) + 1
    clear_pending_return_after_disruption(u)
    return_pattern = "return_after_stuck" if reason == "stuck_phone" else "return_after_disruption"
    await record_profile_signal(u["user_id"], "training", {
        "return_pattern": return_pattern,
        "slip_pattern": return_pattern,
        "last_return_reason": reason,
        "return_count": int(u.get("return_count") or 0),
        **_today_profile_counter_patch(profile, "return_count_today", "return_count_date"),
    }, source=source)
    await record_development_avatar_event(u["user_id"], "return_after_slip", DB_PATH, {
        "return_count": int(u.get("return_count") or 0),
        "reason": reason,
        "source": source,
    })
    return True


async def record_return_after_stuck_if_needed(u: Dict[str, Any], profile: Dict[str, Any], source: str) -> bool:
    reason = u.get("pending_return_reason") or ""
    pattern = u.get("pending_crisis_pattern") or profile.get("last_crisis_pattern") or profile.get("crisis_pattern") or ""
    if u.get("last_event") != "stuck" and reason != "stuck_phone" and pattern != "attention_escape":
        return False
    returns_after_stuck = int(profile.get("returns_after_stuck") or u.get("returns_after_stuck") or 0) + 1
    today_returns = int(profile.get("today_returns_after_stuck") or u.get("today_returns_after_stuck") or 0) + 1
    u["returns_after_stuck"] = returns_after_stuck
    u["today_returns_after_stuck"] = today_returns
    u["return_count"] = int(u.get("return_count") or profile.get("return_count") or 0) + 1
    u["last_event"] = "return_after_stuck"
    clear_pending_return_after_disruption(u)
    await record_profile_signal(
        u["user_id"],
        "crisis",
        {
            "return_pattern": "return_after_stuck",
            "slip_pattern": "return_after_stuck",
            "last_return_reason": "stuck_phone",
            "return_count": int(u.get("return_count") or 0),
            "returns_after_stuck": returns_after_stuck,
            "today_returns_after_stuck": today_returns,
            **_today_profile_counter_patch(profile, "return_count_today", "return_count_date"),
        },
        source=source,
    )
    await record_development_avatar_event(
        u["user_id"],
        "return_after_stuck",
        DB_PATH,
        {"returns_after_stuck": returns_after_stuck, "today_returns_after_stuck": today_returns, "source": source},
    )
    return True




def crisis_pattern_from_text(text: str) -> str:
    low = (text or "").lower().strip()
    if any(x in low for x in ("навредить себе", "не уверен, что остановлюсь", "лучше бы меня не было", "есть план", "покончить", "суицид", "самоуб")):
        return "high_risk"
    if any(x in low for x in ("меня не понимают", "я один", "я одна", "никому не нужен", "никому не нужна", "кому написать", "меня бросят", "один дома")):
        return "social_pain"
    if text.startswith("1") or "не могу начать" in low or "не могу заставить" in low or "смотрю на задачу" in low or "откладываю старт" in low or "начать" in low:
        return "task_entry_block"
    if text.startswith("2") or any(x in low for x in ("залип", "ютуб", "youtube", "tiktok", "тик ток", "telegram", "телеграм", "соцсет", "соцсети", "отвлёк", "отвлек", "вкладк", "видео", "лента", "скрол", "телефон")):
        return "attention_escape"
    if text.startswith("3") or any(x in low for x in ("идеаль", "ошиб", "боюсь", "позор", "стыд", "плохо получится", "публикац", "опубли", "оценят", "критик")):
        return "perfectionism"
    if text.startswith("4") or any(x in low for x in ("слишком много", "перегруз", "не знаю за что", "всё сразу", "все сразу", "слишком больш", "огром")):
        return "overwhelm"
    if text.startswith("5") or any(x in low for x in ("устал", "нет сил", "выгор", "пустой", "не вывожу", "нет ресурса")):
        return "low_energy"
    if text.startswith("6") or any(x in low for x in ("я ленив", "я безволь", "опять всё испортил", "опять все испортил", "неудачник", "сам себя", "самокрит", "сжира")):
        return "self_attack"
    if text.startswith("7") or any(x in low for x in ("тревог", "вдруг", "а если", "пережива", "накруч")):
        return "anxiety_loop"
    return "unknown"


CRISIS_BUTTON_PATTERNS = {
    "не могу начать": "task_entry_block",
    "залип": "attention_escape",
    "боюсь ошибки": "perfectionism",
    "всё слишком большое": "overwhelm",
    "все слишком большое": "overwhelm",
    "нет сил": "low_energy",
    "сам себя сжираю": "self_attack",
    "тревога": "anxiety_loop",
    "другое": "unknown",
}


def _selected_crisis_patterns(u: Dict[str, Any]) -> List[str]:
    temp = u.get("temp") if isinstance(u.get("temp"), dict) else {}
    selected = temp.get("selected_blockers") if isinstance(temp, dict) else None
    if isinstance(selected, list):
        return [str(x) for x in selected if x]
    try:
        data = json.loads(u.get("pending_plan_change") or "{}")
    except Exception:
        data = {}
    if not isinstance(data, dict) or data.get("type") != "crisis_multiselect":
        return []
    return [str(x) for x in data.get("selected_blockers") or data.get("patterns") or [] if x]


def _save_selected_crisis_patterns(u: Dict[str, Any], patterns: List[str]) -> None:
    temp = u.get("temp") if isinstance(u.get("temp"), dict) else {}
    temp["selected_blockers"] = patterns
    u["temp"] = temp
    u["pending_plan_change"] = json.dumps({"type": "crisis_multiselect", "selected_blockers": patterns}, ensure_ascii=False)


def _crisis_pattern_from_button(text: str) -> Optional[str]:
    low = (text or "").lower().replace("⬜", "").replace("✅", "").strip()
    # Telegram keycaps like "1️⃣" contain variation/combine marks; strip a leading label safely.
    low = re.sub(r"^[^а-яa-z]+", "", low).strip()
    return CRISIS_BUTTON_PATTERNS.get(low)


def crisis_multiselect_keyboard(selected: List[str]) -> ReplyKeyboardMarkup:
    labels = [
        ("task_entry_block", "Не могу начать"),
        ("attention_escape", "Залип"),
        ("perfectionism", "Боюсь ошибки"),
        ("overwhelm", "Всё слишком большое"),
        ("low_energy", "Нет сил"),
        ("self_attack", "Сам себя сжираю"),
        ("anxiety_loop", "Тревога"),
        ("unknown", "Другое"),
    ]
    buttons = []
    for code, label in labels:
        mark = "✅" if code in selected else "⬜"
        buttons.append(KeyboardButton(text=f"{mark} {label}"))
    return ReplyKeyboardMarkup(keyboard=[buttons[0:2], buttons[2:4], buttons[4:6], buttons[6:8], [KeyboardButton(text="✅ Всё выбрал")]], resize_keyboard=True)


def combined_crisis_tool_text(patterns: List[str]) -> str:
    patterns = list(dict.fromkeys(patterns or []))
    if "high_risk" in patterns:
        return crisis_tool_text("high_risk")
    if {"attention_escape", "anxiety_loop", "perfectionism"}.issubset(set(patterns)):
        return (
            "Вижу связку: тревога → страх ошибки → уход в залипание.\n\n"
            "Значит, сначала не давим на продуктивность.\n\n"
            "Порядок такой:\n"
            "1. Снизить тревогу телом.\n"
            "2. Убрать источник залипания.\n"
            "3. Сделать безопасный черновой шаг.\n\n"
            "Стек на 3–5 минут:\n"
            "1. Поставь ноги на пол и сделай 3 длинных выдоха.\n"
            "2. Закрой/сверни источник залипания или положи телефон дальше руки.\n"
            "3. Открой документ/задачу и напиши заголовок: “Плохой черновик”.\n"
            "4. Напиши одно плохое предложение или 3 сырых слова без редактуры.\n\n"
            "Минимум: один длинный выдох + закрыть источник залипания + открыть черновик."
        )
    parts = [crisis_tool_text(p) for p in patterns[:3] if p != "unknown"]
    return "\n\n———\n\n".join(parts) if parts else crisis_tool_text("unknown")


crisis_tool_reason_from_text = crisis_pattern_from_text

CRISIS_STACK_TO_PATTERN = {
    "ZALIP": "attention_escape",
    "ANXIETY": "anxiety_loop",
    "DEPRESSIVE_LOW_ENERGY": "low_energy",
    "SHAME_SELF_ATTACK": "self_attack",
    "NOT_UNDERSTOOD": "social_pain",
    "HIGH_RISK": "high_risk",
    "UNKNOWN": "unknown",
}

CRISIS_PATTERN_TO_STACK = {v: k for k, v in CRISIS_STACK_TO_PATTERN.items()}

CRISIS_STACK_KEYWORDS = {
    "HIGH_RISK": (
        "лучше бы меня не было", "могу навредить себе", "навредить себе",
        "не уверен, что остановлюсь", "один дома", "одна дома", "есть план",
        "не хочу никому звонить", "не хочу жить", "самоуб", "суицид",
    ),
    "ZALIP": (
        "залип", "ютуб", "youtube", "телеграм", "telegram", "соцсети",
        "соцсет", "порно", "новости", "не могу оторваться", "скроллю", "скрол",
    ),
    "ANXIETY": (
        "тревожно", "тревога", "паника", "напряжение в груди", "страшно",
        "не могу дышать", "накрывает",
    ),
    "DEPRESSIVE_LOW_ENERGY": (
        "нет сил", "не могу ничего", "лежу", "бессмысленно", "не вижу выхода",
        "ничего не хочу",
    ),
    "SHAME_SELF_ATTACK": (
        "я ничтожество", "я слабый", "я слабая", "все увидят", "облажался",
        "облажалась", "сам себя ненавижу", "стыдно", "стыд",
    ),
    "NOT_UNDERSTOOD": (
        "меня не понимают", "я один", "я одна", "никому не нужен",
        "никому не нужна", "не знаю кому написать", "меня бросят",
    ),
}

def detect_crisis_stack(text: str, selected_buttons: Optional[List[str]] = None) -> str:
    """Detect the crisis stack from free text and/or selected crisis buttons."""
    selected_buttons = selected_buttons or []
    selected_stacks = []
    for item in selected_buttons:
        value = str(item or "").strip()
        if not value:
            continue
        if value in CRISIS_STACK_TO_PATTERN:
            selected_stacks.append(value)
            continue
        if value in CRISIS_PATTERN_TO_STACK:
            selected_stacks.append(CRISIS_PATTERN_TO_STACK[value])
            continue
        button_pattern = _crisis_pattern_from_button(value)
        if button_pattern and button_pattern in CRISIS_PATTERN_TO_STACK:
            selected_stacks.append(CRISIS_PATTERN_TO_STACK[button_pattern])

    # Safety always wins over every other stack.
    if "HIGH_RISK" in selected_stacks:
        return "HIGH_RISK"

    low = (text or "").lower().strip()
    if crisis_safety_check(low).get("high_risk"):
        return "HIGH_RISK"
    for stack in ("HIGH_RISK", "ZALIP", "ANXIETY", "DEPRESSIVE_LOW_ENERGY", "SHAME_SELF_ATTACK", "NOT_UNDERSTOOD"):
        if any(marker in low for marker in CRISIS_STACK_KEYWORDS[stack]):
            return stack

    for stack in selected_stacks:
        if stack != "UNKNOWN":
            return stack
    return "UNKNOWN"

def crisis_pattern_for_stack(stack: str) -> str:
    return CRISIS_STACK_TO_PATTERN.get(stack, "unknown")


def crisis_safety_check(reason_text: str) -> Dict[str, Any]:
    """Fast pre-check before any CBT/productivity crisis tool selection."""
    low = (reason_text or "").lower()
    self_harm = any(x in low for x in (
        "навредить себе", "себе навред", "убить себя", "покончить", "суицид",
        "самоуб", "порез", "вскрыть", "выпить таблетки", "лучше бы меня не было",
        "не хочу жить", "не могу остановиться", "не уверен, что остановлюсь",
    ))
    near_minutes = any(x in low for x in ("сейчас", "прямо сейчас", "в ближайшие минуты", "сегодня", "уже", "скоро"))
    means_plan_intent = any(x in low for x in (
        "есть план", "план", "нож", "лезв", "таблет", "верев", "пистолет",
        "балкон", "окно", "мост", "средства", "предмет", "намерен", "намерение",
    ))
    alone = any(x in low for x in ("я один", "я одна", "один дома", "одна дома", "никого рядом"))
    high = self_harm and (near_minutes or means_plan_intent or alone)
    return {
        "self_harm": self_harm,
        "alone": alone,
        "near_minutes": near_minutes,
        "means_plan_intent": means_plan_intent,
        "high_risk": high,
    }


def crisis_skill_for_pattern(pattern: str) -> str:
    try:
        return crisis_skill_title(pattern)
    except Exception:
        return {
            "attention_escape": "Возврат из залипания",
            "task_entry_block": "Аварийный запуск",
            "perfectionism": "Плохой черновик",
            "overwhelm": "Резак задачи",
            "low_energy": "Минимально жизнеспособный день",
            "self_attack": "Факт вместо приговора",
            "anxiety_loop": "Только следующий шаг",
        }.get(pattern, "Один управляемый шаг")


def crisis_paid_unlimited(u: Dict[str, Any]) -> bool:
    # Crisis support must remain available in every mode, including free mode.
    return True


def _crisis_tool_count_today(profile: Dict[str, Any], u: Dict[str, Any]) -> int:
    if profile.get("crisis_tool_date") != local_date_for_user(u):
        return 0
    return max(0, int(profile.get("crisis_tool_count_today") or 0))


def _crisis_pattern_counts(profile: Dict[str, Any]) -> Dict[str, int]:
    raw = profile.get("crisis_pattern_counts") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    return {str(k): int(v or 0) for k, v in (raw or {}).items()}


def _crisis_skill_counts(profile: Dict[str, Any]) -> Dict[str, int]:
    raw = profile.get("crisis_skill_success_counts") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    return {str(k): int(v or 0) for k, v in (raw or {}).items()}


def _top_key(counts: Dict[str, int]) -> str:
    if not counts:
        return ""
    return max(counts.items(), key=lambda kv: kv[1])[0]


def crisis_effect_code(text: str) -> str:
    low = (text or "").lower().strip()
    if "хуже" in low or "стало плохо" in low or "небезопас" in low:
        return "worse"
    if "👍" in text or low in {"да", "yes", "y"} or "легче" in low:
        return "better"
    if "👎" in text or low in {"нет", "no", "n"}:
        return "no"
    return "same"


async def classify_crisis_pattern(reason_text: str) -> str:
    allowed = {"attention_escape", "task_entry_block", "perfectionism", "overwhelm", "low_energy", "self_attack", "anxiety_loop", "social_pain", "high_risk", "unknown"}
    if (reason_text or "").strip() in allowed:
        return (reason_text or "").strip()
    fallback = crisis_pattern_from_text(reason_text)
    if not AI_ANALYSIS_ENABLED or client is None or not reason_text:
        return fallback
    try:
        prompt = (
            "Classify this crisis message into exactly one code: "
            "attention_escape, task_entry_block, perfectionism, overwhelm, low_energy, self_attack, anxiety_loop, social_pain, high_risk, unknown. "
            "Return only the code. Do not quote the user.\n\n"
            f"Message: {reason_text[:500]}"
        )
        resp = client.chat.completions.create(
            model=OPENAI_CHAT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=12,
        )
        code = (resp.choices[0].message.content or "").strip().lower()
        return code if code in allowed else fallback
    except Exception as e:
        log.warning("crisis_pattern_ai_failed: %s", e)
        return fallback


async def show_crisis_entry(m: Message, u: Dict[str, Any], source: str):
    u["stage"] = CRISIS_WAITING_INPUT
    set_current_state(u, STATE_TASK_CRISIS_INPUT, close_action=True)
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "crisis", "crisis_entry_shown", {"source": source}, DB_PATH, SHEETS_WEBHOOK_URL)
    await answer_with_keyboard(m, u, crisis_entry_text(), kb_crisis_mode, "crisis_mode")


async def show_crisis_tool_prompt(m: Message, u: Dict[str, Any]):
    u["stage"] = "crisis_tool_select"
    set_current_state(u, STATE_TASK_CRISIS_INPUT, close_action=True)
    _save_selected_crisis_patterns(u, _selected_crisis_patterns(u))
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "crisis", "crisis_tool_prompt_shown", {}, DB_PATH, SHEETS_WEBHOOK_URL)
    await m.answer(crisis_tool_prompt_text(), reply_markup=crisis_multiselect_keyboard(_selected_crisis_patterns(u)))


async def send_crisis_tool(m: Message, u: Dict[str, Any], reason_text: str):
    profile = await get_user_profile(u["user_id"], DB_PATH)
    count = _crisis_tool_count_today(profile, u)
    if not crisis_paid_unlimited(u) and count >= MAX_CRISIS_MATCHES_PER_DAY:
        u["stage"] = "waiting_next_day"
        await save_user(u, DB_PATH)
        await log_event(u["user_id"], "crisis", "crisis_tool_limit_reached", {"count": count}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(m, u, crisis_tool_limit_text(), kb_training_main, "training_main")
        return
    safety = crisis_safety_check(reason_text)
    crisis_stack = detect_crisis_stack(reason_text, [])
    pattern = crisis_pattern_for_stack(crisis_stack)
    if pattern == "unknown":
        pattern = "high_risk" if safety.get("high_risk") else await classify_crisis_pattern(reason_text)
        crisis_stack = CRISIS_PATTERN_TO_STACK.get(pattern, "UNKNOWN")
    skill = crisis_skill_for_pattern(pattern)
    today = local_date_for_user(u)
    pattern_counts = _crisis_pattern_counts(profile)
    pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
    crisis_count = int(profile.get("crisis_count") or u.get("crisis_count") or 0) + 1
    patch = {
        "crisis_tool_date": today,
        "crisis_tool_count_today": count + 1,
        "last_crisis_tool_reason": pattern,
        "crisis_pattern": pattern,
        "crisis_stack": crisis_stack,
        "crisis_skill": skill,
        "last_crisis_pattern": pattern,
        "last_crisis_skill": skill,
        "most_common_crisis_pattern": _top_key(pattern_counts),
        "crisis_pattern_counts": pattern_counts,
        "crisis_count": crisis_count,
    }
    await record_profile_signal(u["user_id"], "crisis", patch, source="crisis_tool")
    await log_event(u["user_id"], "crisis", "crisis_tool_selected", {"crisis_pattern": pattern, "crisis_stack": crisis_stack, "crisis_skill": skill, "count": count + 1, "safety": safety}, DB_PATH, SHEETS_WEBHOOK_URL)
    u["stage"] = "crisis_effect_await"
    u["crisis_count"] = crisis_count
    u["pending_crisis_pattern"] = pattern
    u["pending_crisis_skill"] = skill
    set_last_explanation_context(
        u,
        "crisis",
        skill,
        "Я сопоставил текст/выбор с кризисным стеком и сначала отсекаю высокий риск, а уже потом даю продуктивный шаг.",
        [f"распознанный паттерн: {pattern}", "кризисный режим не возвращает в тренировку, пока не станет безопаснее"],
        "Сделай минимум из блока и отметь, стало ли легче хотя бы на 5%.",
    )
    await save_user(u, DB_PATH)
    if pattern == "high_risk":
        await m.answer(crisis_tool_text(crisis_stack))
        await start_safety_interceptor(m, u, reason_text, "crisis_tool_high_risk", explicit=True)
        return
    else:
        await m.answer(crisis_tool_text(crisis_stack))
    u["stage"] = "crisis_action_await"
    await save_user(u, DB_PATH)
    await answer_with_keyboard(m, u, "Сделай минимум из блока и отметь, что получилось.", kb_crisis_action, "crisis_action")

async def send_crisis_stabilize(m: Message, u: Dict[str, Any], source: str):
    u["stage"] = "crisis_stabilize"
    set_current_state(u, STATE_TASK_CRISIS_INPUT, close_action=True)
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], u["stage"], "crisis_open", {"source": source}, DB_PATH, SHEETS_WEBHOOK_URL)
    await answer_with_keyboard(m, u, crisis_stabilize_text(), kb_crisis_stabilize, "crisis_stabilize")


def _remember_downscale_pattern(u: Dict[str, Any], skill_id: str):
    """Сохранить локальную адаптацию без запуска повторной карты."""
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
    u["current_skill_variant_id"] = skill_id
    u["analysis_json"] = json.dumps(data, ensure_ascii=False)


def _select_downscale_skill(u: Dict[str, Any]) -> str:
    """Выбрать и поставить текущий навык downscale на сегодняшний день."""
    current_core_id = str(u.get("current_core_skill_id") or "") if u.get("current_core_skill_date") == local_date_for_user(u) else ""
    variants = [sid for sid in variants_for_core_skill(current_core_id) if sid in SKILLS_DB]
    skill_id = variants[1] if len(variants) > 1 else (DOWNSCALE_PRIMARY_SKILL if DOWNSCALE_PRIMARY_SKILL in SKILLS_DB else DOWNSCALE_FALLBACK_SKILL)
    # Downscale is an in-day version, not a replacement of today's core skill.
    # Explicit replacement is handled by the "Заменить навык" branch.
    u["pending_skill_id"] = None
    u["pending_skill_day"] = None
    _remember_downscale_pattern(u, skill_id)
    return skill_id


async def answer_with_keyboard(m: Message, u: Dict[str, Any], text: str, reply_markup, keyboard_name: str):
    """Send a keyboard only if it respects the reply-keyboard button limit and log it."""
    button_count = keyboard_button_count(reply_markup)
    event_name = "keyboard_shown" if button_count <= MAX_KEYBOARD_BUTTONS else "keyboard_warning"
    await log_event(
        u.get("user_id"),
        u.get("stage", ""),
        event_name,
        {"keyboard": keyboard_name, "button_count": button_count},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    try:
        if button_count > MAX_KEYBOARD_BUTTONS:
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



CONTEXT_FALLBACK_TEXT = (
    "Старый экран уже не актуален.\n"
    "Покажу следующий доступный вариант или закроем день без повтора.\n\n"
    "Что сейчас нужнее?"
)

kb_context_fallback = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💪 Сделать следующий шаг")],
        [KeyboardButton(text="⚡ Я застрял"), KeyboardButton(text="🧭 Моя карта")],
        [KeyboardButton(text="🌙 Закрыть день")],
    ],
    resize_keyboard=True,
)


def _reply_keyboard_texts(markup) -> set[str]:
    rows = getattr(markup, "keyboard", None) or []
    return {str(getattr(button, "text", "")) for row in rows for button in row if getattr(button, "text", "")}


def known_reply_button_texts() -> set[str]:
    keyboard_names = (
        "kb_training_main", "kb_more_actions", "kb_skill_card", "kb_new_day_skill", "kb_done", "kb_active_skill",
        "kb_day_core_stop", "kb_failed", "kb_action_clarify", "kb_downscale", "kb_skill_result_feedback", "kb_skill_result_feedback",
        "kb_downscale_name_task", "kb_microstep", "kb_skeptic", "kb_crisis_mode",
        "kb_crisis_stabilize", "kb_crisis_tool_select", "kb_crisis_effect", "kb_short_mode_main",
        "kb_day_menu", "kb_day_pause_confirm", "kb_skill_change_reason", "kb_skill_change_meaning", "kb_map_with_trainer", "kb_trainers", "kb_input_mode", "kb_yes_no",
        "kb_stuck_validation_meaning", "kb_stuck_validation_self_attack", "kb_stuck_validation_file_fear", "kb_stuck_validation_safety",
        "kb_working_map", "kb_analysis_confirm", "kb_social_support", "kb_notifications_consent",
    )
    texts = set()
    for name in keyboard_names:
        markup = globals().get(name)
        if markup is not None:
            texts.update(_reply_keyboard_texts(markup))
    texts.update({
        "💪 Дать следующий шаг", "⚡ Я застрял", "🌙 Закрыть день",
        "📱 Залип", "😣 Слишком сложно", "😵 Нет сил", "❌ Не сделал",
        "🔁 Другой навык", "🧩 Уменьшить шаг", "🔁 Ещё круг", "📌 Что изменилось?",
        "📱 Ушёл в телефон / YouTube", "📱 Ушёл в телефон", "😬 Страшно, стыдно, боюсь ошибиться", "😬 Страх ошибки / оценки",
        "🧠 Слишком много всего", "🌀 Слишком много вариантов", "😶 Не понимаю, с чего начать", "🔋 Нет сил", "😵 Слишком тяжело", "🫨 Тревога и перегруз", "🧨 Самокритика после срыва", "🎙️ Опишу голосом или текстом",
        "➕ Ещё 2 минуты", "💪 Продолжить тренировку", "🌙 На сегодня достаточно", "🌙 Закрыть подход", "🔄 Сменить навык", "🗣️ Что помогло?", "💪 Другое действие",
        "💪 Сделать следующий шаг", "🆘 Кризис прокрастинации", "🎭 Сменить тренера", "🔄 Сменить тренера", "Ещё", "Еще",
    })
    return texts


def is_known_reply_button(text: str) -> bool:
    return (text or "").strip() in known_reply_button_texts()


def button_fits_current_state(text: str, u: Dict[str, Any]) -> bool:
    stage = u.get("stage") or ""
    by_stage = {
        "ask_name": {"Пропустить"},
        "await_trainer": _reply_keyboard_texts(globals().get("kb_trainers")),
        "notification_consent": _reply_keyboard_texts(globals().get("kb_notifications_consent")),
        "trainer_intro": _reply_keyboard_texts(globals().get("kb_yes_no")),
        "await_input_mode": _reply_keyboard_texts(globals().get("kb_input_mode")),
        "choose_input_mode": _reply_keyboard_texts(globals().get("kb_input_mode")),
        "await_problem_text": {"Пропустить"},
        "await_problem_voice": {"Назад"},
        "taking_test": set(),  # inline callbacks, not reply-keyboard text
        "working_map": _reply_keyboard_texts(globals().get("kb_working_map")),
        "confirm_analysis": _reply_keyboard_texts(globals().get("kb_analysis_confirm")),
        "analysis_details": _reply_keyboard_texts(globals().get("kb_analysis_confirm")),
        "day_menu": _reply_keyboard_texts(globals().get("kb_day_menu")),
        "day_pause_confirm": _reply_keyboard_texts(globals().get("kb_day_pause_confirm")),
        "done": _reply_keyboard_texts(globals().get("kb_done")),
        "day_core_stop": _reply_keyboard_texts(globals().get("kb_day_core_stop")),
        "trainer_switch": _reply_keyboard_texts(globals().get("kb_trainer_switch")),
        "crisis_mode": _reply_keyboard_texts(globals().get("kb_crisis_mode")),
        "crisis_action": _reply_keyboard_texts(globals().get("kb_crisis_action")),
        "success_menu": _reply_keyboard_texts(globals().get("kb_success_next")) | _reply_keyboard_texts(globals().get("kb_success_limit")),
        "success_help_note": _reply_keyboard_texts(globals().get("kb_success_next")) | _reply_keyboard_texts(globals().get("kb_success_limit")),
        "failed_options": _reply_keyboard_texts(globals().get("kb_failed")) | {"📱 Залип", "😣 Слишком сложно", "😵 Нет сил"},
        "stuck_validation_choice": _reply_keyboard_texts(globals().get("kb_stuck_validation_meaning"))
        | _reply_keyboard_texts(globals().get("kb_stuck_validation_self_attack"))
        | _reply_keyboard_texts(globals().get("kb_stuck_validation_file_fear"))
        | _reply_keyboard_texts(globals().get("kb_stuck_validation_safety")),
    }
    if stage in ACTION_RELATED_STAGES or stage in {"waiting_next_day", "training", "skill_card"}:
        allowed = set()
        for name in ("kb_training_main", "kb_more_actions", "kb_skill_card", "kb_new_day_skill", "kb_active_skill", "kb_failed", "kb_downscale", "kb_downscale_name_task", "kb_microstep", "kb_skeptic", "kb_done"):
            allowed.update(_reply_keyboard_texts(globals().get(name)))
        allowed.update({"📱 Залип", "😣 Слишком сложно", "😵 Нет сил"})
        return text in allowed
    allowed = by_stage.get(stage)
    return allowed is None or text in allowed


async def show_context_fallback(m: Message, u: Dict[str, Any], source: str = "stale_button"):
    # Deliberately do not log metrics, open skills, or mutate progress counters here.
    u["stage"] = "waiting_next_day"
    await save_user(u, DB_PATH)
    await m.answer(CONTEXT_FALLBACK_TEXT, reply_markup=kb_context_fallback)


STATE_ONBOARDING = "ONBOARDING"
STATE_ACTION_ACTIVE = "ACTION_ACTIVE"
STATE_AWAITING_RESULT = "AWAITING_RESULT"
STATE_AWAITING_STUCK_REASON = "AWAITING_STUCK_REASON"
STATE_PAUSED = "PAUSED"
STATE_DAY_CLOSED = "DAY_CLOSED"
STATE_TASK_CRISIS_INPUT = "TASK_CRISIS_INPUT"
STATE_SAFETY_LOCK = "SAFETY_LOCK"
STATE_OFFER_SCREEN = "OFFER_SCREEN"

ACTIVE_ACTION_STATES = {STATE_ACTION_ACTIVE, STATE_AWAITING_RESULT, STATE_AWAITING_STUCK_REASON}
ACTION_OUTCOME_BUTTONS = {"✅ Сделал", "✅ Сделал(а)", "🟡 Застрял / не вышло", "🟡 Не вышло", "📱 Залип", "😣 Слишком сложно", "😵 Нет сил", "❌ Не сделал", "⏸ Пауза"}
POST_MINIMUM_CONTINUE_TEXT = (
    "Минимум на сегодня уже выполнен. Это успех.\n\n"
    "Можно остановиться и спокойно закрыть день.\n"
    "Если хочешь, можем сделать ещё один короткий шаг без обязательства."
)

STALE_ACTION_CHANGED_TEXT = POST_MINIMUM_CONTINUE_TEXT

def bump_state_version(u: Dict[str, Any]) -> int:
    try:
        version = int(u.get("state_version") or 0) + 1
    except (TypeError, ValueError):
        version = 1
    u["state_version"] = version
    return version


def set_current_state(u: Dict[str, Any], state: str, *, new_action: bool = False, close_action: bool = False) -> str:
    previous = u.get("current_state")
    if previous != state or new_action or close_action:
        bump_state_version(u)
    u["current_state"] = state
    if new_action:
        u["current_action_id"] = f"act_{uuid.uuid4().hex[:12]}"
    elif close_action:
        u["current_action_id"] = None
    return str(u.get("current_action_id") or "")


def mark_action_card_active(u: Dict[str, Any]) -> str:
    u["daily_skill_status"] = "in_progress"
    return set_current_state(u, STATE_AWAITING_RESULT, new_action=True)


def mark_current_skill_status(u: Dict[str, Any], status: str) -> None:
    if status in {"not_started", "in_progress", "completed", "stuck", "replaced", "closed"}:
        u["daily_skill_status"] = status


def current_skill_completed_or_closed(u: Dict[str, Any]) -> bool:
    return str(u.get("daily_skill_status") or "") in {"completed", "closed"}


def current_day_matches_active_action(u: Dict[str, Any]) -> bool:
    return bool(u.get("current_day_id"))


def is_previous_scenario_finished(u: Dict[str, Any]) -> bool:
    return u.get("current_state") in {STATE_PAUSED, STATE_DAY_CLOSED, STATE_TASK_CRISIS_INPUT, STATE_SAFETY_LOCK, STATE_OFFER_SCREEN}


def should_reject_action_button(text: str, u: Dict[str, Any]) -> bool:
    if u.get("stage") in {"skill_change_reason", "skill_change_free_text", "skill_change_meaning", "stuck_validation_choice"}:
        return False
    if text not in ACTION_OUTCOME_BUTTONS:
        return False
    if text in {"📱 Залип", "😣 Слишком сложно", "😵 Нет сил"} and u.get("current_state") == STATE_AWAITING_STUCK_REASON:
        return False
    if u.get("current_state") != STATE_AWAITING_RESULT:
        return True
    if not u.get("current_action_id") or not current_day_matches_active_action(u):
        return True
    if is_previous_scenario_finished(u):
        return True
    return False


async def show_action_changed_fallback(m: Message, u: Dict[str, Any], source: str = "action_context_mismatch"):
    # No metrics/profile updates here: stale buttons must not mutate an old scenario.
    u["stage"] = "waiting_next_day"
    set_current_state(u, STATE_PAUSED, close_action=True)
    await save_user(u, DB_PATH)
    await m.answer(STALE_ACTION_CHANGED_TEXT, reply_markup=kb_success_next)

SUCCESS_APPROACH_TEXT = POST_MINIMUM_CONTINUE_TEXT

SUCCESS_REPEAT_LIMIT_TEXT = (
    "Минимум на сегодня уже выполнен. Это успех.\n\n"
    "Можно остановиться и спокойно закрыть день.\n"
    "Если хочешь, можем продолжить тренировку без обязательства."
)

SUCCESS_HELP_PROMPT = (
    "Это добровольный вопрос, не обязательный отчёт.\n\n"
    "Одним словом или голосом:\n"
    "что помогло начать?"
)


SUCCESS_SECOND_STEP_DONE_TEXT = (
    "Ещё один короткий шаг засчитан.\n\n"
    "Можно остановиться и спокойно закрыть день.\n"
    "Если хочешь, можем продолжить тренировку без обязательства."
)


def extra_microstep_prompt(u: Dict[str, Any]) -> str:
    sid = str(current_skill_id(u) or u.get("daily_skill_id") or u.get("current_skill_variant_id") or "")
    task = current_task_title(u, "задачу")
    if sid in {"open_only", "open_without_timer", "visible_next_step", "one_visible_step"}:
        return (
            "Отлично. Ещё 2 минуты — только если хочешь.\n"
            f"Напиши одно слово, с которого начнёшь {task}."
        )
    if sid in {"phone_far_3min", "phone_away_3_min"}:
        return (
            "Отлично. Ещё 2 минуты — открой задачу и просто посмотри на неё 30 секунд."
        )
    if sid in {"bad_first_step", "bad_draft_entry"}:
        return (
            "Отлично. Ещё 2 минуты — напиши одну плохую строку без редактирования."
        )
    if sid in {"task_naming", "name_task", "visible_next_step"}:
        return (
            "Отлично. Ещё 2 минуты — назови следующий физический шаг одним словом."
        )
    return ""


def has_extra_microstep(u: Dict[str, Any]) -> bool:
    return bool(extra_microstep_prompt(u)) and int(u.get("success_repeat_count") or 0) == 0 and not day_closed_today(u)


def success_menu_keyboard(u: Dict[str, Any]) -> ReplyKeyboardMarkup:
    return kb_success_next if has_extra_microstep(u) else kb_success_no_extra


def build_action_request_context(u: Dict[str, Any], profile: Dict[str, Any], skill_map: Dict[str, Any], *, repeat: bool = False) -> Dict[str, Any]:
    """Snapshot the checks that make «Давай действие» pick the current best step."""
    pending_stuck = pending_stuck_validation(u)
    selected_task = current_task_title(u, "")
    sid = current_skill_for_action(u) or current_skill_id(u) or u.get("daily_skill_id") or ""
    return {
        "active_action": bool(u.get("current_action_id") and u.get("current_state") in ACTIVE_ACTION_STATES),
        "day_closed": day_closed_today(u, profile),
        "closed_day_voluntary_prompt_required": day_closed_today(u, profile),
        "fresh_stuck_text": str(pending_stuck.get("text") or profile.get("last_free_stuck_text") or "")[:240],
        "selected_task": selected_task or "сегодняшняя задача",
        "current_skill_id": str(sid or ""),
        "skill_status": str(u.get("daily_skill_status") or ""),
        "skill_history": {
            "worked": _profile_list(profile.get("successful_skills"))[:5],
            "did_not_work": _profile_list(profile.get("failed_skills"))[:5],
            "not_fit": str(profile.get("failed_skill") or profile.get("worst_skill") or ""),
            "map": skill_map_lines(skill_map, 4),
        },
        "trainer_key": u.get("trainer_key") or "marsha",
        "repeat": bool(repeat),
    }


async def remember_action_request_context(u: Dict[str, Any], profile: Dict[str, Any], skill_map: Dict[str, Any], *, repeat: bool = False) -> Dict[str, Any]:
    context = build_action_request_context(u, profile, skill_map, repeat=repeat)
    u["last_action_request_context_json"] = json.dumps(context, ensure_ascii=False)
    await save_user(u, DB_PATH)
    await log_event(u.get("user_id"), "training", "action_request_context_checked", context, DB_PATH, SHEETS_WEBHOOK_URL)
    return context


async def maybe_resume_pending_stuck_validation(m: Message, u: Dict[str, Any]) -> bool:
    pending = pending_stuck_validation(u)
    if not pending:
        return False
    text = str(pending.get("text") or "")
    kind = str(pending.get("kind") or classify_free_stuck_text(text))
    response, keyboard = stuck_validation_response(kind, text)
    u["stage"] = "stuck_validation_choice"
    await save_user(u, DB_PATH)
    await answer_with_keyboard(m, u, response, keyboard, "stuck_validation_choice")
    return True


FEEDBACK_SENSITIVE_MARKERS = (
    "очень устал", "нет сил", "бессмыс", "небезопас", "плачу", "слез", "рыда",
    "не хочу жить", "хочу умереть", "паника", "отчаяние",
)


def feedback_blocked_now(u: Dict[str, Any], text: str = "") -> bool:
    if safety_mode(u) != "none":
        return True
    low = (text or "").lower()
    return any(marker in low for marker in FEEDBACK_SENSITIVE_MARKERS)


def feedback_context(u: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = {
        "day_id": u.get("current_day_id") or "",
        "day_number": int(u.get("day") or 1),
        "skill_id": current_skill_id(u) or u.get("daily_skill_id") or "",
        "trainer_key": u.get("trainer_key") or "",
        "attempts": int(u.get("done_count") or 0),
        "stage": u.get("stage") or "",
    }
    if extra:
        data.update(extra)
    return data


async def feedback_questions_today(u: Dict[str, Any]) -> int:
    day_id = u.get("current_day_id") or ""
    if not day_id:
        return 0
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM user_feedback WHERE user_id=? AND day_id=? AND feedback_type IN ('feedback_instruction_clarity','feedback_validation','feedback_day_value','product_value_score','offer_feedback')",
            (u["user_id"], day_id),
        )
        row = await cur.fetchone()
    return int(row[0] if row else 0)


async def can_show_feedback_question(u: Dict[str, Any], feedback_type: str, *, once_per_test: bool = False, max_per_day: int = 3) -> bool:
    if feedback_blocked_now(u):
        return False
    day_id = u.get("current_day_id") or ""
    if day_id and await feedback_questions_today(u) >= max_per_day:
        return False
    if await user_feedback_count(u["user_id"], DB_PATH, feedback_type, day_id="" if once_per_test else day_id) > 0:
        return False
    return True


async def ask_instruction_clarity_feedback(m: Message, u: Dict[str, Any]) -> bool:
    if int(u.get("done_count") or 0) != 1:
        return False
    if not await can_show_feedback_question(u, "feedback_instruction_clarity"):
        return False
    u["stage"] = "feedback_instruction_clarity"
    await save_user(u, DB_PATH)
    await answer_with_keyboard(m, u, "Коротко для теста: было понятно, что делать?", kb_feedback_instruction_clarity, "feedback_instruction_clarity")
    return True


async def ask_validation_feedback(m: Message, u: Dict[str, Any], reason: str, bot_answer: str) -> bool:
    if not await can_show_feedback_question(u, "feedback_validation", once_per_test=True):
        return False
    u["stage"] = "feedback_validation"
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "feedback", "feedback_validation_prompted", {"reason": reason, "bot_answer": bot_answer[:240]}, DB_PATH, SHEETS_WEBHOOK_URL)
    await answer_with_keyboard(m, u, "Коротко для теста: тебе сейчас показалось, что бот понял твою ситуацию?", kb_feedback_validation, "feedback_validation")
    return True


def set_pending_validation_feedback(u: Dict[str, Any], reason: str, bot_answer: str) -> None:
    u["pending_feedback_json"] = json.dumps(
        {"type": "feedback_validation", "reason": reason, "bot_answer": bot_answer[:240]},
        ensure_ascii=False,
    )


def pop_pending_validation_feedback(u: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = json.loads(u.get("pending_feedback_json") or "{}")
    except Exception:
        data = {}
    if data.get("type") == "feedback_validation":
        u["pending_feedback_json"] = None
        return data
    return {}


def should_ask_day_value_feedback(u: Dict[str, Any]) -> bool:
    day = int(u.get("day") or 1)
    return day == 1 or day in {2, 3} or (day > 3 and day % 3 == 0)


async def ask_day_value_feedback(m: Message, u: Dict[str, Any]) -> bool:
    if not should_ask_day_value_feedback(u):
        return False
    if not await can_show_feedback_question(u, "feedback_day_value"):
        return False
    u["stage"] = "feedback_day_value"
    await save_user(u, DB_PATH)
    await answer_with_keyboard(m, u, trainer_wrap(u, "Что сегодня было полезнее всего?", "close"), kb_feedback_day_value, "feedback_day_value")
    return True


async def ask_product_value_feedback(m: Message, u: Dict[str, Any], *, before_offer: bool = False) -> bool:
    day = int(u.get("day") or 1)
    if day not in {2, 3} and not before_offer:
        return False
    if not await can_show_feedback_question(u, "product_value_score", once_per_test=True):
        return False
    u["stage"] = "feedback_product_value_score"
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], u.get("stage", ""), "keyboard_shown", {"keyboard": "feedback_product_value_score", "button_count": 11}, DB_PATH, SHEETS_WEBHOOK_URL)
    await m.answer(
        "Представь, что завтра этого бота больше нет.\nНасколько тебе было бы жалко его потерять?\n\n0 — вообще не жалко\n10 — очень жалко, хочу продолжать",
        reply_markup=kb_feedback_product_score,
    )
    return True


def unclear_skill_simplification_text(u: Dict[str, Any]) -> str:
    task = current_task_title(u, "").strip()
    if not task:
        concrete = "открой место, где обычно лежит эта задача, и ничего не меняй"
        example = "открой файл с резюме и ничего в нём не меняй"
    else:
        concrete = f"открой место для задачи «{task}» и ничего в нём не меняй"
        example = f"открой файл или страницу для «{task}» и просто посмотри 10 секунд"
    return (
        "Понял. Я объяснил слишком абстрактно.\n"
        "Сейчас только одно действие:\n"
        f"{concrete}.\n\n"
        f"Например: {example}.\n\n"
        "Нажми:\n"
        "✅ Открыл(а)\n"
        "🟡 Не смог(ла)"
    )


def kb_unclear_skill_simplified() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Открыл(а)"), KeyboardButton(text="🟡 Не смог(ла)")]],
        resize_keyboard=True,
    )


async def save_feedback_answer(u: Dict[str, Any], feedback_type: str, value: str, *, comment: str = "", metadata: Optional[Dict[str, Any]] = None) -> None:
    ctx = feedback_context(u, metadata)
    await record_user_feedback(
        u["user_id"],
        DB_PATH,
        feedback_type,
        value,
        comment=comment,
        day_id=ctx.get("day_id") or "",
        day_number=int(ctx.get("day_number") or 1),
        skill_id=ctx.get("skill_id") or "",
        trainer_key=ctx.get("trainer_key") or "",
        metadata=ctx,
    )
    await log_event(u["user_id"], "feedback", feedback_type, {"value": value, "comment": comment[:240], **ctx}, DB_PATH, SHEETS_WEBHOOK_URL)


async def handle_feedback_response(m: Message, u: Dict[str, Any], text: str) -> bool:
    stage = u.get("stage") or ""
    low = (text or "").lower().strip()
    if stage == "feedback_instruction_clarity":
        values = {
            "🟢 Да, очень понятно": "clear",
            "🟡 В целом понятно": "partly_clear",
            "🔴 Не понял(а), что от меня хотят": "unclear",
            "🎙️ Напишу или скажу сам(а)": "free_text",
        }
        value = values.get(text, "free_text")
        await save_feedback_answer(u, "feedback_instruction_clarity", value, comment="" if text in values else text)
        if value == "unclear":
            u["stage"] = "downscale_action"
            mark_current_skill_status(u, "stuck")
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "feedback", "instruction_unclear_simplified", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, unclear_skill_simplification_text(u), kb_unclear_skill_simplified(), "unclear_skill_simplified")
            return True
        u["stage"] = "success_menu"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Спасибо, записал для улучшения теста.", success_menu_keyboard(u), "success_menu")
        return True

    if stage == "feedback_validation":
        values = {
            "🟢 Да, очень похоже": "understood",
            "🟡 Частично понял": "partly_understood",
            "🔴 Нет, мимо": "missed",
            "🎙️ Хочу объяснить, что было не так": "free_text",
        }
        value = values.get(text, "free_text")
        if value in {"missed", "free_text"} and text in values:
            u["stage"] = "feedback_validation_free_text"
            await save_user(u, DB_PATH)
            await m.answer("Спасибо. Это важно для теста.\nНапиши или скажи голосом: что бот не понял или что должен был ответить иначе?")
            return True
        await save_feedback_answer(u, "feedback_validation", value, comment="" if text in values else text)
        u["stage"] = "downscale_action"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Спасибо, записал для улучшения теста.", action_keyboard(), "downscale")
        return True

    if stage == "feedback_validation_free_text":
        await save_feedback_answer(u, "feedback_validation", "free_text", comment=text)
        u["stage"] = "downscale_action"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(
            m,
            u,
            "Записал. Сейчас не будем спорить с твоим опытом.\nМожешь вернуться к задаче, сменить навык или закрыть подход.",
            kb_feedback_validation_after_missed,
            "feedback_validation_done",
        )
        return True

    if stage == "feedback_day_value":
        values = {
            "🧩 Маленький конкретный шаг": "small_step",
            "🧠 Разбор, почему я застрял": "stuck_analysis",
            "🤍 Поддержка без стыда": "shame_free_support",
            "🔄 Возможность сменить навык": "skill_change",
            "😐 Пока ничего": "nothing_yet",
            "🎙️ Напишу сам(а)": "free_text",
        }
        value = values.get(text, "free_text")
        if value == "nothing_yet":
            u["stage"] = "feedback_day_none_reason"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Спасибо, это тоже полезный ответ.\nЧто было главным?", kb_feedback_day_none_reason, "feedback_day_none_reason")
            return True
        await save_feedback_answer(u, "feedback_day_value", value, comment="" if text in values else text, metadata={"approaches": int(u.get("done_count") or 0)})
        u["stage"] = "day_core_stop"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Спасибо, записал для улучшения теста.\n\n" + await day_close_metrics_text(u), kb_day_core_stop, "day_core_stop")
        return True

    if stage == "feedback_day_none_reason":
        values = {
            "🤷 Не понял, как пользоваться": "unclear_usage",
            "🧊 Не почувствовал пользы": "no_value_felt",
            "😬 Было слишком много текста": "too_much_text",
            "🐌 Было скучно / медленно": "boring_or_slow",
            "🧠 Совет не подошёл": "advice_mismatch",
            "🎙️ Опишу сам(а)": "free_text",
        }
        value = values.get(text, "free_text")
        await save_feedback_answer(u, "feedback_day_value", "nothing_yet", comment=text if value == "free_text" else "", metadata={"reason": value, "approaches": int(u.get("done_count") or 0)})
        u["stage"] = "day_core_stop"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Спасибо, записал для улучшения теста.\n\n" + await day_close_metrics_text(u), kb_day_core_stop, "day_core_stop")
        return True

    if stage == "feedback_product_value_score":
        if not low.isdigit() or not (0 <= int(low) <= 10):
            await m.answer("Выбери число от 0 до 10.")
            return True
        score = int(low)
        await save_feedback_answer(u, "product_value_score", str(score), metadata={"offer_shown": bool(u.get("last_offer_shown_at"))})
        u["pending_product_value_score"] = score
        u["stage"] = "feedback_product_value_reason"
        await save_user(u, DB_PATH)
        if score <= 6:
            await answer_with_keyboard(m, u, "Спасибо. Что сильнее всего мешает почувствовать пользу?", kb_feedback_product_low, "feedback_product_value_reason")
        else:
            await answer_with_keyboard(m, u, "Спасибо. Что тебе хотелось бы сохранить в боте?", kb_feedback_product_high, "feedback_product_value_reason")
        return True

    if stage == "feedback_product_value_reason":
        score = int(u.get("pending_product_value_score") or 0)
        await save_feedback_answer(u, "product_value_reason", "low_reason" if score <= 6 else "high_value", comment=text, metadata={"score": score})
        u["stage"] = "day_core_stop"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Спасибо, записал для улучшения теста.", kb_day_core_stop, "day_core_stop")
        return True

    if stage == "feedback_offer":
        await save_feedback_answer(u, "offer_feedback", "reason", comment=text, metadata={"value_score": u.get("pending_product_value_score")})
        u["stage"] = "waiting_next_day"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Спасибо, записал для улучшения теста.\n\n" + stay_free_text(), kb_short_mode_main, "short_mode_main")
        return True

    return False


SKILL_RESULT_STATUS_BY_BUTTON = {
    "✅ Сделал — стало легче": ("done_relief", 1),
    "😐 Сделал — но легче не стало": ("done_no_relief", 0),
    "🚪 Сделал — начал задачу": ("done_started_task", 0),
    "🟡 Не получилось": ("not_completed", 0),
    "🤷 Не мой навык": ("not_my_skill", 0),
    "😣 Слишком сложно": ("too_hard", 0),
    "🔄 Нужен другой вход": ("needs_other_entry", 0),
    "⏳ Не успел попробовать": ("not_tried", 0),
}


def skill_result_feedback_text(source: str = "done") -> str:
    return (
        "Зафиксируем честно, что произошло с навыком.\n\n"
        "Выбери ближайший вариант — я не буду считать навык полезным без подтверждённого эффекта."
    )


async def ask_skill_result_feedback(m: Message, u: Dict[str, Any], *, source: str = "done") -> bool:
    if source not in {"action_done", "downscale_done", "downscale_name_done", "return"}:
        return False
    u["stage"] = "skill_result_feedback"
    set_current_state(u, STATE_PAUSED, close_action=True)
    await save_user(u, DB_PATH)
    await answer_with_keyboard(m, u, skill_result_feedback_text(source), kb_skill_result_feedback, "skill_result_feedback")
    return True


async def handle_skill_result_feedback(m: Message, u: Dict[str, Any], text: str) -> bool:
    if u.get("stage") != "skill_result_feedback":
        return False
    if text not in SKILL_RESULT_STATUS_BY_BUTTON:
        await answer_with_keyboard(m, u, "Выбери, что реально произошло с навыком:", kb_skill_result_feedback, "skill_result_feedback")
        return True
    result_status, effect_rating = SKILL_RESULT_STATUS_BY_BUTTON[text]
    sid = current_skill_id(u)
    profile = await get_user_profile(u["user_id"], DB_PATH)
    helpful_count = int(profile.get("skill_helpful_confirmation_count") or 0)
    patch = {
        "last_skill_result_status": result_status,
        "last_skill_effect": "helpful" if effect_rating > 0 else ("started_task" if result_status == "done_started_task" else "not_helpful"),
    }
    if effect_rating > 0:
        helpful_count += 1
        patch.update({
            "best_skill": sid,
            "last_successful_skill": sid,
            "skill_helpful_confirmation_count": helpful_count,
        })
    elif result_status in {"not_completed", "not_my_skill", "too_hard", "needs_other_entry"}:
        patch.update({"failed_skill": sid, "worst_skill": sid})
    u["stage"] = "success_menu"
    await save_user(u, DB_PATH)
    await bot_record_action_event(u, "skill_result_reported", skill_id=sid, metadata={"result_status": result_status, "effect_rating": effect_rating, "button": text})
    await record_profile_signal(u["user_id"], "training", patch, source="skill_result_feedback")
    if effect_rating > 0:
        await record_working_map_skill_result(u["user_id"], "successful_skills", sid)
        msg = "Этот вход часто помогает тебе вернуться к задаче." if helpful_count >= 2 else "Есть первый сигнал, что этот вход тебе помогает."
    elif result_status == "done_started_task":
        msg = "Записал честно: шаг помог начать задачу. Эффект ещё не считаю подтверждённым."
    elif result_status == "done_no_relief":
        msg = "Записал честно: шаг сделан, но легче не стало. Не буду считать навык помогающим."
    elif result_status == "not_tried":
        msg = "Записал: не успел попробовать. Это не провал и не сигнал, что навык помогает."
    else:
        await record_working_map_skill_result(u["user_id"], "failed_skills", sid)
        msg = "Записал: этот вход сейчас не подошёл. Не буду считать его помогающим."
    await answer_with_keyboard(m, u, msg, success_menu_keyboard(u), "success_menu")
    return True


async def send_success_menu(m: Message, u: Dict[str, Any], *, source: str = "done"):
    if await ask_skill_result_feedback(m, u, source=source):
        return
    pending_validation = pop_pending_validation_feedback(u)
    if pending_validation and await ask_validation_feedback(
        m,
        u,
        str(pending_validation.get("reason") or ""),
        str(pending_validation.get("bot_answer") or ""),
    ):
        return
    if await ask_instruction_clarity_feedback(m, u):
        return
    u["stage"] = "success_menu"
    set_current_state(u, STATE_PAUSED, close_action=True)
    await save_user(u, DB_PATH)
    await answer_with_keyboard(m, u, trainer_wrap(u, SUCCESS_APPROACH_TEXT, "continue"), success_menu_keyboard(u), "success_menu")


async def send_success_limit_menu(m: Message, u: Dict[str, Any]):
    u["stage"] = "success_menu"
    set_current_state(u, STATE_PAUSED, close_action=True)
    await save_user(u, DB_PATH)
    await answer_with_keyboard(m, u, trainer_wrap(u, SUCCESS_REPEAT_LIMIT_TEXT, "continue"), kb_success_limit, "success_limit")

STUCK_REASON_PROMPT = (
    "Понял. Это кризис прокрастинации, не провал.\n"
    "Выбери ближайший механизм или опиши текстом/голосом: телефон, страх ошибки, слишком много задач, тревога, самокритика или потеря смысла."
)

kb_stuck_validation_meaning = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🧭 Не вижу смысла в самой задаче")],
        [KeyboardButton(text="🔋 Я слишком устал, чтобы видеть смысл")],
        [KeyboardButton(text="😡 Меня бесит, что я должен это делать")],
        [KeyboardButton(text="🧠 Задача слишком расплывчатая")],
    ],
    resize_keyboard=True,
)

kb_stuck_validation_self_attack = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🤍 Нужно сначала успокоиться")],
        [KeyboardButton(text="✍️ Хочу вернуть маленький шаг")],
        [KeyboardButton(text="🎙️ Хочу сказать ещё")],
    ],
    resize_keyboard=True,
)

kb_stuck_validation_file_fear = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🫁 Сначала снизить тревогу")],
        [KeyboardButton(text="📂 Открыть файл на 10 секунд")],
        [KeyboardButton(text="👤 Нужен внешний контакт")],
    ],
    resize_keyboard=True,
)

kb_stuck_validation_safety = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🟢 Я в безопасности, просто очень устал")],
        [KeyboardButton(text="🟡 Не уверен(а), насколько я в безопасности")],
    ],
    resize_keyboard=True,
)

kb_stuck_analysis_confirm = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Да, похоже"), KeyboardButton(text="🟡 Не совсем")],
        [KeyboardButton(text="🔄 Сменить навык"), KeyboardButton(text="🧠 Уточнить")],
    ],
    resize_keyboard=True,
)

STUCK_REASON_CONFIG = {
    "phone": {
        "buttons": {"📱 Ушёл в телефон / YouTube", "📱 Залип", "📱 Ушёл в телефон"},
        "skill_id": "phone_far_3min",
        "skill_name": "Телефон вне руки",
        "minimum_step": "Отодвинуть телефон на 30 секунд и открыть задачу на 10 секунд",
        "modality": "environment",
        "profile_patch": {"avoidance_pattern": "dopamine_avoidance", "attention_pattern": "scroll_autopilot"},
    },
    "shame": {
        "buttons": {"😬 Страшно, стыдно, боюсь ошибиться", "😬 Страх ошибки / оценки"},
        "skill_id": "bad_first_step",
        "skill_name": "Плохой черновик",
        "minimum_step": "Написать одну плохую строку, которую никто не увидит",
        "modality": "cognitive",
        "profile_patch": {"avoidance_pattern": "perfectionism_start_block", "emotional_trigger": "shame_or_anxiety"},
    },
    "overwhelm": {
        "buttons": {"🧠 Слишком много всего", "😣 Слишком сложно", "🌀 Слишком много вариантов", "😶 Не понимаю, с чего начать"},
        "skill_id": "visible_next_step",
        "skill_name": "Один следующий шаг",
        "minimum_step": "Назвать одно действие: «открыть», «написать», «найти», «отправить»",
        "modality": "cognitive",
        "profile_patch": {"avoidance_pattern": "task_too_big", "downscale_pattern": "needs_single_next_step"},
    },
    "energy": {
        "buttons": {"🔋 Нет сил", "😵 Нет сил", "😵 Слишком тяжело"},
        "skill_id": "body_before_task",
        "skill_name": "Сначала тело",
        "minimum_step": "Длинный выдох, ноги на пол, вода или встать",
        "modality": "body",
        "profile_patch": {"avoidance_pattern": "low_energy", "energy_pattern": "low_start_energy"},
    },
    "anxiety": {
        "buttons": {"🫨 Тревога и перегруз"},
        "skill_id": "body_before_task",
        "skill_name": "Сузить поле",
        "minimum_step": "Назвать одну контролируемую вещь, сделать один короткий выдох и выбрать ближайшее действие",
        "modality": "body",
        "profile_patch": {"avoidance_pattern": "anxiety_overload", "emotional_trigger": "anxiety_overload"},
    },
    "self_attack": {
        "buttons": {"🧨 Самокритика после срыва"},
        "skill_id": "bad_first_step",
        "skill_name": "Факт вместо приговора",
        "minimum_step": "Записать один факт: что реально произошло, без оценки себя, и выбрать минимальный возврат",
        "modality": "cognitive",
        "profile_patch": {"avoidance_pattern": "shame_self_attack", "emotional_trigger": "self_criticism_after_slip"},
    },
    "meaning": {
        "buttons": {"🤷 Не понимаю, зачем это делать"},
        "skill_id": "visible_next_step",
        "skill_name": "Маленькая цель",
        "minimum_step": "Назвать, зачем нужен ближайший результат, и сделать две минуты без требования мотивации",
        "modality": "cognitive",
        "profile_patch": {"avoidance_pattern": "meaning_gap", "motivation_pattern": "value_disconnected"},
    },
}

SKILL_LEARNING_REFRAME_TEXT = (
    "Это не откат. Мы нашли, что предыдущий шаг был не по размеру или не по механизму. "
    "Сейчас подберём другой."
)


def skill_learning_signal_patch(previous_sid: str, reason: str, better_entry_type: str, tolerable_difficulty: str, new_sid: str = "") -> Dict[str, Any]:
    return {
        "failed_skill": previous_sid,
        "worst_skill": previous_sid,
        "last_not_fit_skill": previous_sid,
        "last_not_fit_reason": reason,
        "last_better_entry_type": better_entry_type,
        "current_tolerable_difficulty": tolerable_difficulty,
        "next_skill_hint": new_sid,
        "best_variant": new_sid or better_entry_type,
    }


def tolerable_difficulty_for_reason(code: str) -> str:
    return {
        "shame": "одна плохая строка без показа",
        "overwhelm": "одно физическое действие на 60–120 секунд",
        "energy": "телесный шаг без требования продуктивности",
        "phone": "сначала подготовить среду на 30 секунд",
        "anxiety": "сузить поле до одной контролируемой вещи",
        "self_attack": "факт без самоприговора и минимальный возврат",
        "meaning": "две минуты действия без требования мотивации",
        "not_my_skill": "другой вход без оценки результата",
        "too_hard": "микрошаг без таймера и давления",
    }.get(code or "", "маленький вход без обязательства")


REPEATED_SIMPLIFICATION_BODY_TEXT = (
    "Похоже, сейчас даже маленький шаг к задаче слишком дорогой.\n\n"
    "Не будем снова давить на неё.\n\n"
    "Сделай только одно:\n"
    "положи ладонь на стол и сделай длинный выдох.\n\n"
    "Это не продуктивность.\n"
    "Это возвращение контроля."
)


def simplification_modality_for(config: Dict[str, Any]) -> str:
    return str(config.get("modality") or "cognitive")


def should_switch_simplification_modality(profile: Dict[str, Any], next_modality: str, u: Optional[Dict[str, Any]] = None) -> bool:
    previous = str((u or {}).get("last_simplification_modality") or profile.get("last_simplification_modality") or "")
    if not previous:
        return False
    if previous == next_modality:
        return True
    return previous == "cognitive" and next_modality == "cognitive"


async def send_repeated_simplification_body_reset(m: Message, u: Dict[str, Any], *, original_code: str, user_text: str = ""):
    previous_sid = current_skill_for_action(u) or current_skill_id(u) or ""
    skill_id = "body_before_task" if "body_before_task" in SKILLS_DB else DOWNSCALE_PRIMARY_SKILL
    u["stage"] = "downscale_action"
    u["last_simplification_modality"] = "body"
    u["skill_variant_label"] = "Сначала тело"
    u["pending_skill_id"] = None
    u["pending_skill_day"] = None
    _remember_downscale_pattern(u, skill_id)
    await record_profile_signal(
        u["user_id"],
        "training",
        {
            **skill_learning_signal_patch(previous_sid, original_code, "body", tolerable_difficulty_for_reason(original_code), skill_id),
            "last_stuck_reason": original_code,
            "last_simplification_modality": "body",
            "previous_simplification_repeated": True,
        },
        source="stuck_modality_switch_body",
    )
    if previous_sid:
        await record_working_map_skill_result(u["user_id"], "failed_skills", previous_sid)
    await bot_record_action_event(u, "step_reduced", skill_id=skill_id, metadata={"reason": "repeated_simplification_body_reset", "original_reason": original_code, "user_text": user_text[:160], "previous_skill_id": previous_sid})
    await log_event(u["user_id"], "training", "stuck_modality_switched", {"from_reason": original_code, "to_modality": "body", "skill_id": skill_id}, DB_PATH, SHEETS_WEBHOOK_URL)
    mark_action_card_active(u)
    await save_user(u, DB_PATH)
    await answer_with_keyboard(m, u, trainer_wrap(u, f"{SKILL_LEARNING_REFRAME_TEXT}\n\n{REPEATED_SIMPLIFICATION_BODY_TEXT}", "stuck"), action_keyboard(), "downscale")



def stuck_reason_code_from_text(text: str) -> str:
    raw = (text or "").strip()
    low = raw.lower()
    for code, config in STUCK_REASON_CONFIG.items():
        if raw in config.get("buttons", set()):
            return code
    if any(x in low for x in ("телефон", "youtube", "ютуб", "залип", "скрол", "scroll")):
        return "phone"
    if any(x in low for x in ("самокрит", "ненавижу себя", "тупой", "тупая", "ничтож", "сорвался", "сорвалась", "срыв")):
        return "self_attack"
    if any(x in low for x in ("нахуя", "нахуй", "зачем", "смысл", "бессмыс", "мотивац")):
        return "meaning"
    if any(x in low for x in ("тревог", "паник", "перегруз", "накрывает")):
        return "anxiety"
    if any(x in low for x in ("страш", "стыд", "ошиб", "боюсь", "плохо", "оцен", "перфекц")):
        return "shame"
    if any(x in low for x in ("много", "слишком", "огром", "непонят", "хаос", "выбрать", "несколько задач")):
        return "overwhelm"
    if any(x in low for x in ("нет сил", "устал", "устала", "энерг", "выжат", "сон")):
        return "energy"
    return "overwhelm"


def classify_free_stuck_text(text: str) -> str:
    low = (text or "").lower()
    if any(x in low for x in ("просрал", "ненавижу себя", "тупой", "тупая", "ничтож")):
        return "self_attack"
    if any(x in low for x in ("самокрит", "сорвался", "сорвалась", "срыв")):
        return "self_attack"
    if ("страш" in low or "ужас" in low or "стыд" in low) and any(x in low for x in ("файл", "документ", "откры")):
        return "file_fear"
    if any(x in low for x in ("нахуя", "нахуй", "зачем", "смысл", "бессмыс", "должен", "должна", "бесит")):
        return "meaning"
    if any(x in low for x in ("телефон", "ютуб", "youtube", "скрол", "залип")):
        return "phone"
    if any(x in low for x in ("тревог", "паник", "перегруз", "накрывает")):
        return "anxiety"
    if any(x in low for x in ("устал", "нет сил", "выжат", "сон")):
        return "energy"
    if any(x in low for x in ("страш", "стыд", "боюсь", "оцен")):
        return "file_fear"
    return "overwhelm"


def reflect_stuck_text(text: str) -> str:
    clean = " ".join((text or "").split())
    return clamp_str(clean, 140) if clean else "сейчас сложно продолжать"


def pending_stuck_clarification(u: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = json.loads(u.get("pending_feedback_json") or "{}")
    except Exception:
        data = {}
    return data if isinstance(data, dict) and data.get("type") == "stuck_clarification" else {}


def stuck_text_needs_clarification(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return True
    clear_markers = (
        "нахуя", "нахуй", "зачем", "смысл", "бессмыс", "должен", "должна", "бесит",
        "страш", "стыд", "боюсь", "оцен", "ошиб", "телефон", "ютуб", "youtube",
        "скрол", "залип", "устал", "устала", "нет сил", "выжат", "сон", "тревог", "паник",
        "перегруз", "не могу выбрать", "самокрит", "сорвался", "сорвалась", "срыв",
        "ненавижу себя", "тупой", "тупая", "ничтож",
    )
    if any(marker in low for marker in clear_markers):
        return False
    words = re.findall(r"[\wёа-яА-Я-]+", low, flags=re.IGNORECASE)
    if len(words) <= 3:
        return True
    vague_markers = {"застрял", "застряла", "не могу", "сложно", "тяжело", "плохо", "не идет", "не идёт", "ступор", "ничего"}
    return len(words) <= 6 and any(marker in low for marker in vague_markers)


def stuck_clarification_question(count: int) -> str:
    questions = [
        "Сейчас тяжелее выбрать, с чего начать, или начать уже выбранное?",
        "Тревога больше про ошибку, последствия или ощущение, что всего слишком много?",
        "Тебе сейчас нужен толчок, спокойствие или понятный план?",
    ]
    index = max(0, min(count, len(questions) - 1))
    return questions[index]


def stuck_validation_response(kind: str, text: str) -> tuple[str, ReplyKeyboardMarkup]:
    heard = reflect_stuck_text(text)
    config = STUCK_REASON_CONFIG.get(stuck_reason_code_from_text(text)) or STUCK_REASON_CONFIG["overwhelm"]
    if kind == "meaning":
        mechanism = "сейчас стопор может быть не в размере шага, а в вопросе смысла или внутреннем сопротивлении"
        hypothesis = "пока есть гипотеза, что сначала нужно вернуть связь с ближайшим полезным результатом"
        config = STUCK_REASON_CONFIG["meaning"]
    elif kind == "self_attack":
        mechanism = "самокритика может делать вход в задачу ещё дороже"
        hypothesis = "пока есть гипотеза, что сначала нужен возврат без самонаказания"
        config = STUCK_REASON_CONFIG["self_attack"]
    elif kind == "file_fear":
        mechanism = "страх ошибки или оценки может превращать первый шаг в риск"
        hypothesis = "пока есть гипотеза, что важнее снизить цену ошибки, а не давить на дисциплину"
        config = STUCK_REASON_CONFIG["shame"]
    elif kind == "phone":
        mechanism = "внимание может уходить в быстрый стимул вместо входа в задачу"
        hypothesis = "пока есть гипотеза, что поможет короткий барьер перед отвлечением"
        config = STUCK_REASON_CONFIG["phone"]
    elif kind == "anxiety":
        mechanism = "тревога и перегруз могут расширять поле так, что старт кажется невозможным"
        hypothesis = "пока есть гипотеза, что сначала нужно сузить поле до одной контролируемой вещи"
        config = STUCK_REASON_CONFIG["anxiety"]
    else:
        mechanism = "задача может выглядеть не как один вход, а как слишком большой ком"
        hypothesis = "пока есть гипотеза, что поможет сузить всё до одного следующего действия"
        config = STUCK_REASON_CONFIG["overwhelm"]

    response = (
        f"Я услышал: {heard}\n\n"
        f"Главный механизм: {mechanism}.\n\n"
        f"Рабочая гипотеза: {hypothesis}.\n\n"
        f"Навык: {config['skill_name']}.\n"
        f"Минимальный физический шаг: {config['minimum_step']}.\n\n"
        "Если я попал мимо — поправь разбор."
    )
    return response, kb_stuck_analysis_confirm


async def start_stuck_text_validation(m: Message, u: Dict[str, Any], text: str):
    pending_clarification = pending_stuck_clarification(u)
    previous_text = str(pending_clarification.get("text") or "").strip()
    combined_text = " ".join(x for x in (previous_text, (text or "").strip()) if x).strip()
    clarification_count = int(pending_clarification.get("count") or 0)

    if stuck_text_needs_clarification(combined_text) and clarification_count < 3:
        question = stuck_clarification_question(clarification_count)
        u["stage"] = "stuck_reason_text"
        u["pending_feedback_json"] = json.dumps({"type": "stuck_clarification", "count": clarification_count + 1, "text": combined_text[:500]}, ensure_ascii=False)
        set_current_state(u, STATE_AWAITING_STUCK_REASON)
        await save_user(u, DB_PATH)
        await log_event(u["user_id"], "training", "stuck_clarification_asked", {"count": clarification_count + 1, "text": combined_text[:240]}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(
            "Пока данных мало, не хочу угадывать.\n\n"
            f"{question}\n\n"
            "Ответь коротко одним сообщением или голосом — после уточнения я дам действие."
        )
        return

    text_for_analysis = combined_text or text
    kind = classify_free_stuck_text(text_for_analysis)
    response, keyboard = stuck_validation_response(kind, text_for_analysis)
    u["stage"] = "stuck_validation_choice"
    u["pending_feedback_json"] = json.dumps({"type": "stuck_validation", "kind": kind, "text": text_for_analysis[:500]}, ensure_ascii=False)
    set_current_state(u, STATE_AWAITING_STUCK_REASON)
    await save_user(u, DB_PATH)
    await record_profile_signal(
        u["user_id"],
        "training",
        {
            "last_free_stuck_text": text_for_analysis[:240],
            "last_free_stuck_hypothesis": kind,
            "stuck_clarification_count": clarification_count,
            "user_model_events": [user_model_event(u["user_id"], "barrier_reported", text_for_analysis[:240], confidence=0.8)],
        },
        source="stuck_free_text_validation",
    )
    await log_event(u["user_id"], "training", "stuck_free_text_validated", {"kind": kind, "text": text_for_analysis[:240], "clarification_count": clarification_count}, DB_PATH, SHEETS_WEBHOOK_URL)
    await answer_with_keyboard(m, u, trainer_wrap(u, response, "stuck"), keyboard, f"stuck_validation_{kind}")


def pending_stuck_validation(u: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = json.loads(u.get("pending_feedback_json") or "{}")
    except Exception:
        data = {}
    return data if isinstance(data, dict) and data.get("type") == "stuck_validation" else {}


async def handle_stuck_validation_choice(m: Message, u: Dict[str, Any], text: str) -> bool:
    pending = pending_stuck_validation(u)
    if not pending:
        return False
    kind = str(pending.get("kind") or "overwhelm")
    original_text = str(pending.get("text") or "")
    await record_profile_signal(
        u["user_id"],
        "training",
        {
            "last_free_stuck_choice": text[:120],
            "last_free_stuck_hypothesis": kind,
        },
        source="stuck_validation_choice",
    )
    u["pending_feedback_json"] = None
    await save_user(u, DB_PATH)
    if text == "✅ Да, похоже":
        await send_stuck_reason_skill(m, u, stuck_reason_code_from_text(original_text), user_text=original_text)
        return True
    if text in {"🟡 Не совсем", "🧠 Уточнить"}:
        u["stage"] = "stuck_reason_text"
        u["pending_feedback_json"] = json.dumps({"type": "stuck_validation_more", "previous": original_text[:300]}, ensure_ascii=False)
        await save_user(u, DB_PATH)
        await m.answer("Ок, уточни одним сообщением или голосом: что именно мимо и что важнее учесть? Я пересоберу разбор по твоим словам.")
        return True
    if text == "🔄 Сменить навык":
        await send_stuck_reason_skill(m, u, stuck_reason_code_from_text(original_text or text), user_text=original_text or text)
        return True
    if text == "🟢 Я в безопасности, просто очень устал":
        await send_stuck_reason_skill(m, u, "energy", user_text=original_text)
        return True
    if text in {"🔋 Я слишком устал, чтобы видеть смысл", "🤍 Нужно сначала успокоиться", "🫁 Сначала снизить тревогу"}:
        await send_stuck_reason_skill(m, u, "energy", user_text=original_text)
        return True
    if text in {"🧠 Задача слишком расплывчатая", "✍️ Хочу вернуть маленький шаг"}:
        await send_stuck_reason_skill(m, u, "overwhelm", user_text=original_text)
        return True
    if text == "📂 Открыть файл на 10 секунд":
        await send_stuck_reason_skill(m, u, "shame", user_text=original_text)
        return True
    if text == "👤 Нужен внешний контакт":
        await apply_skill_change(
            m,
            u,
            reason_code="external_contact",
            reason_text="нужен внешний контакт перед входом",
            new_sid="body_doubling_plan",
            new_name="Внешний контакт перед входом",
            intro="Похоже, сейчас одному входить в задачу слишком дорого. Используем контакт с человеком как опору, не как контроль.",
            minimum="Написать одному человеку: «Я попробую открыть задачу на 10 секунд и потом отмечу, начал ли».",
        )
        return True
    if text in {"🧭 Не вижу смысла в самой задаче", "😡 Меня бесит, что я должен это делать"}:
        await apply_skill_change(
            m,
            u,
            reason_code="meaning",
            reason_text=text,
            new_sid="task_naming",
            new_name="Вернуть смысл шага",
            intro="Похоже, сейчас проблема не в размере шага, а в контакте со смыслом или протесте против давления.",
            minimum=meaning_step_text(text, current_task_label(u)),
            meaning_choice=text,
        )
        return True
    if text == "🎙️ Хочу сказать ещё":
        u["stage"] = "stuck_reason_text"
        u["pending_feedback_json"] = json.dumps({"type": "stuck_validation_more", "previous": original_text[:300]}, ensure_ascii=False)
        await save_user(u, DB_PATH)
        await m.answer("Скажи ещё одним сообщением или голосом. Я сначала попробую понять, а не сразу дать совет.")
        return True
    await send_stuck_reason_skill(m, u, stuck_reason_code_from_text(original_text or text), user_text=original_text or text)
    return True


def stuck_skill_card_text(u: Dict[str, Any], config: Dict[str, Any], *, user_text: str = "") -> str:
    reflected = f"\n\nЯ услышал: {clamp_str(user_text, 180)}" if user_text else ""
    return (
        f"{config['skill_name']}.{reflected}\n\n"
        f"Минимальный шаг:\n{config['minimum_step']}\n\n"
        "Что получилось?"
    )


async def send_stuck_reason_skill(m: Message, u: Dict[str, Any], code: str, *, user_text: str = ""):
    config = STUCK_REASON_CONFIG.get(code) or STUCK_REASON_CONFIG["overwhelm"]
    if code == "phone":
        u["last_event"] = "stuck"
        mark_pending_return_after_disruption(u, "stuck_phone")
        await bot_record_action_event(u, "slip_reported", metadata={"source": "stuck_reason", "user_text": user_text[:160]})
    elif code == "overwhelm":
        await bot_record_action_event(u, "too_hard_reported", metadata={"source": "stuck_reason", "user_text": user_text[:160]})
    elif code == "energy":
        await bot_record_action_event(u, "no_energy_reported", metadata={"source": "stuck_reason", "user_text": user_text[:160]})
    modality = simplification_modality_for(config)
    profile = await get_user_profile(u["user_id"], DB_PATH)
    if should_switch_simplification_modality(profile, modality, u):
        await send_repeated_simplification_body_reset(m, u, original_code=code, user_text=user_text)
        return
    previous_sid = current_skill_for_action(u) or current_skill_id(u) or ""
    skill_id = config["skill_id"] if config["skill_id"] in SKILLS_DB else DOWNSCALE_PRIMARY_SKILL
    mark_current_skill_status(u, "stuck")
    u["stage"] = "downscale_action"
    u["last_simplification_modality"] = modality
    u["skill_variant_label"] = config["skill_name"]
    u["pending_skill_id"] = None
    u["pending_skill_day"] = None
    _remember_downscale_pattern(u, skill_id)
    await record_profile_signal(
        u["user_id"],
        "training",
        {
            **config.get("profile_patch", {}),
            **skill_learning_signal_patch(previous_sid, code, modality, tolerable_difficulty_for_reason(code), skill_id),
            "last_stuck_reason": code,
            "last_simplification_modality": modality,
        },
        source=f"stuck_reason_{code}",
    )
    if previous_sid:
        await record_working_map_skill_result(u["user_id"], "failed_skills", previous_sid)
    await bot_record_action_event(u, "step_reduced", skill_id=skill_id, metadata={"reason": f"stuck_{code}", "user_text": user_text[:160], "previous_skill_id": previous_sid})
    await bot_record_action_event(u, "stuck_reason_selected", skill_id=skill_id, metadata={"reason": code})
    await log_event(u["user_id"], "training", "stuck_reason_selected", {"reason": code, "skill_id": skill_id}, DB_PATH, SHEETS_WEBHOOK_URL)
    mark_action_card_active(u)
    await save_user(u, DB_PATH)
    bot_answer = f"{SKILL_LEARNING_REFRAME_TEXT}\n\n{stuck_skill_card_text(u, config, user_text=user_text)}"
    set_pending_validation_feedback(u, code, bot_answer)
    await save_user(u, DB_PATH)
    await answer_with_keyboard(m, u, trainer_wrap(u, bot_answer, "stuck"), action_keyboard(), "downscale")


def is_misunderstood_button(text: str) -> bool:
    low = (text or "").lower().strip()
    return text == "😑 Ты меня не понял" or "ты меня не понял" in low


def misunderstood_prompt_text() -> str:
    return (
        "Ок. Тогда не защищаю прошлый ответ.\n"
        "Что именно мимо?\n\n"
        "1. Не та проблема\n"
        "2. Слишком общий ответ\n"
        "3. Не тот навык\n"
        "4. Это не про лень\n"
        "5. Хочу объяснить иначе"
    )


def misunderstood_context(u: Dict[str, Any]) -> Dict[str, Any]:
    try:
        data = json.loads(u.get("pending_plan_change") or "{}")
        return data if isinstance(data, dict) and data.get("type") == "misunderstood" else {}
    except Exception:
        return {}


async def open_misunderstood_flow(m: Message, u: Dict[str, Any], source: str):
    u["stage"] = "misunderstood_reason"
    u["pending_plan_change"] = json.dumps({"type": "misunderstood", "source": source}, ensure_ascii=False)
    u["analysis_retry_count"] = int(u.get("analysis_retry_count") or 0) + 1
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "analysis", "misunderstood_clicked", {"source": source}, DB_PATH, SHEETS_WEBHOOK_URL)
    await answer_with_keyboard(m, u, misunderstood_prompt_text(), kb_misunderstood_reasons, "misunderstood_reasons")


def stored_analysis_user_text(u: Dict[str, Any]) -> str:
    """Return a safe non-verbatim analysis prompt from stored categories only."""
    try:
        data = json.loads(u.get("analysis_json") or "{}")
        if not isinstance(data, dict):
            return ""
        summary = data.get("input_signal_summary") if isinstance(data.get("input_signal_summary"), dict) else {}
        parts = [
            data.get("specific_pattern") or summary.get("specific_pattern"),
            data.get("avoidance_behavior") or summary.get("avoidance_behavior"),
            data.get("useful_signal") or summary.get("useful_signal"),
        ]
        skills = data.get("skills_focus") or summary.get("skills_focus") or []
        if isinstance(skills, list) and skills:
            parts.append("; ".join(str(x) for x in skills[:4]))
        safe = ". ".join(str(x).strip() for x in parts if x)
        return clamp_str(safe, 700)
    except Exception:
        return ""


async def rebuild_analysis_lightweight(m: Message, u: Dict[str, Any], extra_text: str, reason: str, *, replace_skill: bool = False):
    previous_text = stored_analysis_user_text(u)
    combined_text = clamp_str(f"{previous_text}\n\nУточнение: {extra_text}" if previous_text else extra_text, 1500)
    comp = await ai_analyze_comprehensive(combined_text, u.get("trainer_key", "marsha"), client, OPENAI_CHAT_MODEL)
    comp = normalize_analysis(comp, combined_text)
    comp.pop("user_text", None)
    comp.update(safe_analysis_memory(combined_text, comp))
    u["analysis_json"] = json.dumps(comp, ensure_ascii=False)
    u["bucket"] = comp.get("bucket") or u.get("bucket") or "mixed"

    new_sid = comp.get("selected_skill") if comp.get("selected_skill") in SKILLS_DB else None
    if replace_skill and not new_sid:
        new_sid = rebuild_current_skill(u)
    elif replace_skill and new_sid:
        apply_skill_rebuild(u, new_sid)

    source = misunderstood_context(u).get("source") or "analysis"
    u["pending_plan_change"] = None
    u["stage"] = "confirm_analysis" if source == "confirm_analysis" else "waiting_next_day"
    await save_user(u, DB_PATH)

    patch = {
        "main_pattern": comp.get("specific_pattern"),
        "avoidance_behavior": comp.get("avoidance_behavior"),
        "useful_signal": comp.get("useful_signal"),
        "last_misunderstood_reason": reason,
    }
    await update_user_profile(u["user_id"], patch, DB_PATH)
    await log_event(u["user_id"], "analysis", "analysis_rebuilt", {"reason": reason, "bucket": u.get("bucket")}, DB_PATH, SHEETS_WEBHOOK_URL)
    await log_event(u["user_id"], "analysis", "profile_map_updated", {"source": "misunderstood", **patch}, DB_PATH, SHEETS_WEBHOOK_URL)
    if replace_skill and new_sid:
        await log_event(u["user_id"], "training", "skill_rebuilt", {"skill_id": new_sid, "reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)

    msg = format_comprehensive_analysis(comp, trainer_key=u.get("trainer_key") or "marsha")
    if replace_skill and new_sid:
        msg += f"\n\nНовый навык на сейчас: {SKILLS_DB[new_sid]['name']}"
    markup = kb_analysis_confirm if u["stage"] == "confirm_analysis" else kb_training_main
    await answer_with_keyboard(m, u, msg, markup, "analysis_rebuilt")


def apply_skill_rebuild(u: Dict[str, Any], new_sid: str):
    plan = get_current_plan(u)
    if not plan:
        plan = build_28_day_plan(u.get("bucket") or "mixed")
    day = int(u.get("day") or 1)
    idx = max(0, min(len(plan) - 1, day - 1))
    plan[idx] = new_sid
    u["plan_json"] = json.dumps(plan, ensure_ascii=False)
    replace_day_core_skill(u, new_sid)


def rebuild_current_skill(u: Dict[str, Any]) -> str:
    plan = get_current_plan(u)
    day = int(u.get("day") or 1)
    current_sid = current_skill_id(u) or (plan[max(0, min(len(plan) - 1, day - 1))] if plan else "open_only")
    current_skill = SKILLS_DB.get(current_sid, {})
    track = current_skill.get("track") or u.get("bucket") or "mixed"
    new_sid = suggest_alternative_skill(track, current_sid, u) or current_sid
    if new_sid == current_sid:
        alt = [k for k, v in SKILLS_DB.items() if v.get("track") == track and k != current_sid]
        if alt:
            new_sid = alt[0]
    apply_skill_rebuild(u, new_sid)
    return new_sid

def _profile_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v) for v in parsed if v]
        except Exception:
            return [value]
    return []




def _profile_append_unique(profile: Dict[str, Any], key: str, value: str, limit: int = 12) -> List[str]:
    values = _profile_list(profile.get(key))
    if value and value not in values:
        values.append(value)
    return values[-limit:]


def _system_day_label(system_id: str) -> str:
    for item in LONG_TERM_MICRO_HABITS:
        if item.get("id") == system_id:
            return item.get("map_label") or item.get("id") or system_id
    return system_id or "система дня"


def _system_day_signals_text(summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    for sid in summary.get("system_day_useful") or []:
        lines.append(f"✔ {_system_day_label(sid)}")
    for sid in summary.get("system_day_already") or []:
        label_text = _system_day_label(sid)
        if label_text not in [line.replace("✔ ", "") for line in lines]:
            lines.append(f"✔ уже используете: {label_text}")
    if not lines and summary.get("system_day_opened"):
        for sid in (summary.get("system_day_opened") or [])[:2]:
            lines.append(f"✔ открывали систему: {_system_day_label(sid)}")
    return "\n".join(lines[:4])


def _crisis_pattern_label(pattern: str) -> str:
    return {
        "attention_escape": "чаще всего выбивает залипание",
        "task_entry_block": "чаще всего ломается вход в задачу",
        "perfectionism": "часто выбивает страх ошибки",
        "overwhelm": "часто выбивает перегруз масштаба",
        "low_energy": "часто выбивает нехватка ресурса",
        "self_attack": "часто выбивает самокритика",
        "anxiety_loop": "часто выбивает тревожная петля",
    }.get(pattern or "", "кризисный паттерн ещё уточняется")


def _crisis_map_signals_text(summary: Dict[str, Any]) -> str:
    lines: List[str] = []
    pattern = summary.get("most_common_crisis_pattern") or summary.get("crisis_pattern") or ""
    skill = summary.get("most_effective_crisis_skill") or summary.get("crisis_skill") or ""
    rate = summary.get("crisis_success_rate")
    if pattern:
        lines.append(f"✔ {_crisis_pattern_label(pattern)}")
    if skill:
        lines.append(f"✔ лучший кризисный навык — {skill}")
    if rate not in (None, "", 0, "0"):
        try:
            pct = int(float(rate) * 100)
            lines.append(f"✔ после кризисного навыка легче в {pct}% отметок")
        except Exception:
            pass
    return "\n".join(lines[:3])



def _skill_replacement_count_today(profile: Dict[str, Any], u: Dict[str, Any]) -> int:
    if profile.get("skill_replace_date") != local_date_for_user(u):
        return 0
    try:
        return max(0, int(profile.get("skill_replace_count_today") or 0))
    except (TypeError, ValueError):
        return 0


def choose_replacement_skill(u: Dict[str, Any], seen_today: List[str]) -> str:
    plan = get_current_plan(u)
    day = int(u.get("day") or 1)
    current_sid = current_skill_id(u) or (plan[max(0, min(len(plan) - 1, day - 1))] if plan else "open_only")
    current_skill = SKILLS_DB.get(current_sid, {})
    track = current_skill.get("track") or u.get("bucket") or "mixed"
    candidates: List[str] = []
    suggested = suggest_alternative_skill(track, current_sid, u)
    if suggested:
        candidates.append(suggested)
    candidates.extend([sid for sid in CORE_LAUNCH_WEEK_SKILL_IDS if sid in SKILLS_DB])
    candidates.extend([sid for sid, skill in SKILLS_DB.items() if skill.get("track") == track])
    candidates.extend([sid for sid in SKILLS_DB])
    for sid in candidates:
        if sid in SKILLS_DB and sid != current_sid and sid not in seen_today:
            return sid
    for sid in candidates:
        if sid in SKILLS_DB and sid != current_sid:
            return sid
    return current_sid if current_sid in SKILLS_DB else ("open_only" if "open_only" in SKILLS_DB else next(iter(SKILLS_DB.keys())))



async def bot_record_action_event(u: Dict[str, Any], event_type: str, *, attempt_id: Optional[int] = None, skill_id: str = "", metadata: Optional[Dict[str, Any]] = None):
    await record_action_event(
        u["user_id"],
        DB_PATH,
        event_type,
        day_id=str(u.get("current_day_id") or ""),
        attempt_id=attempt_id,
        skill_id=skill_id or current_skill_for_action(u),
        task_id=str(u.get("current_task_id") or u.get("today_target") or ""),
        metadata=metadata or {},
    )


async def record_return_after_slip_action_event_if_needed(u: Dict[str, Any], source: str):
    if u.get("last_event") == "stuck" or u.get("pending_return_reason") == "stuck_phone":
        await bot_record_action_event(u, "returned_after_slip", metadata={"source": source})
        return
    day_id = str(u.get("current_day_id") or "")
    if not day_id:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT event_type, COUNT(*) FROM action_events WHERE user_id=? AND day_id=? AND event_type IN ('slip_reported', 'returned_after_slip') GROUP BY event_type",
            (u["user_id"], day_id),
        )
        counts = {str(event_type): int(count or 0) for event_type, count in await cur.fetchall()}
    if counts.get("slip_reported", 0) > counts.get("returned_after_slip", 0):
        await bot_record_action_event(u, "returned_after_slip", metadata={"source": source, "detected_from_event_log": True})

def replacement_reason_code(reason: str) -> str:
    low = (reason or "").lower()
    if "сложно" in low or "hard" in low:
        return "слишком сложно"
    if "не подходит" in low or "не работает" in low or "другой" in low or "replace" in low:
        return "не подходит"
    if "нет сил" in low or "устал" in low:
        return "нет сил"
    return "другое"


async def replace_skill_or_request_rediagnosis(m: Message, u: Dict[str, Any], reason: str) -> bool:
    profile = await get_user_profile(u["user_id"], DB_PATH)
    today = local_date_for_user(u)
    allow_user_requested_replacement = reason in {"new_day_other_skill", "route_other_skill", "training_other_skill"}
    if not day_core_test_mode_enabled(u) and not allow_user_requested_replacement:
        sid = current_skill_id(u) or "open_only"
        skill = dict(SKILLS_DB.get(sid) or SKILLS_DB.get("open_only") or next(iter(SKILLS_DB.values())))
        skill.setdefault("skill_id", sid)
        u["skill_variant_label"] = "Вариант сейчас"
        await record_profile_signal(u["user_id"], "training", {
            "skill_replacement_blocked_by_day_lock": True,
            "last_replacement_reason": reason,
        }, source="day_lock_skill_replace")
        await log_event(u["user_id"], "training", "skill_replace_blocked_day_lock", {"skill_id": sid, "reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(
            m,
            u,
            "Сегодня основной навык не меняю.\n\n"
            "Это и есть тренировка: не искать новую технику, а уменьшить вход в текущую.\n\n"
            f"Вариация текущего навыка:\n\n{format_skill_card(u, skill, current_task_label(u))}",
            action_keyboard(),
            "skill_card",
        )
        return True
    count = _skill_replacement_count_today(profile, u)
    if count >= MAX_SKILL_REPLACEMENTS_BEFORE_REDIAGNOSIS:
        u["stage"] = "await_input_mode"
        u["pending_skill_id"] = None
        u["pending_skill_day"] = None
        clear_day_core_lock(u)
        await save_user(u, DB_PATH)
        await record_profile_signal(u["user_id"], "training", {
            "skill_replace_limit_reached": True,
            "skill_replace_count_today": count,
            "skill_replace_date": today,
        }, source="skill_replace_limit")
        await log_event(u["user_id"], "training", "skill_replace_limit_reached", {"count": count, "reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(
            m,
            u,
            "Три замены подряд — это уже сигнал, что карта собрана мимо.\n\n"
            "Не буду дальше крутить случайные навыки.\n"
            "Нужно быстро пересобрать диагностику и выбрать другой вход.",
            kb_input_mode,
            "input_mode",
        )
        return True

    seen_today = _profile_list(profile.get("skill_replace_seen_today")) if profile.get("skill_replace_date") == today else []
    current_seen = current_skill_for_action(u)
    if current_seen and current_seen not in seen_today:
        seen_today.append(current_seen)
    new_sid = choose_replacement_skill(u, seen_today)
    plan = get_current_plan(u) or build_28_day_plan(u.get("bucket") or "mixed")
    day = int(u.get("day") or 1)
    idx = max(0, min(len(plan) - 1, day - 1))
    plan[idx] = new_sid
    u["plan_json"] = json.dumps(plan, ensure_ascii=False)
    previous_sid = current_skill_for_action(u)
    u["previous_replaced_skill_id"] = previous_sid
    u["previous_replaced_skill_status"] = "replaced"
    u["stage"] = "training"
    u["current_skill"] = new_sid
    u["current_skill_variant"] = new_sid
    u["pending_skill_id"] = new_sid
    u["pending_skill_day"] = day
    u["daily_skill_id"] = new_sid
    u["daily_skill_name"] = (SKILLS_DB.get(new_sid) or {}).get("name") or new_sid
    u["daily_skill_status"] = "in_progress"
    replace_day_core_skill(u, new_sid)
    await ensure_user_day(u, DB_PATH, calendar_date=local_date_for_user(u), skill_id=new_sid, skill_name=u["daily_skill_name"])
    await bot_record_action_event(u, "skill_changed", skill_id=new_sid, metadata={"reason": replacement_reason_code(reason), "raw_reason": reason})
    await save_user(u, DB_PATH)

    seen_today.append(new_sid)
    await record_profile_signal(u["user_id"], "training", {
        **skill_learning_signal_patch(previous_sid, replacement_reason_code(reason), "replacement", tolerable_difficulty_for_reason(replacement_reason_code(reason)), new_sid),
        "skill_replace_date": today,
        "skill_replace_count_today": count + 1,
        "skill_replace_seen_today": seen_today[-MAX_SKILL_REPLACEMENTS_BEFORE_REDIAGNOSIS:],
        "last_replacement_skill": new_sid,
        "last_replacement_reason": replacement_reason_code(reason),
    }, source="skill_replace")
    if previous_sid:
        await record_working_map_skill_result(u["user_id"], "failed_skills", previous_sid)
    await log_event(u["user_id"], "training", "skill_replaced", {
        "skill_id": new_sid,
        "count": count + 1,
        "max_count": MAX_SKILL_REPLACEMENTS_BEFORE_REDIAGNOSIS,
        "reason": reason,
        "reason_code": replacement_reason_code(reason),
        "day_id": u.get("current_day_id"),
    }, DB_PATH, SHEETS_WEBHOOK_URL)

    skill = dict(SKILLS_DB[new_sid])
    skill.setdefault("skill_id", new_sid)
    text = (
        f"{SKILL_LEARNING_REFRAME_TEXT}\n\n"
        f"Предлагаю другой вход. Замена {count + 1}/{MAX_SKILL_REPLACEMENTS_BEFORE_REDIAGNOSIS}.\n\n"
        f"{format_skill_card(u, skill, current_task_label(u))}"
    )
    mark_action_card_active(u)
    await save_user(u, DB_PATH)
    await answer_with_keyboard(m, u, trainer_wrap(u, text, "change"), action_keyboard(), "skill_card")
    return True


SKILL_CHANGE_REASON_CONFIG = {
    "anxiety": {
        "buttons": {"😬 Слишком тревожно / страшно"},
        "skill_id": "body_before_task",
        "reason": "слишком тревожно / страшно",
        "intro": "Похоже, сейчас вход через задачу слишком дорогой.\nСначала уменьшим напряжение, потом вернёмся к действию.",
        "name": "Сначала тело, потом задача",
        "minimum": "Один длинный выдох и назвать задачу одним словом.",
    },
    "energy": {
        "buttons": {"🔋 Нет сил"},
        "skill_id": "body_first",
        "reason": "нет сил",
        "intro": "Похоже, сейчас проблема не в дисциплине, а в ресурсе.\nНе будем требовать от тебя полноценного входа.",
        "name": "Вход через восстановление",
        "minimum": "Встать, выпить воды или умыться. Потом решить: возвращаемся к задаче или закрываем подход без штрафа.",
    },
    "phone": {
        "buttons": {"📱 Меня уносит в телефон / другое"},
        "skill_id": "phone_away_3_min",
        "reason": "уносит в телефон / другое",
        "intro": "Ок. Сначала работаем не с силой воли, а со средой.",
        "name": "Телефон вне руки",
        "minimum": "Положить телефон экраном вниз не в зоне руки. Открыть задачу на 10 секунд.",
    },
    "overwhelm": {
        "buttons": {"🧠 Слишком много всего"},
        "skill_id": "one_visible_step",
        "reason": "слишком много всего",
        "intro": "Похоже, задача сейчас выглядит не как один шаг, а как огромный ком.\nНе будем решать всё. Найдём только физический следующий шаг.",
        "name": "Один физический шаг",
        "minimum": "Выбрать одно действие: открыть / написать / найти / отправить / назвать.",
    },
    "not_my_skill": {
        "buttons": {"🤷 Не моё", "🤷 Не мой навык"},
        "skill_id": "visible_next_step",
        "reason": "навык не подходит",
        "intro": "Не спорим с этим сигналом. Проверим другой механизм входа: не убеждать себя, а найти один видимый следующий шаг.",
        "name": "Один видимый следующий шаг",
        "minimum": "Назвать одно физическое действие и сделать только первые 60 секунд.",
    },
}


def skill_change_code_from_text(text: str) -> str:
    for code, config in SKILL_CHANGE_REASON_CONFIG.items():
        if text in config["buttons"]:
            return code
    low = (text or "").lower()
    if "тревож" in low or "страш" in low:
        return "anxiety"
    if "нет сил" in low or "устал" in low or "ресурс" in low:
        return "energy"
    if "телефон" in low or "уносит" in low or "залип" in low:
        return "phone"
    if "не мо" in low or "не мой" in low or "не подходит" in low:
        return "not_my_skill"
    if "сложно" in low or "тяжело" in low:
        return "overwhelm"
    if "много" in low or "перегруз" in low or "ком" in low:
        return "overwhelm"
    return "overwhelm"


async def open_skill_change_reason(m: Message, u: Dict[str, Any]):
    u["stage"] = "skill_change_reason"
    await save_user(u, DB_PATH)
    await answer_with_keyboard(
        m,
        u,
        "Ок. Не будем повторять навык, который сейчас не ложится.\nЧто в нём не подходит?",
        kb_skill_change_reason,
        "skill_change_reason",
    )


def meaning_step_text(choice: str, task_label: str) -> str:
    low = (choice or "").lower()
    if "деньг" in low or "работ" in low:
        return f"Открой место, где лежит «{task_label}», и напиши один рабочий следующий шаг."
    if "челов" in low:
        return f"Напиши одно предложение: кому станет легче или понятнее, если «{task_label}» сдвинется на 1%."
    if "освобож" in low:
        return f"Напиши одну строку: что освободится позже, если сейчас сделать 10 секунд по «{task_label}»."
    if "страх" in low:
        return "Отметь: «я делаю это из страха». Потом выбери самый маленький шаг, который не требует доказывать ценность."
    return f"Не ищем большой смысл. Только назови «{task_label}» одним словом и остановись."


async def apply_skill_change(
    m: Message,
    u: Dict[str, Any],
    *,
    reason_code: str,
    reason_text: str,
    new_sid: str,
    new_name: str,
    intro: str,
    minimum: str,
    meaning_choice: str = "",
):
    previous_sid = current_skill_for_action(u) or current_skill_id(u) or ""
    if new_sid not in SKILLS_DB:
        new_sid = "open_only" if "open_only" in SKILLS_DB else next(iter(SKILLS_DB.keys()))
    plan = get_current_plan(u) or build_28_day_plan(u.get("bucket") or "mixed")
    day = int(u.get("day") or 1)
    if plan:
        idx = max(0, min(len(plan) - 1, day - 1))
        plan[idx] = new_sid
        u["plan_json"] = json.dumps(plan, ensure_ascii=False)
    previous_sid = current_skill_for_action(u)
    u["previous_replaced_skill_id"] = previous_sid
    u["previous_replaced_skill_status"] = "replaced"
    u["stage"] = "training"
    u["current_skill"] = new_sid
    u["current_skill_variant"] = new_sid
    u["pending_skill_id"] = new_sid
    u["pending_skill_day"] = day
    u["daily_skill_id"] = new_sid
    u["daily_skill_name"] = new_name
    u["daily_skill_status"] = "in_progress"
    replace_day_core_skill(u, new_sid)
    await ensure_user_day(u, DB_PATH, calendar_date=local_date_for_user(u), skill_id=new_sid, skill_name=new_name)
    await bot_record_action_event(
        u,
        "skill_changed",
        skill_id=new_sid,
        metadata={"previous_skill_id": previous_sid, "reason_code": reason_code, "reason": reason_text, "new_skill_name": new_name, "meaning_choice": meaning_choice},
    )
    model_events = [
        user_model_event(u["user_id"], "intervention_not_helpful", f"Пользователь сменил навык: {reason_text}", source_skill_id=previous_sid, confidence=0.8),
        user_model_event(u["user_id"], "intervention_offered", "Навык заменён и предложен как новый тест, не как доказанный рабочий способ", source_skill_id=new_sid, confidence=0.6),
    ]
    await log_event(u["user_id"], "training", "skill_changed_by_user_reason", {
        "previous_skill_id": previous_sid,
        "skill_id": new_sid,
        "reason_code": reason_code,
        "reason": reason_text,
        "meaning_choice": meaning_choice,
    }, DB_PATH, SHEETS_WEBHOOK_URL)
    mark_action_card_active(u)
    await save_user(u, DB_PATH)
    await record_profile_signal(u["user_id"], "training", {
        **skill_learning_signal_patch(previous_sid, reason_code, reason_code, tolerable_difficulty_for_reason(reason_code), new_sid),
        "last_replacement_skill": new_sid,
        "last_replacement_reason": reason_text,
        "skill_change_previous_skill": previous_sid,
        "skill_change_new_skill": new_sid,
    }, source="skill_change_requested")
    if previous_sid:
        await record_working_map_skill_result(u["user_id"], "failed_skills", previous_sid)
    await update_user_profile(u["user_id"], {"user_model_events": model_events}, DB_PATH, source="skill_change_requested_events")
    text = (
        "Навык заменён.\n"
        f"{SKILL_LEARNING_REFRAME_TEXT}\n\n"
        f"{intro}\n\n"
        f"Новый навык:\n🧩 {new_name}\n\n"
        f"Минимум:\n{minimum}\n\n"
        "Что получилось?"
    )
    await answer_with_keyboard(m, u, trainer_wrap(u, text, "change"), action_keyboard(), "skill_card")


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


def profile_patch_from_diagnosis(comp: Dict[str, Any]) -> Dict[str, Any]:
    """Map diagnosis output to the V1 dynamic user profile categories."""
    bucket = (comp.get("bucket") or "mixed").strip()
    mapping = {
        "anxiety": {
            "main_pattern": "anxiety_avoidance",
            "avoidance_reason": "fear_of_bad_result",
            "emotional_trigger": "shame_or_anxiety",
            "barriers": ["страх ошибки или оценки", "тревога перед входом в задачу"],
            "resources": ["бережный маленький шаг"],
            "failure_patterns": ["избегание из-за страха плохого результата"],
            "working_strategies": ["плохой черновик", "уменьшение шага"],
            "emotional_profile": {"dominant_load": "shame_or_anxiety"},
        },
        "low_energy": {
            "main_pattern": "start_avoidance",
            "avoidance_reason": "low_energy",
            "emotional_trigger": "fatigue_or_overload",
            "barriers": ["низкий ресурс на старте", "перегруз"],
            "resources": ["минимально жизнеспособный день"],
            "failure_patterns": ["задача не запускается, когда требует много энергии"],
            "working_strategies": ["сначала тело, потом задача", "минимальный вход"],
            "motivation_profile": {"energy_gate": "low_start_energy"},
            "emotional_profile": {"dominant_load": "fatigue_or_overload"},
        },
        "distractibility": {
            "main_pattern": "start_avoidance",
            "avoidance_reason": "unclear_first_step",
            "emotional_trigger": "distraction_or_restlessness",
            "barriers": ["неясный первый шаг", "отвлечения уводят от действия"],
            "resources": ["видимый следующий шаг"],
            "failure_patterns": ["уход в отвлечение до ясного старта"],
            "working_strategies": ["одно окно", "сделать следующий шаг видимым"],
            "attention_profile": {"risk": "scroll_autopilot_or_context_switching"},
        },
        "mixed": {
            "main_pattern": "start_avoidance",
            "avoidance_reason": "task_too_big",
            "emotional_trigger": "shame_or_anxiety",
            "barriers": ["первый шаг кажется слишком большим", "самокритика после откладывания"],
            "resources": ["способность вернуться через маленький шаг"],
            "failure_patterns": ["откладывание усиливается, когда задача выглядит большой"],
            "working_strategies": ["открыть задачу без требования работать", "уменьшение шага"],
            "attention_profile": {"start_gate": "unclear_or_large_first_step"},
            "emotional_profile": {"dominant_load": "shame_or_anxiety"},
        },
    }
    patch = dict(mapping.get(bucket, mapping["mixed"]))
    analysis_result = comp.get("analysis_result") if isinstance(comp.get("analysis_result"), dict) else {}
    selected_skill = comp.get("selected_skill") or analysis_result.get("recommended_variant")
    skills_focus = comp.get("skills_focus") if isinstance(comp.get("skills_focus"), list) else []
    useful_signal = comp.get("useful_signal")

    patch.update({
        "status": "preliminary",
        "recommended_track": "procrastination",
        "strengths": [x for x in [useful_signal] if x],
        "working_strategies": [*patch.get("working_strategies", []), *[str(x) for x in skills_focus[:3] if x]],
        "development_stats": {
            "diagnosis_completed": True,
            "diagnosis_bucket": bucket,
            "profile_confidence": "preliminary",
        },
    })
    if selected_skill in SKILLS_DB:
        patch["working_strategies"].append(SKILLS_DB[selected_skill].get("name") or selected_skill)
        patch["recommended_variant"] = selected_skill
    return patch


def _day3_offer_profile_points(summary: Dict[str, Any], profile: Dict[str, Any]) -> List[str]:
    attempts = _real_attempts_count(summary, profile) if "_real_attempts_count" in globals() else int(summary.get("done_count") or 0)
    points: List[str] = []
    if int(summary.get("downscale_count") or profile.get("downscale_count") or 0) > 0:
        points.append("похоже, маленький шаг может снижать входной порог")
    if (
        profile.get("shame_signal")
        or profile.get("emotional_trigger") == "shame_or_anxiety"
        or profile.get("main_pattern") == "shame_self_attack"
    ):
        points.append("возможно, самокритика после откладывания влияет на старт")
    if (
        profile.get("avoidance_reason") == "fear_of_bad_result"
        or profile.get("main_pattern") == "anxiety_avoidance"
    ):
        points.append("похоже, страх ошибки или оценки стоит проверить отдельно")
    if int(summary.get("return_count") or profile.get("return_count") or 0) > 0:
        points.append("есть факт возврата после срыва; это можно тренировать дальше")
    if (
        profile.get("preferred_activation") == "body_doubling"
        or profile.get("best_skill") == "body_doubling_plan"
    ):
        points.append("присутствие другого человека может снижать порог старта")
    if (
        summary.get("attention_pattern") == "scroll_autopilot"
        or int(summary.get("attention_escape_count") or 0) > 0
    ):
        points.append("отвлечения включаются как способ уйти от напряжения")
    best_skill = (
        summary.get("best_skill")
        or profile.get("best_skill")
        or profile.get("last_successful_skill")
    )
    confirmed_skills = [x for x in ((summary.get("skill_map") or {}).get("skills") or []) if x.get("status") == "confirmed"]
    if confirmed_skills:
        points.append(f"подтверждённый вход: {_skill_label(str(confirmed_skills[0].get('skill_id')))}")

    fallback = [
        "возможно, легче заходить через маленький шаг",
        "самокритика после откладывания может усиливать ступор",
        "страх оценки или ошибки пока остаётся важной гипотезой",
    ]
    for item in fallback:
        if len(points) >= 3:
            break
        if item not in points:
            points.append(item)
    return points[:5]


def _real_attempts_count(summary: Dict[str, Any], profile: Dict[str, Any]) -> int:
    return max(
        int(summary.get("done_count") or profile.get("done_count") or 0),
        int(profile.get("action_done_count") or 0),
        len(_profile_list(profile.get("completed_skills_effect_unknown"))),
        len(_profile_list(profile.get("successful_skills"))),
    )


def _day3_offer_success_points(summary: Dict[str, Any], profile: Dict[str, Any]) -> List[str]:
    attempts = _real_attempts_count(summary, profile)
    if attempts < 3:
        return [
            "появились первые гипотезы, но данных пока мало",
            "нужно проверить реакции на нескольких реальных попытках",
            "полный режим нужен, чтобы не давать случайные советы",
        ]
    points: List[str] = []
    if int(summary.get("return_count") or profile.get("return_count") or 0) > 0:
        points.append("есть факт возврата к действию после залипания")
    if int(summary.get("downscale_count") or profile.get("downscale_count") or 0) > 0:
        points.append("похоже, уменьшение шага стоит проверить дальше")
    preferred = summary.get("preferred_activation") or profile.get("preferred_activation")
    skill_map_text = summary.get("skill_map_text") or ""
    if skill_map_text:
        points.append("статусы навыков:\n" + skill_map_text)
    elif preferred in {"small_visible_step", "phone_away", "body_doubling"}:
        points.append("короткие физические входы выглядят перспективной гипотезой")
    if profile.get("avoidance_reason") == "fear_of_bad_result" or profile.get("main_pattern") in {"anxiety_avoidance", "shame_self_attack"}:
        points.append("похоже, страх ошибки может влиять на запуск")
    if int(summary.get("done_count") or profile.get("done_count") or profile.get("action_done_count") or 0) > 0:
        points.append("есть факт выполненного шага; эффект проверяем отдельно")
    fallback = [
        "ты уже начал собирать данные о своём запуске",
        "короткий шаг выглядит перспективной гипотезой",
        "страх оценки или ошибки пока остаётся гипотезой",
        "нужно проверить, когда движение появляется легче",
    ]
    for item in fallback:
        if len(points) >= 5:
            break
        if item not in points:
            points.append(item)
    return points[:5]


def _day3_offer_breakdown_point(summary: Dict[str, Any], profile: Dict[str, Any]) -> str:
    if summary.get("attention_pattern") == "scroll_autopilot" or int(summary.get("attention_escape_count") or 0) > 0:
        return "похоже, важная задача вызывает напряжение, а затем хочется уйти в быстрый стимул"
    if profile.get("main_pattern") in {"shame_self_attack", "anxiety_avoidance"}:
        return "возможно, страх ошибки или стыд делают вход дороже; это нужно проверить"
    if int(summary.get("downscale_count") or profile.get("downscale_count") or 0) > 0:
        return "похоже, шаг может быть слишком большим; нужно проверить меньший вход"
    return "пока данных мало: проверяем, что происходит в моменте входа в важную задачу"


def day3_personal_offer_text(summary: Dict[str, Any], profile: Dict[str, Any]) -> str:
    return (
        "За эти дни ты уже сделал несколько маленьких входов в задачу.\n\n"
        "Но пока неясно, что именно сильнее мешает: страх оценки, телефон, перегруз или слишком большой шаг.\n\n"
        "В коротком режиме бот даёт один базовый шаг в день.\n\n"
        "В полном режиме мы не просто даём новый совет.\n"
        "Мы проверяем разные входы, сравниваем эффект и собираем твою личную систему возвращения к делу.\n\n"
        "Например: если тебя ломает страх оценки — бот даст «плохой черновик».\n"
        "Если ты уходишь в телефон — сначала изменит среду.\n"
        "Если шаг слишком большой — уменьшит его до физического действия.\n\n"
        "Полный режим нужен не для большего количества техник.\n"
        "Он нужен, чтобы понять, какие из них реально работают именно у тебя.\n\n"
        "Короткий режим:\n"
        "• один основной навык в день;\n"
        "• один короткий маршрут после застревания;\n"
        "• короткая карта;\n"
        "• кризисная самопомощь;\n"
        "• закрытие дня.\n\n"
        "Полный режим:\n"
        "• несколько вариантов входа в зависимости от причины срыва;\n"
        "• больше замен навыков в течение дня;\n"
        "• сравнение: что помогло, а что не помогло;\n"
        "• персональные выводы по повторяющимся срывам;\n"
        "• недельный план;\n"
        "• более подробный разбор паттернов;\n"
        "• персонализация под страх оценки, отвлечения, перегруз, самокритику, избегание.\n\n"
        "Цена: 14.98 €/месяц.\n\n"
        "Можно остаться бесплатно без стыда — короткий режим останется."
    )


def day3_conclusion_and_map_text(summary: Dict[str, Any], profile: Dict[str, Any]) -> str:
    map_points = _day3_offer_profile_points(summary, profile)[:3]
    success_points = _day3_offer_success_points(summary, profile)[:3]
    map_block = "\n".join(f"• {point}" for point in map_points)
    success_block = "\n".join(f"• {point}" for point in success_points)
    return (
        "🧭 За первые дни — первичная карта, не окончательный вывод.\n"
        "Мы уже видим первые гипотезы, но эта модель будет уточняться на реальных попытках.\n\n"
        f"Что уже похоже на правду:\n{map_block}\n\n"
        f"Что стоит проверить дальше:\n{success_block}\n\n"
        "В полном режиме можно точнее увидеть, какие шаги реально работают или не подходят, "
        "и получить разбор залипаний после каждой попытки.\n\n"
        f"{day3_personal_offer_text(summary, profile)}"
    )


async def show_day3_offer(m: Message, u: Dict[str, Any], source: str):
    """Show the adaptive day-3 map and paid continuation offer."""
    u["stage"] = "offer"
    set_current_state(u, STATE_OFFER_SCREEN, close_action=True)
    u["last_offer_shown_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    set_last_explanation_context(
        u,
        "offer",
        "полный режим после карты",
        "Предложение появляется после накопления первых данных: что сработало, где был срыв и какой тип поддержки нужен дальше.",
        ["карта уже содержит первые поведенческие сигналы", "полный режим нужен для системы, а не для разового совета", "можно остаться в коротком режиме без стыда"],
        "Реши: продолжать коротко или включить полный режим."
    )
    await save_user(u, DB_PATH)

    profile = await get_user_profile(u["user_id"], DB_PATH)
    profile["_skill_map"] = await build_skill_map_data(u, profile)
    summary = build_profile_map_summary(u, profile)
    profile_patch = {
        "main_pattern": summary["main_pattern"],
        "avoidance_trigger": summary["avoidance_trigger"],
        "energy_pattern": summary["energy_pattern"],
        "best_skill": summary["best_skill"],
        "worst_skill": summary["worst_skill"],
        "failed_skill": summary["failed_skill"],
        "preferred_activation": summary["preferred_activation_code"],
        "slip_pattern": summary["slip_pattern"],
        "return_pattern": summary["return_pattern"],
        "attention_pattern": summary["attention_pattern"],
        "side_skill_interest": summary["side_skill_interest"],
        "system_day_opened": summary["system_day_opened"],
        "system_day_useful": summary["system_day_useful"],
        "system_day_already": summary["system_day_already"],
        "system_day_signals": summary["system_day_signals"],
        "most_common_crisis_pattern": summary["most_common_crisis_pattern"],
        "most_effective_crisis_skill": summary["most_effective_crisis_skill"],
        "crisis_count": summary["crisis_count"],
        "crisis_success_rate": summary["crisis_success_rate"],
        "done_count": summary["done_count"],
        "downscale_count": summary["downscale_count"],
        "return_count": summary["return_count"],
        "downscale_pattern": summary["downscale_pattern"],
        "main_hypothesis": summary.get("main_hypothesis", ""),
        "successful_skills": summary.get("successful_skills", []),
        "failed_skills": summary.get("failed_skills", []),
        "trainer_current_mode": summary.get("trainer_current_mode", ""),
        "trainer_switch_count": summary.get("trainer_switch_count", 0),
        "trainer_fit_signal": summary.get("trainer_fit_signal", ""),
    }
    await update_user_profile(u["user_id"], profile_patch, DB_PATH)

    offer_meta = {
        "source": source,
        "day": int(u.get("day") or 0),
        "price_month": "14.98",
        **profile_patch,
    }
    await log_event(u["user_id"], "offer", "offer_shown", offer_meta, DB_PATH, SHEETS_WEBHOOK_URL)
    await log_event(u["user_id"], "offer", "profile_map_updated", {"source": source, **profile_patch}, DB_PATH, SHEETS_WEBHOOK_URL)
    await log_event(u["user_id"], "offer", "day3_conclusion_shown", offer_meta, DB_PATH, SHEETS_WEBHOOK_URL)
    await log_event(u["user_id"], "offer", "adaptive_offer_shown", offer_meta, DB_PATH, SHEETS_WEBHOOK_URL)

    await answer_with_inline_screen(m, u, trainer_wrap(u, day3_conclusion_and_map_text(summary, profile), "offer"), offer_inline_keyboard(u["user_id"]), "offer")

def should_show_day3_offer(u: Dict[str, Any], day: int) -> bool:
    """Day 3 offer is shown only for unpaid users outside free mode.

    Admin fast-forward (testmode/flag) allows testing offer path without waiting 3 days.
    """
    if day_core_test_mode_enabled(u):
        if is_paid(u) or int(u.get("free_mode") or 0) == 1:
            return False
        return True
    if offer_shown_today(u):
        return False
    state = dict(u)
    state["day"] = calendar_program_day(state)
    if not engine_should_show_offer(state):
        return False
    has_payment_url = bool(PAYMENT_URL_MONTH_1498 or PAYMENT_URL_FULL or PAYMENT_URL)
    return ENABLE_PAYMENTS or has_payment_url


def is_admin(user_id: int) -> bool:
    """Admin commands are available only for user IDs listed in ADMIN_IDS env."""
    ids = os.getenv("ADMIN_IDS", "")
    return str(user_id) in [x.strip() for x in ids.split(",") if x.strip()]




def qa_command_allowed(user_id: int, u: Dict[str, Any]) -> bool:
    """Allow QA navigation commands for admins, global TEST_MODE, or users who enabled test access."""
    return (
        is_admin(user_id)
        or TEST_MODE
        or int(u.get("is_test_user") or 0) == 1
        or int(u.get("fast_forward_enabled") or 0) == 1
    )

def payment_month_url() -> str:
    if PAYMENT_ACCEPT_ANY and PAYMENT_TEST_URL:
        return PAYMENT_TEST_URL
    return PAYMENT_MONTH_URL or PAYMENT_URL_MONTH_1498 or PAYMENT_URL_FULL or PAYMENT_URL or "https://your-payment-link"


def paid_access_until(days: int = 30, existing_until: Optional[str] = None) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    base = now
    if existing_until:
        try:
            parsed = dt.datetime.fromisoformat(str(existing_until).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            if parsed > now:
                base = parsed
        except (TypeError, ValueError):
            base = now
    return (base + dt.timedelta(days=days)).isoformat()




kb_full_mode_experiment = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📩 Скопировать текст")],
        [KeyboardButton(text="✅ Отправил(а)"), KeyboardButton(text="↩️ Хочу другой шаг")],
    ],
    resize_keyboard=True,
)


def _profile_list_values(value: Any, limit: int = 4) -> List[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x][:limit]
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x][:limit]
        except Exception:
            return [value.strip()]
    return []


def build_full_mode_plan(u: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    facts = _profile_list_values(profile.get("confirmed_signals"), 4)
    if u.get("current_task_title"):
        facts.insert(0, f"текущая задача: {u.get('current_task_title')}")
    facts = list(dict.fromkeys(facts))[:4]
    hypotheses = []
    if profile.get("main_hypothesis"):
        hypotheses.append(str(profile.get("main_hypothesis")))
    hypotheses.extend(_profile_list_values(profile.get("secondary_hypotheses"), 3))
    hypotheses = list(dict.fromkeys([x for x in hypotheses if x]))[:4]
    task = current_task_title(u, "выбранной задаче")
    step = str(u.get("current_next_physical_step") or "").strip()
    if not facts:
        facts = ["пока данных мало: есть только первые ответы и нажатия"]
    if not hypotheses:
        hypotheses = ["есть первые признаки цикла входа в задачу, но мы ещё не знаем, что главное"]
    experiments = [
        "Что возникает раньше: напряжение перед оценкой, неопределённость первого шага или потеря смысла.",
        "Помогает ли внешний контакт: коротко сообщить живому человеку, что ты начинаешь маленький тест.",
        "Что легче сегодня: открыть задачу или сделать плохой черновик 2 минуты.",
    ]
    message = (
        f"Я сажусь на 10 минут работать над задачей: {task}. "
        "Через 15 минут напишу, начал(а) ли. Мне не нужен совет — только короткая отметка присутствия."
    )
    first_experiment = step or f"открыть место, где лежит задача «{task}», на 10 секунд"
    return {
        "facts": facts,
        "hypotheses": hypotheses,
        "experiments": experiments,
        "first_experiment": first_experiment,
        "copy_text": message,
        "low_data": facts == ["пока данных мало: есть только первые ответы и нажатия"],
    }


def full_mode_welcome_text(plan: Dict[str, Any]) -> str:
    facts = "\n".join(f"— {x}" for x in plan["facts"])
    hypotheses = "\n".join(f"— {x}" for x in plan["hypotheses"])
    experiments = "\n".join(f"{i + 1}. {x}" for i, x in enumerate(plan["experiments"][:3]))
    low_data = "\n\nПока данных мало. Полный режим поможет их собрать, а не будет делать вид, что уже знает тебя полностью." if plan.get("low_data") else ""
    return (
        "Полный режим включён.\n\n"
        "Теперь я не просто буду давать один навык в день.\n"
        "Я буду проверять твой цикл и подбирать следующий шаг по твоим реакциям.\n\n"
        "Сейчас у нас есть:\n\n"
        "Что ты описал:\n"
        f"{facts}\n\n"
        "Что пока гипотеза:\n"
        f"{hypotheses}\n\n"
        "На ближайшие 3 дня мы проверим:\n"
        f"{experiments}\n\n"
        "Первый персональный эксперимент на сегодня:\n"
        f"{plan['first_experiment']}\n\n"
        "Не нужно делать всё сразу. Нужен только первый тест."
        f"{low_data}"
    )


async def send_full_mode_welcome(m: Message, u: Dict[str, Any]):
    profile = await get_user_profile(u["user_id"], DB_PATH)
    plan = build_full_mode_plan(u, profile)
    u["full_mode_plan_json"] = json.dumps(plan, ensure_ascii=False)
    await save_user(u, DB_PATH)
    await m.answer(
        "Полный режим включён для теста.\n"
        "Теперь бот будет глубже собирать повторяющиеся причины срывов и давать недельный маршрут.\n"
        "Следующий персональный шаг — когда ты нажмёшь «Продолжить».",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Продолжить")]], resize_keyboard=True),
    )

def test_payment_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я оплатил(а) — включить тестовый доступ", callback_data="confirm_test_payment")],
    ])


async def grant_paid_access(u: Dict[str, Any], source: str, meta: Optional[Dict[str, Any]] = None) -> None:
    meta = dict(meta or {})
    meta.setdefault("days", 30)
    meta.setdefault("amount", 1 if PAYMENT_ACCEPT_ANY else 14.98)
    days = int(meta.get("days") or 30)
    u["payment_status"] = "paid"
    u["trial_phase"] = "paid"
    u["free_mode"] = 0
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    until = paid_access_until(days, u.get("paid_until"))
    u["paid_until"] = until
    u["full_mode"] = 1
    u["full_mode_started_at"] = u.get("full_mode_started_at") or now_iso
    u["full_mode_until"] = until
    u["last_payment_click"] = u.get("last_payment_click") or source
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], u.get("stage", ""), "payment_completed", {"source": source, **meta}, DB_PATH, SHEETS_WEBHOOK_URL)
    await log_event(u["user_id"], u.get("stage", ""), "paid_mode_started", {"source": source, **meta}, DB_PATH, SHEETS_WEBHOOK_URL)


def offer_inline_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="💳 Подключить полный режим за €14.98", url=payment_month_url())],
        [InlineKeyboardButton(text="📚 Что входит", callback_data="offer_details")],
        [InlineKeyboardButton(text="🧭 Показать мою карту", callback_data="show_map")],
        [InlineKeyboardButton(text="🤔 Остаться в коротком режиме", callback_data="stay_free")],
    ]
    if PAYMENT_ACCEPT_ANY:
        keyboard.append([InlineKeyboardButton(text="✅ Я оплатил(а) — тест", callback_data="confirm_test_payment")])
    if is_admin(user_id) and PAYMENT_TEST_URL:
        keyboard.append([InlineKeyboardButton(text="🧪 Тестовая оплата", url=PAYMENT_TEST_URL)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def offer_details_inline_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Подключить полный режим за €14.98", url=payment_month_url())],
        [InlineKeyboardButton(text="🧭 Показать мою карту", callback_data="show_map")],
        [InlineKeyboardButton(text="🤔 Пока короткий режим", callback_data="stay_free")],
    ])


def stay_free_inline_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Продолжить короткий режим", callback_data="continue_free")],
        [InlineKeyboardButton(text="💳 Всё же подключить полный режим", url=payment_month_url())],
    ])


def offer_details_full_mode_text() -> str:
    return (
        "📚 Честная разница режимов\n\n"
        "Короткий режим:\n"
        "• один основной навык в день;\n"
        "• один короткий маршрут после застревания;\n"
        "• короткая карта;\n"
        "• кризисная самопомощь;\n"
        "• закрытие дня.\n\n"
        "Полный режим:\n"
        "• несколько вариантов входа в зависимости от причины срыва;\n"
        "• больше замен навыков в течение дня;\n"
        "• сравнение: что помогло, а что не помогло;\n"
        "• персональные выводы по повторяющимся срывам;\n"
        "• недельный план;\n"
        "• более подробный разбор паттернов;\n"
        "• персонализация под страх оценки, отвлечения, перегруз, самокритику, избегание."
    )


def stay_free_text() -> str:
    return (
        "Ок. Продолжаем в коротком режиме.\n\n"
        "Короткий режим остаётся рабочим:\n"
        "• один основной навык в день;\n"
        "• один короткий маршрут после застревания;\n"
        "• короткая карта;\n"
        "• кризисная самопомощь;\n"
        "• закрытие дня.\n\n"
        "Полный режим добавляет не просто больше техник, а сравнение эффектов, недельный план и персонализацию под повторяющиеся причины срывов.\n\n"
        "Сегодня можно продолжить без оплаты."
    )



def local_date_for_user(u: Dict[str, Any]) -> str:
    try:
        return dt.datetime.now(ZoneInfo(str(u.get("timezone") or "Europe/Vilnius"))).date().isoformat()
    except Exception:
        return dt.datetime.now(dt.timezone.utc).date().isoformat()


def day_core_test_mode_enabled(u: Dict[str, Any]) -> bool:
    if TEST_MODE:
        return True
    try:
        uid = int(u.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    return qa_command_allowed(uid, u)


def _parse_iso_date(value: str):
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except Exception:
        return None


def ensure_first_start_date(u: Dict[str, Any]) -> str:
    start = _parse_iso_date(u.get("first_start_date"))
    if not start:
        today = local_date_for_user(u)
        u["first_start_date"] = today
        return today
    return start.isoformat()


def calendar_program_day(u: Dict[str, Any]) -> int:
    start = _parse_iso_date(ensure_first_start_date(u))
    today = _parse_iso_date(local_date_for_user(u)) or dt.datetime.now(dt.timezone.utc).date()
    if not start:
        return 1
    return max(1, (today - start).days + 1)


def sync_calendar_day(u: Dict[str, Any]) -> int:
    if day_core_test_mode_enabled(u):
        ensure_first_start_date(u)
        return max(1, int(u.get("day") or 1))
    computed_day = calendar_program_day(u)
    old_day = int(u.get("day") or 1)
    if old_day != computed_day:
        u["day"] = computed_day
        if "current_day" in u:
            u["current_day"] = computed_day
        u["today_target"] = None
        u["pending_skill_id"] = None
        u["pending_skill_day"] = None
    if has_stale_day_core_lock(u):
        clear_day_core_lock(u)
    return computed_day


def has_stale_day_core_lock(u: Dict[str, Any]) -> bool:
    return bool(u.get("day_core_skill_date")) and u.get("day_core_skill_date") != local_date_for_user(u)

def _today_iso(u: Optional[Dict[str, Any]] = None):
    if u:
        return local_date_for_user(u)
    return dt.datetime.now(dt.timezone.utc).date().isoformat()

def should_show_micro_habit(u: Dict[str, Any], source: str = "done") -> bool:
    # System of Day can be shown after the user names today's target
    # or when the training day is closed. It is not a second skill,
    # not progress, and not a streak action.
    allowed_sources = {"day_start", "day_closed", "day_core_stop", "done_enough_today"}
    if source not in allowed_sources:
        return False
    if (u.get("last_micro_habit_date") or "") == _today_iso(u):
        return False
    if u.get("stage") in {"crisis_choose_mode","crisis_voice","crisis_text","crisis_plan_confirm","confirm_analysis", "failed_options", "downscale_action", "downscale_name_task"}:
        return False
    return True

async def maybe_show_micro_habit(m: Message, u: Dict[str, Any], source: str = "done"):
    if not should_show_micro_habit(u, source):
        return False
    habits = LONG_TERM_MICRO_HABITS
    last_id = u.get("last_micro_habit_id")
    pool = [h for h in habits if h.get("id") != last_id] or habits
    habit = random.choice(pool)
    trainer = (u.get("trainer_key") or "marsha").lower()
    variant = (habit.get("trainer_variants") or {}).get(trainer, "")
    text = f"{habit.get('title')}\n\n{habit.get('text')}" + (f"\n\n{variant}" if variant else "")
    u["last_micro_habit_id"] = habit.get("id")
    u["last_micro_habit_date"] = _today_iso(u)
    u["pending_skill_id"] = u.get("pending_skill_id")
    u["micro_habit_json"] = json.dumps(habit, ensure_ascii=False)
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "training", "system_day_shown", {"system_day_id": habit.get("id"), "habit_id": habit.get("id"), "source": source}, DB_PATH, SHEETS_WEBHOOK_URL)
    profile = await get_user_profile(u["user_id"], DB_PATH)
    await record_profile_signal(
        u["user_id"],
        "training",
        {
            "last_system_day_id": habit.get("id"),
            "system_day_opened": _profile_append_unique(profile, "system_day_opened", habit.get("id") or ""),
        },
        source="system_day_shown",
    )
    await answer_with_keyboard(m, u, text, kb_micro_habit, "system_day")
    return True


def current_skill_id(u: Dict[str, Any]) -> str:
    variant = u.get("current_skill_variant_id")
    if variant in SKILLS_DB and u.get("current_core_skill_date") == local_date_for_user(u):
        return variant
    locked = u.get("day_core_skill_id")
    if not day_core_test_mode_enabled(u) and locked in SKILLS_DB and u.get("day_core_skill_date") == local_date_for_user(u):
        return locked
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


def new_day_insights_text(profile: Dict[str, Any]) -> str:
    insights = []
    if int(profile.get("downscale_count") or profile.get("failed_reason_count") or 0) > 0:
        insights.append("большой вход тормозит старт")
        insights.append("уменьшение шага пока выглядит как гипотеза для проверки")
    if profile.get("attention_pattern") == "scroll_autopilot" or int(profile.get("attention_escape_count") or 0) > 0:
        insights.append("залипание усиливается, когда телефон рядом")
    if not insights:
        insights = [
            "данных пока мало, поэтому не делаем выводов о причине",
            "сегодня проверяем один маленький вход",
            "эффект навыка запишем как сигнал, а не как диагноз",
        ]
    cautious_fallback = [
        "данных пока мало, поэтому гипотезы проверяем действием",
        "маленький шаг безопаснее, чем попытка резко собраться",
        "важен факт попытки, а не идеальный результат",
    ]
    while len(insights) < 3:
        for item in cautious_fallback:
            if item not in insights:
                insights.append(item)
                break
    return "\n".join(f"— {item};" for item in insights[:3])


def has_previous_day_evidence(profile: Dict[str, Any]) -> bool:
    profile = profile or {}
    evidence_keys = (
        "action_done_count", "attempts_started", "stuck_count", "attention_escape_count",
        "return_count", "downscale_count", "skill_attempts_total", "crisis_count",
    )
    if any(int(profile.get(key) or 0) > 0 for key in evidence_keys):
        return True
    dev_map = profile.get("development_map") if isinstance(profile.get("development_map"), dict) else {}
    return bool(profile.get("user_model_events") or profile.get("confirmed_signals") or int(dev_map.get("behavior_events_count") or 0) > 0)


def new_day_context_header(profile: Dict[str, Any]) -> str:
    if has_previous_day_evidence(profile):
        return (
            "🌱 Новый день.\n\n"
            "Вчера мы увидели важное:\n"
            f"{new_day_insights_text(profile)}\n\n"
            "Сегодня не начинаем с нуля.\n"
        )
    return (
        "🌱 Новый день.\n\n"
        "Пока у нас мало фактических данных за прошлый день.\n"
        "Начинаем с короткого теста без выводов про вчера.\n"
    )


def new_day_skill_text(skill: Dict[str, Any], profile: Dict[str, Any]) -> str:
    header = new_day_context_header(profile)
    if skill.get("daily_text"):
        return (
            header +
            "Проверим новый вход: не “заставить себя”, а создать условия для старта.\n\n"
            f"{skill.get('daily_text')}\n\n"
            "Что получилось?"
        )
    skill_name = skill.get("name") or "Телефон вне руки на 3 минуты"
    if (skill.get("skill_id") or "") in {"phone_far_3min", "phone_away_3_min"} or "телефон" in skill_name.lower():
        return (
            header +
            "Проверим новый вход: не “заставить себя”, а создать условия для старта.\n\n"
            "🧩 Навык дня: Телефон вне руки на 3 минуты\n\n"
            "Попробуй:\n"
            "1. Положи телефон вне досягаемости руки.\n"
            "2. Открой место задачи.\n"
            "3. Не работай идеально. Просто побудь рядом с задачей 3 минуты.\n\n"
            "Минимум:\n"
            "положить телефон экраном вниз на расстояние вытянутой руки.\n\n"
            "Что получилось?"
        )
    steps = skill.get("simple") or skill.get("steps") or []
    if isinstance(steps, list) and steps:
        step_text = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(steps[:3]))
    else:
        step_text = skill.get("how") or skill.get("how_more") or "Сделай самый маленький вход в задачу 60–120 секунд."
    minimum = skill.get("minimum") or skill.get("minimum_action") or "один маленький вход в задачу"
    return (
        header +
        "Проверим новый вход: не “заставить себя”, а создать условия для старта.\n\n"
        f"🧩 Навык дня: {skill_name}\n\n"
        f"Попробуй:\n{step_text}\n\n"
        f"Минимум:\n{minimum}\n\n"
        "Что получилось?"
    )


DAILY_SKILL_ALIASES = {
    "task_naming": "name_task_one_word",
    "open_only": "open_without_timer",
    "phone_far_3min": "phone_away_3_min",
    "bad_first_step": "bad_draft",
    "body_before_task": "body_first",
    "one_breath": "body_first",
    "minimum_contact": "body_first",
    "visible_next_step": "one_visible_step",
    "choose_one": "one_visible_step",
    "task_cut": "one_visible_step",
}


def _canonical_daily_skill_id(skill_id: Any) -> str:
    raw = str(skill_id or "").strip()
    return DAILY_SKILL_ALIASES.get(raw, raw)


def _profile_or_user_int(u: Dict[str, Any], profile: Dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = profile.get(key, u.get(key))
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _daily_skill_history(u: Dict[str, Any], profile: Dict[str, Any]) -> List[str]:
    history = _profile_list(profile.get("skill_history")) or _profile_list(u.get("skill_history"))
    return [_canonical_daily_skill_id(sid) for sid in history if _canonical_daily_skill_id(sid) in SKILLS_DB]


def _first_available_skill_id(candidates: List[str], blocked: List[str]) -> str:
    for raw_sid in candidates:
        sid = _canonical_daily_skill_id(raw_sid)
        if sid in SKILLS_DB and sid not in blocked:
            return sid
    for fallback in ("bad_draft", "phone_away_3_min", "body_first", "one_visible_step", "open_without_timer"):
        if fallback in SKILLS_DB and fallback not in blocked:
            return fallback
    return next(iter(SKILLS_DB.keys()))


def select_daily_skill(u: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    profile = profile or {}
    last_skills = _daily_skill_history(u, profile)[-3:]
    blocked: List[str] = []
    for raw_sid in ("name_task_one_word", "open_without_timer", "task_naming", "open_only", "visible_next_step", "one_visible_step"):
        sid = _canonical_daily_skill_id(raw_sid)
        if sid in SKILLS_DB and (last_skills.count(sid) >= 2 or last_skills[-2:] == [sid, sid]):
            blocked.append(sid)

    last_crisis_type = str(profile.get("last_crisis_type") or profile.get("crisis_stack") or u.get("last_crisis_type") or "").lower()
    stuck_count = _profile_or_user_int(u, profile, "stuck_count", "attention_escape_count", "failed_stuck_phone_count")
    low_energy_count = _profile_or_user_int(u, profile, "low_energy_count", "low_energy_signal_count")
    too_many_options_count = _profile_or_user_int(u, profile, "too_many_options_count", "overwhelm_count")
    if "youtube" in last_crisis_type or "stuck" in last_crisis_type or "zalip" in last_crisis_type or stuck_count >= 2:
        preferred = ["phone_away_3_min", "bad_draft", "body_first"]
    elif low_energy_count >= 2 or profile.get("energy_pattern") in {"low_start_energy", "low_energy"}:
        preferred = ["body_first", "one_breath", "minimum_contact"]
    elif too_many_options_count >= 2 or profile.get("downscale_pattern") == "entry_too_large":
        preferred = ["one_visible_step", "choose_one", "task_cut"]
    else:
        preferred = ["open_without_timer", "bad_draft", "phone_away_3_min", "one_visible_step"]

    sid = _first_available_skill_id(preferred, blocked)
    skill = dict(SKILLS_DB[sid])
    skill.setdefault("skill_id", sid)
    skill.setdefault("id", sid)
    skill.setdefault("variant", skill.get("variant") or sid)
    return skill


def build_new_day_intro(u: Dict[str, Any], skill: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> str:
    return new_day_skill_text(skill, profile or {})


def build_current_skill_text(skill: Dict[str, Any], prefix: str = "", u: Optional[Dict[str, Any]] = None) -> str:
    skill_name = skill.get("name") or "навык дня"
    steps = skill.get("simple") or skill.get("steps") or []
    if isinstance(steps, list) and steps:
        step_text = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(steps[:3]))
    else:
        step_text = skill.get("how") or skill.get("how_more") or "Сделай самый маленький вход в задачу 60–120 секунд."
    minimum = skill.get("minimum") or skill.get("minimum_action") or "один маленький вход в задачу"
    head = f"{prefix}\n\n" if prefix else ""
    task_line = f"📌 Дело: {current_task_label(u)}\n\n" if u else ""
    status_note = str((u or {}).get("current_skill_status_text") or "").strip()
    status_block = f"Статус проверки:\n{status_note}\n\n" if status_note else ""
    return (
        f"{head}"
        f"{task_line}"
        f"{status_block}"
        f"🧩 Навык дня: {skill_name}\n\n"
        f"Попробуй:\n{step_text}\n\n"
        f"Минимум:\n{minimum}\n\n"
        "Что получилось?"
    )


def action_keyboard() -> ReplyKeyboardMarkup:
    return kb_active_skill


async def open_new_day_skill(m: Message, u: Dict[str, Any], day: int, source: str):
    plan = get_current_plan(u) or build_28_day_plan(u.get("bucket") or "mixed")
    if not plan:
        plan = ["phone_far_3min"] if "phone_far_3min" in SKILLS_DB else list(SKILLS_DB.keys())[:1]
    day = max(1, min(day, len(plan)))
    profile = await get_user_profile(u["user_id"], DB_PATH)
    skill = select_daily_skill(u, profile)
    sid = skill.get("skill_id") or skill.get("id") or plan[day - 1]
    u["day"] = day
    u["stage"] = "training"
    u["has_started_training"] = 1
    u["today_started"] = 1
    u["day_closed"] = 0
    u["today_closed"] = 0
    u["day_status"] = "open"
    u["daily_session_id"] = f"day_{uuid.uuid4().hex[:12]}"
    u["current_action_id"] = None
    u["current_skill"] = sid
    u["current_skill_variant"] = skill.get("variant") or sid
    u["pending_skill_id"] = sid
    u["pending_skill_day"] = day
    u["daily_skill_id"] = sid
    u["daily_skill_name"] = skill.get("name") or sid
    u["daily_skill_status"] = "in_progress"
    if not u.get("current_task_title"):
        u["today_target"] = None
    replace_day_core_skill(u, sid)
    await ensure_user_day(u, DB_PATH, calendar_date=local_date_for_user(u), skill_id=sid, skill_name=skill.get("name") or sid)
    set_last_explanation_context(
        u,
        "skill",
        skill.get("name") or sid,
        skill.get("why_short") or skill.get("explain") or "Новый день открывается сразу навыком, чтобы не возвращать тебя в меню и не увеличивать трение старта.",
        ["новый день = новый вход", "сначала создаём условия для старта", "минимум считается выполнением"],
        "Сделай минимальный шаг и отметь, что получилось."
    )
    await save_user(u, DB_PATH)
    skill_history = (_daily_skill_history(u, profile) + [sid])[-12:]
    await record_profile_signal(
        u["user_id"],
        "training",
        {
            "skill_history": skill_history,
            "last_daily_skill_id": sid,
        },
        source=f"new_day_skill_{source}",
    )
    await bot_record_action_event(u, "attempt_started", skill_id=sid, metadata={"source": source, "day": day})
    await log_event(u["user_id"], "training", "new_day_skill_opened", {"day": day, "skill_id": sid, "source": source}, DB_PATH, SHEETS_WEBHOOK_URL)
    await answer_with_keyboard(m, u, build_new_day_intro(u, skill, profile), kb_first_day_skill, "new_day_skill")


async def start_new_day(user_id: int, message: Message, user: Optional[Dict[str, Any]] = None, source: str = "force_next_day"):
    """Start the new-day scenario directly after admin/test day changes."""
    if user is None:
        user = await get_user(user_id, DB_PATH)
    await start_new_day_for_user(message, user, int(user.get("day") or 1), source)


async def start_new_day_for_user(message: Message, user: Dict[str, Any], day: int, source: str = "new_day"):
    await open_new_day_skill(message, user, day, source)


def is_same_calendar_day(value: Any, u: Optional[Dict[str, Any]] = None) -> bool:
    if not value:
        return False
    return str(value)[:10] == local_date_for_user(u or {})


def current_skill_for_action(u: Dict[str, Any]) -> str:
    for raw_sid in (
        u.get("current_skill"),
        u.get("current_skill_variant"),
        u.get("pending_skill_id"),
        u.get("current_skill_variant_id"),
        u.get("day_core_skill_id"),
        current_skill_id(u),
    ):
        sid = _canonical_daily_skill_id(raw_sid)
        if sid in SKILLS_DB:
            return sid
    return ""


def day_closed_today(u: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> bool:
    profile = profile or {}
    closed_at = u.get("last_day_closed_at") or profile.get("last_day_closed_at")
    if is_same_calendar_day(closed_at, u):
        return True
    return (str(u.get("day_status") or "").lower() == "closed" or bool(int(u.get("day_closed") or u.get("today_closed") or 0))) and not has_stale_day_core_lock(u)


DAY_CLOSED_ACTION_TEXT = (
    "Сегодняшний подход уже закрыт.\n"
    "Это не откат. Завтра откроется следующий короткий шаг."
)
DAY_ALREADY_CLOSED_TEXT = "День уже закрыт.\nДо завтра."
DAY_CLOSED_CONTINUE_PROMPT = (
    "День уже закрыт, и минимум ты выполнил.\n"
    "Хочешь сделать ещё один короткий добровольный подход?"
)
DAY_CLOSED_ALLOWED_KINDS = {"map", "trainer_switch", "crisis", "tomorrow"}
DAY_CLOSED_BLOCKED_KINDS = {"other_skill", "change_skill", "stuck", "more", "details", "why", "enough", "close_day"}
DAY_CLOSED_VOLUNTARY_ACTIONS = {"action", "repeat"}


def closed_day_extra_used_today(u: Dict[str, Any]) -> bool:
    return is_same_calendar_day(u.get("closed_day_extra_step_date"), u) and int(u.get("closed_day_extra_step_count") or 0) >= 1


def next_step_prefix(u: Dict[str, Any], repeat: bool = False, voluntary: bool = False) -> str:
    task = current_task_title(u, "выбранной задаче")
    step = str(u.get("current_next_physical_step") or "").strip()
    done = int(u.get("done_count") or 0)
    if voluntary:
        base = "Добровольный короткий подход. Он не обязанность и не новая норма."
    elif repeat or done > 0 or step:
        base = "Это следующий шаг, не повтор старта."
    else:
        base = "Лучший доступный шаг сейчас."
    if step:
        return f"{base}\n\nЛогичное продолжение по задаче «{task}»: {step}. Сделай только 60–120 секунд или подготовь вход и снова можешь закрыть день."
    if done > 0:
        sid = str(u.get("previous_completed_skill_id") or current_skill_for_action(u) or current_skill_id(u) or u.get("daily_skill_id") or "")
        if sid in {"phone_far_3min", "phone_away_3_min"}:
            continuation = "телефон уже убран — открой нужный файл и просто посмотри на первое место, где можно продолжить"
        elif sid in {"task_naming", "name_task", "visible_next_step", "one_visible_step"}:
            continuation = "назови следующий физический шаг и сделай первые 60–120 секунд"
        elif sid in {"bad_first_step", "bad_draft_entry"}:
            continuation = "напиши один плохой тезис или отправь короткий черновик без полировки"
        elif sid in {"open_only", "open_without_timer"}:
            continuation = "раз файл уже открыт — напиши одну сырую строку или выбери одно место, куда ткнуть курсором"
        else:
            continuation = "открой нужное место, напиши один плохой тезис или сделай 2 минуты работы"
        return f"{base}\n\nЛогичное продолжение по задаче «{task}»: {continuation}. Остановиться можно сразу после этого."
    return f"{base}\n\nВыбери один физический вход в задачу «{task}»: открыть файл, написать одну сырую строку или убрать телефон на 2 минуты."


def continuation_skill_id(u: Dict[str, Any]) -> str:
    sid = str(current_skill_for_action(u) or current_skill_id(u) or u.get("daily_skill_id") or "")
    if sid in {"phone_far_3min", "phone_away_3_min", "open_only", "open_without_timer"}:
        return "visible_next_step" if "visible_next_step" in SKILLS_DB else "one_visible_step"
    if sid in {"task_naming", "name_task", "visible_next_step", "one_visible_step"}:
        return "open_without_timer" if "open_without_timer" in SKILLS_DB else "visible_next_step"
    if sid in {"bad_first_step", "bad_draft_entry"}:
        return "visible_next_step" if "visible_next_step" in SKILLS_DB else "open_without_timer"
    return "visible_next_step" if "visible_next_step" in SKILLS_DB else (sid if sid in SKILLS_DB else next(iter(SKILLS_DB)))


async def open_next_logical_step(m: Message, u: Dict[str, Any], *, source: str = "next_step_after_completion") -> None:
    previous_sid = str(current_skill_for_action(u) or current_skill_id(u) or u.get("daily_skill_id") or "")
    sid = continuation_skill_id(u)
    skill = dict(SKILLS_DB[sid])
    skill.setdefault("skill_id", sid)
    u["stage"] = "training"
    u["current_skill"] = sid
    u["current_skill_variant"] = skill.get("variant") or sid
    u["pending_skill_id"] = sid
    u["daily_skill_id"] = sid
    u["daily_skill_name"] = skill.get("name") or sid
    u["daily_skill_status"] = "in_progress"
    u["previous_completed_skill_id"] = previous_sid
    u["skill_variant_label"] = "Следующий шаг"
    mark_action_card_active(u)
    await save_user(u, DB_PATH)
    await bot_record_action_event(u, "attempt_started", skill_id=sid, metadata={"source": source, "continuation": True})
    await answer_with_keyboard(m, u, build_current_skill_text(skill, next_step_prefix(u, repeat=True), u), action_keyboard(), "next_logical_step")


async def open_closed_day_voluntary_step(m: Message, u: Dict[str, Any]) -> None:
    if closed_day_extra_used_today(u):
        await answer_with_keyboard(m, u, "На сегодня достаточно: добровольный дополнительный подход уже был. День остаётся закрытым.", kb_day_core_stop, "day_core_stop")
        return
    sid = current_skill_for_action(u) or "visible_next_step"
    if sid not in SKILLS_DB:
        sid = next(iter(SKILLS_DB))
    skill = dict(SKILLS_DB[sid])
    skill.setdefault("skill_id", sid)
    u["stage"] = "training"
    u["closed_day_extra_step_date"] = local_date_for_user(u)
    u["closed_day_extra_step_count"] = int(u.get("closed_day_extra_step_count") or 0) + 1
    u["daily_skill_status"] = "voluntary_extra"
    mark_action_card_active(u)
    await save_user(u, DB_PATH)
    await bot_record_action_event(u, "attempt_started", skill_id=sid, metadata={"source": "closed_day_continue", "voluntary": True})
    await answer_with_keyboard(m, u, build_current_skill_text(skill, next_step_prefix(u, voluntary=True), u), action_keyboard(), "closed_day_voluntary_step")


async def handle_closed_day_input(m: Message, u: Dict[str, Any], text: str, low: str) -> bool:
    if not day_closed_today(u):
        return False
    if u.get("stage") == "closed_day_voluntary_step" and text in ACTION_OUTCOME_BUTTONS:
        return False
    kind = global_button_kind(text, low) if is_known_reply_button(text) or text else ""
    if u.get("stage") == "closed_day_continue_confirm":
        if text == "✅ Да, ещё один короткий шаг" or low.startswith("да"):
            await open_closed_day_voluntary_step(m, u)
            return True
        if text == "🌙 Нет, оставить день закрытым" or low.startswith("нет"):
            u["stage"] = "day_core_stop"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Ок. Оставляем день закрытым. До завтра.", kb_day_core_stop, "day_core_stop")
            return True
    if kind == "map":
        await send_user_map(m, u, "day_closed")
        return True
    if kind == "trainer_switch":
        await open_trainer_switch(m, u, "day_closed")
        return True
    if kind == "crisis":
        await start_safety_interceptor(m, u, text, "day_closed_crisis", explicit=True)
        return True
    if kind == "tomorrow":
        u["stage"] = "day_core_stop"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, DAY_ALREADY_CLOSED_TEXT, kb_day_core_stop, "day_core_stop")
        return True
    if kind in DAY_CLOSED_VOLUNTARY_ACTIONS or should_route_action_request(text, low, u) or text in {"➕ Ещё 2 минуты", "💪 Сделать следующий шаг", "💪 Давай действие", "🧭 Давай действие", "💪 Продолжить тренировку"}:
        if closed_day_extra_used_today(u):
            u["stage"] = "day_core_stop"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "День уже закрыт, и один добровольный дополнительный подход сегодня уже был. Лучше оставить день закрытым.", kb_day_core_stop, "day_core_stop")
            return True
        u["stage"] = "closed_day_continue_confirm"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, DAY_CLOSED_CONTINUE_PROMPT, kb_closed_day_continue, "closed_day_continue_confirm")
        return True
    if kind in DAY_CLOSED_BLOCKED_KINDS:
        u["stage"] = "day_core_stop"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, DAY_CLOSED_ACTION_TEXT, kb_day_core_stop, "day_core_stop")
        return True
    # Closed-day mode is intentionally narrow: do not start new branches from leftover buttons.
    u["stage"] = "day_core_stop"
    await save_user(u, DB_PATH)
    await answer_with_keyboard(m, u, DAY_ALREADY_CLOSED_TEXT, kb_day_core_stop, "day_core_stop")
    return True

async def get_honest_day_counts(u: Dict[str, Any]) -> Dict[str, int]:
    day_id = str(u.get("current_day_id") or "")
    raw_counts = {"attempt_started": 0, "stuck_reason_selected": 0, "returned_after_slip": 0, "skill_changed": 0}
    if day_id:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT event_type, COUNT(*) FROM action_events WHERE user_id=? AND day_id=? AND event_type IN ('attempt_started', 'stuck_reason_selected', 'returned_after_slip', 'skill_changed') GROUP BY event_type",
                (u["user_id"], day_id),
            )
            for event_type, count in await cur.fetchall():
                raw_counts[str(event_type)] = int(count or 0)
    return {
        "completed_actions_today": raw_counts["attempt_started"],
        "stuck_events_today": raw_counts["stuck_reason_selected"],
        "returns_today": raw_counts["returned_after_slip"],
        "skill_changes_today": raw_counts["skill_changed"],
    }


async def day_close_metrics_text(u: Dict[str, Any]) -> str:
    counts = await get_honest_day_counts(u)
    profile = await get_user_profile(u["user_id"], DB_PATH)
    skill_map = await build_skill_map_data(u, profile)
    stuck_line = f"Застреваний: {counts['stuck_events_today']}."
    return (
        f"{trainer_style_line(u.get('trainer_key') or 'marsha', 'close')}\n\n"
        f"Сегодня ты сделал: {counts['completed_actions_today']} попыток.\n"
        f"{stuck_line}\n"
        f"Вернулся после паузы: {counts['returns_today']}.\n"
        f"Сменил навык: {counts['skill_changes_today']}.\n\n"
        "Это не оценка продуктивности.\n"
        "Это данные о том, как тебе легче начинать.\n\n"
        "Статусы навыков:\n"
        f"{skill_map_lines(skill_map, 3)}\n\n"
        "До завтра. Новый навык откроется после смены календарного дня."
    )



def enough_for_today_text() -> str:
    return (
        "На сегодня достаточно.\n\n"
        "Ты уже закрыл день.\n"
        "Сейчас задача — не добивать себя ещё одним кругом, а закрепить результат.\n\n"
        "Завтра я дам новый навык."
    )


async def mark_day_closed(u: Dict[str, Any], source: str):
    today = local_date_for_user(u)
    if not u.get("current_day_id"):
        sid = current_skill_for_action(u)
        await ensure_user_day(u, DB_PATH, calendar_date=today, skill_id=sid, skill_name=(SKILLS_DB.get(sid) or {}).get("name") or sid)
    await close_user_day(u, DB_PATH)
    await bot_record_action_event(u, "day_closed", metadata={"source": source})
    u["day_closed"] = 1
    u["today_closed"] = 1
    u["last_day_closed_at"] = today
    u["day_status"] = "closed"
    mark_current_skill_status(u, "closed")
    try:
        summary_counts = await get_honest_day_counts(u)
        if summary_counts.get("completed_actions_today", 0) > 0 and not int(u.get("streak_counted_today") or 0):
            u["streak"] = int(u.get("streak") or 0) + 1
            u["streak_counted_today"] = 1
    except Exception:
        pass
    set_current_state(u, STATE_DAY_CLOSED, close_action=True)
    await record_profile_signal(
        u["user_id"],
        "training",
        {
            "day_closed": 1,
            "today_closed": 1,
            "last_day_closed_at": today,
            "day_status": "closed",
        },
        source=source,
    )


async def send_current_skill(user_id: int, message: Message, user: Optional[Dict[str, Any]] = None):
    user = user or await get_user(user_id, DB_PATH)
    profile = await get_user_profile(user_id, DB_PATH)
    pre_skill_map = await build_skill_map_data(user, profile)
    user["current_skill_status_text"] = current_skill_status_note(user, pre_skill_map)
    await remember_action_request_context(user, profile, pre_skill_map)
    if await maybe_resume_pending_stuck_validation(message, user):
        return
    if current_skill_completed_or_closed(user):
        if day_closed_today(user, profile):
            user["stage"] = "closed_day_continue_confirm"
            await save_user(user, DB_PATH)
            await answer_with_keyboard(message, user, DAY_CLOSED_CONTINUE_PROMPT, kb_closed_day_continue, "closed_day_continue_confirm")
            return
        await open_next_logical_step(message, user, source="send_current_skill_after_completion")
        return
    sid = current_skill_for_action(user)
    if not sid:
        await start_new_day(user_id, message, user, "action_request_no_current_skill")
        return
    skill = dict(SKILLS_DB[sid])
    skill.setdefault("skill_id", sid)
    user["stage"] = "training"
    user["has_started_training"] = 1
    user["today_started"] = 1
    user["current_skill"] = sid
    user["current_skill_variant"] = skill.get("variant") or sid
    user["pending_skill_id"] = sid
    user["pending_skill_day"] = int(user.get("day") or 1)
    user["daily_skill_id"] = sid
    user["daily_skill_name"] = skill.get("name") or sid
    user["daily_skill_status"] = "in_progress"
    user["success_repeat_count"] = 0
    if user.get("day_core_skill_id") != sid:
        replace_day_core_skill(user, sid)
    await ensure_user_day(user, DB_PATH, calendar_date=local_date_for_user(user), skill_id=sid, skill_name=skill.get("name") or sid)
    action_id = mark_action_card_active(user)
    await save_user(user, DB_PATH)
    await update_user_profile(
        user_id,
        {"user_model_events": [user_model_event(user_id, "intervention_offered", "", source_skill_id=sid, confidence=0.6)]},
        DB_PATH,
        source="skill_offered",
    )
    profile_for_map = await get_user_profile(user_id, DB_PATH)
    skill_map = await build_skill_map_data(user, profile_for_map)
    user["current_skill_status_text"] = current_skill_status_note(user, skill_map)
    await log_event(user_id, "training", "current_skill_resent", {"skill_id": sid, "day_id": user.get("current_day_id"), "action_id": user.get("current_action_id"), "state_version": user.get("state_version")}, DB_PATH, SHEETS_WEBHOOK_URL)
    await answer_with_keyboard(message, user, build_current_skill_text(skill, next_step_prefix(user), user), action_keyboard(), "new_day_skill")


async def handle_action_request(user_id: int, message: Message, user: Optional[Dict[str, Any]] = None, *, repeat: bool = False):
    user = user or await get_user(user_id, DB_PATH)
    profile = await get_user_profile(user_id, DB_PATH)
    skill_map = await build_skill_map_data(user, profile)
    user["current_skill_status_text"] = current_skill_status_note(user, skill_map)
    await remember_action_request_context(user, profile, skill_map, repeat=repeat)
    if await maybe_resume_pending_stuck_validation(message, user):
        return
    if day_closed_today(user, profile):
        user["stage"] = "closed_day_continue_confirm"
        await save_user(user, DB_PATH)
        await answer_with_keyboard(message, user, DAY_CLOSED_CONTINUE_PROMPT, kb_closed_day_continue, "closed_day_continue_confirm")
        return
    if current_skill_completed_or_closed(user):
        if day_closed_today(user, profile):
            user["stage"] = "closed_day_continue_confirm"
            await save_user(user, DB_PATH)
            await answer_with_keyboard(message, user, DAY_CLOSED_CONTINUE_PROMPT, kb_closed_day_continue, "closed_day_continue_confirm")
            return
        await open_next_logical_step(message, user, source="action_request_after_completion")
        return
    if not current_skill_for_action(user):
        await start_new_day(user_id, message, user, "action_request_no_current_skill")
        return
    if repeat:
        sid = current_skill_for_action(user)
        await ensure_user_day(user, DB_PATH, calendar_date=local_date_for_user(user), skill_id=sid, skill_name=(SKILLS_DB.get(sid) or {}).get("name") or sid)
        attempt_id = await create_skill_attempt(user, DB_PATH, skill_id=sid, task_id=str(user.get("current_task_id") or user.get("today_target") or ""), result="started")
        await record_return_after_slip_action_event_if_needed(user, "repeat_attempt")
        user["stage"] = "training"
        user["skill_attempts_today"] = int(user.get("skill_attempts_today") or 0) + 1
        mark_action_card_active(user)
        await save_user(user, DB_PATH)
        skill = dict(SKILLS_DB[sid])
        skill.setdefault("skill_id", sid)
        await bot_record_action_event(user, "attempt_started", skill_id=sid, metadata={"source": "repeat_attempt", "attempt_id": attempt_id})
        await log_event(user_id, "training", "attempt_started", {"attempt_id": attempt_id, "day_id": user.get("current_day_id"), "skill_id": sid, "action_id": user.get("current_action_id"), "state_version": user.get("state_version")}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(message, user, build_current_skill_text(skill, next_step_prefix(user, repeat=True), user), action_keyboard(), "new_day_skill")
        return
    await send_current_skill(user_id, message, user)


ACTION_REQUEST_LABELS = {"💪 Давай действие", "🧭 Давай действие", "💪 Дать сегодняшний навык", "💪 Сделать следующий шаг", "💪 Дать следующий шаг", "💪 Продолжить тренировку", "🔁 Ещё круг"}


def is_action_request(text: str, low: str) -> bool:
    return (
        text in ACTION_REQUEST_LABELS
        or "давай действие" in low
        or "сделать следующий шаг" in low
        or "продолжить тренировку" in low
        or "дать следующий шаг" in low
        or "дать сегодняшний навык" in low
        or "ещё круг" in low
        or "еще круг" in low
    )


def should_route_action_request(text: str, low: str, u: Dict[str, Any]) -> bool:
    return is_action_request(text, low)


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
        "payment_click_month_1498": 0,
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
            elif name in {"payment_click_20", "payment_click_month_1498"}:
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
        f"€14.98 clicks: {event_counts['payment_click_month_1498']}",
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
    """Fully erase one user's durable run state and recreate a clean profile.

    This is intentionally stronger than overwriting the users row: tester resets
    must not leave old maps, skill outcomes, helpfulness signals, tasks, action
    history, feedback, paid/test flags, streaks, or points in side tables.
    """
    fresh = default_user(uid)
    fresh["chat_id"] = chat_id
    # Keep reset as a true pre-start state: /start must rebuild onboarding,
    # not resume a stale/default skill card.
    fresh["stage"] = "start"
    fresh["current_step"] = "start"
    # A profile reset must not preserve or re-grant per-user QA/payment state,
    # even when the bot process itself runs with TEST_MODE enabled.
    fresh["trial_phase"] = "trial3"
    fresh["payment_status"] = "trial"
    fresh["access_status"] = "trial"
    fresh["paid_until"] = None
    fresh["is_test_user"] = 0
    fresh["fast_forward_enabled"] = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for table in (
            "events",
            "user_days",
            "skill_attempts",
            "action_events",
            "user_feedback",
            "user_tasks",
            "user_sessions",
        ):
            await db.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
        await db.commit()
    await save_user(fresh, DB_PATH)
    return fresh


async def recent_user_events_text(user_id: int, limit: int = 20) -> str:
    """Render a compact admin-only tail of the event log for QA."""
    rows = []
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                event_name TEXT,
                event_data TEXT,
                stage TEXT,
                created_at TEXT,
                event TEXT,
                meta TEXT
            )
            """
        )
        cur = await db.execute(
            """
            SELECT created_at, COALESCE(event_name, event), COALESCE(stage, ''), COALESCE(event_data, meta, '{}')
            FROM events
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = await cur.fetchall()
    if not rows:
        return "DEBUG EVENTS\nпоследних событий пока нет"
    lines = ["DEBUG EVENTS", f"last: {len(rows)}"]
    for created_at, event_name, stage, event_data in rows:
        meta = str(event_data or "{}")
        if len(meta) > 160:
            meta = meta[:157] + "..."
        lines.append(f"- {created_at or '-'} | {stage or '-'} | {event_name or '-'} | {meta}")
    return "\n".join(lines)


async def recent_user_feedback_text(user_id: int, limit: int = 20) -> str:
    rows = await recent_user_feedback(user_id, DB_PATH, limit=limit)
    if not rows:
        return "DEBUG FEEDBACK\nответов тестирования пока нет"
    labels = {
        "feedback_instruction_clarity": "понятность первого навыка",
        "feedback_validation": "ощущение понимания ситуации",
        "feedback_day_value": "полезная механика дня",
        "product_value_score": "жалко потерять 0–10",
        "product_value_reason": "раздражающие/ценные точки",
        "offer_feedback": "причина отказа от полного режима",
    }
    lines = ["DEBUG FEEDBACK", f"last: {len(rows)}"]
    for item in rows:
        label = labels.get(item.get("feedback_type"), item.get("feedback_type") or "-")
        comment = item.get("comment") or ""
        if len(comment) > 120:
            comment = comment[:117] + "..."
        lines.append(
            f"- {item.get('created_at') or '-'} | day {item.get('day_number') or '-'} | {label}: {item.get('value') or '-'}"
            f"{(' | ' + comment) if comment else ''}"
        )
    return "\n".join(lines)


def debug_state_text(u: Dict[str, Any]) -> str:
    """Render the minimum QA state requested before manual testing."""
    sid = current_skill_id(u)
    return (
        "DEBUG STATE\n"
        f"FSM-state: {u.get('stage') or '-'}\n"
        f"day_id: {u.get('current_day_id') or '-'}\n"
        f"current_task: {current_task_title(u, '-')}\n"
        f"current_skill: {sid or u.get('daily_skill_id') or '-'}\n"
        f"safety_mode: {safety_mode(u)}\n"
        f"full_mode: {int(u.get('full_mode') or 0)}"
    )


async def activate_test_cheat(m: Message, u: Dict[str, Any], source: str, days: int = 30):
    days = days if days in {7, 14, 30} else 30
    u["is_test_user"] = 1
    u["fast_forward_enabled"] = 1
    u["free_mode"] = 0
    u["payment_status"] = "test"
    u["trial_phase"] = "paid"
    u["paid_until"] = paid_access_until(days, u.get("paid_until"))
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], u.get("stage", ""), "test_cheat_activated", {"source": source, "days": days}, DB_PATH, SHEETS_WEBHOOK_URL)
    await m.answer(
        f"Тестовый режим включён на {days} дней для этого пользователя.\n\n"
        "Доступно:\n"
        "/force_next_day — перейти на следующий день\n"
        "/set_day 3 — прыгнуть на день 3\n"
        "/show_offer — показать offer вручную\n"
        "/debug_state\n"
        "/debug_events\n"
        "/debug_feedback\n"
        "/reset_test_user\n"
        "/simulate_payment\n"
        "/testmode_off\n\n"
        "Админские команды оплаты, статистики и синка по-прежнему закрыты."
    )


async def handle_user_command(m: Message, u: Dict[str, Any], text: str) -> bool:
    """Handle simple user commands; does not require admin access."""
    if not text or not text.startswith("/"):
        return False
    uid = m.from_user.id
    command = text.split(maxsplit=1)[0].lower()

    if command == "/confirm_payment":
        if PAYMENT_ACCEPT_ANY:
            await grant_paid_access(u, "test_confirm_command", {"accept_any_payment": True})
            await send_full_mode_welcome(m, u)
        else:
            await m.answer("Автоподтверждение оплаты выключено. Нужен PAYMENT_ACCEPT_ANY=1 или админская /mark_paid.")
        return True

    if command == "/show_offer":
        if not qa_command_allowed(uid, u):
            await m.answer("Команда доступна в QA-режиме. Отправь /test_access <код> или попроси добавить твой Telegram ID в ADMIN_IDS.")
            return True
        await log_event(uid, u.get("stage", ""), "show_offer_command", {"source": "user_command"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await show_day3_offer(m, u, "manual_command")
        return True

    if command == "/test_access":
        code = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) == 2 else ""
        if TEST_CHEAT_CODE and code == TEST_CHEAT_CODE:
            await activate_test_cheat(m, u, "command")
        else:
            await log_event(uid, u.get("stage", ""), "test_cheat_failed", {"source": "command"}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Код не подошёл.")
        return True

    if command == "/testmode_on":
        parts = text.split()
        code = parts[1].strip() if len(parts) >= 2 else ""
        days = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() and int(parts[2]) in {7, 14, 30} else 30
        if code and code.lower() in {TEST_CHEAT_CODE.lower(), "skiller_test"}:
            await activate_test_cheat(m, u, "testmode_on_code", days)
        else:
            await log_event(uid, u.get("stage", ""), "testmode_on_failed", {"source": "user_command"}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Код не подошёл. Формат: /testmode_on КОД [7|14|30]")
        return True

    if command == "/testmode_off":
        u["is_test_user"] = 0
        u["fast_forward_enabled"] = 0
        if u.get("payment_status") == "test":
            u["payment_status"] = "trial"
            u["paid_until"] = None
        await save_user(u, DB_PATH)
        await log_event(uid, u.get("stage", ""), "testmode_off", {"source": "user_command"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer("Тестовый режим выключен.")
        return True

    if command in {"/my_card", "/card"}:
        profile = await get_user_profile(uid, DB_PATH)
        await log_event(uid, u.get("stage", ""), "my_card_requested", {"source": "user_command"}, DB_PATH, SHEETS_WEBHOOK_URL)
        set_last_explanation_context(u, "map", "Моя карта", "Карта собирается из действий, срывов, кризисных обращений и рабочих навыков.", ["это гипотезы, не диагноз", "срыв = информация, не наказание"], "Проверь, что из карты похоже на правду.")
        await save_user(u, DB_PATH)
        await m.answer(trainer_wrap(u, render_short_user_map(profile, u.get("name")), "map"))
        return True

    if command == "/help":
        await log_event(uid, u.get("stage", ""), "help_requested", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(user_help_text())
        return True

    if command == "/progress":
        await m.answer("Пока прогресс-цифры скрыты до проверки точности. Ложные цифры хуже отсутствия цифр. Итоги покажу при закрытии дня.")
        return True

    if command == "/mirror":
        profile = await get_user_profile(uid, DB_PATH)
        await log_event(uid, u.get("stage", ""), "development_mirror_requested", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(render_development_mirror_reports(profile))
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

    if command == "/reset_me":
        await reset_current_user(uid, m.chat.id)
        await m.answer("Профиль полностью сброшен для нового прогона. Напиши /start.")
        return True

    if command == "/start_over":
        await reset_current_user(uid, m.chat.id)
        await m.answer(
            start_over_confirm_text(),
            reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True),
        )
        return True

    return False


async def handle_admin_command(m: Message, u: Dict[str, Any], text: str) -> bool:
    """Handle admin-only test commands. Returns True when command was consumed."""
    uid = m.from_user.id
    command = (text.split(maxsplit=1)[0] if text else "").lower()
    qa_commands = {
        "/debug_state", "/debug_events", "/show_offer", "/simulate_payment", "/reset_test_user",
        "/testmode_on", "/testmode_off", "/set_day", "/force_next_day", "/debug_map", "/debug_user",
        "/debug_feedback", "/whoami", "/health", "/payment_status",
    }
    admin_only_commands = {
        "/reset", "/test_payment", "/mark_paid", "/mark_free", "/grant_full", "/revoke_full",
        "/simulate_paid", "/simulate_unpaid", "/sync_sheets", "/stats",
    }
    if command not in qa_commands and command not in admin_only_commands:
        return False
    if command == "/testmode_on" and not qa_command_allowed(uid, u):
        # Let the user-command handler validate /testmode_on <code> for non-admin testers.
        return False
    if command in qa_commands and not qa_command_allowed(uid, u):
        await m.answer("QA-команда недоступна. Отправь /test_access <код> или добавь свой Telegram ID в ADMIN_IDS.")
        return True
    if command in admin_only_commands and not is_admin(uid):
        await m.answer("Админская команда недоступна для этого пользователя.")
        return True

    if command == "/debug_state":
        await m.answer(debug_state_text(u))
        return True

    if command == "/debug_events":
        await m.answer(await recent_user_events_text(uid, 20))
        return True

    if command == "/debug_feedback":
        await m.answer(await recent_user_feedback_text(uid, 20))
        return True

    if command == "/show_offer":
        await log_event(uid, u.get("stage", ""), "show_offer_command", {"source": "admin_command"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await show_day3_offer(m, u, "admin_command")
        return True

    if command == "/simulate_payment":
        u["is_test_user"] = 1
        u["fast_forward_enabled"] = 1
        await grant_paid_access(u, "admin_simulate_payment", {"days": 30, "test_user_only": True})
        await send_full_mode_welcome(m, u)
        return True

    if command == "/reset_test_user":
        if not (TEST_MODE or int(u.get("is_test_user") or 0) == 1 or int(u.get("fast_forward_enabled") or 0) == 1):
            await m.answer("Сначала включи тестовый режим для этого пользователя: /testmode_on 30. Боевые данные не трогаю.")
            return True
        await reset_current_user(uid, m.chat.id)
        await m.answer("Тестовые данные этого пользователя очищены. Боевые пользователи не затронуты. Напиши /start.")
        return True

    if command == "/debug_state":
        await m.answer(debug_state_text(u))
        return True

    if command == "/debug_events":
        await m.answer(await recent_user_events_text(uid, 20))
        return True

    if command == "/show_offer":
        await log_event(uid, u.get("stage", ""), "show_offer_command", {"source": "admin_command"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await show_day3_offer(m, u, "admin_command")
        return True

    if command == "/simulate_payment":
        u["is_test_user"] = 1
        u["fast_forward_enabled"] = 1
        await grant_paid_access(u, "admin_simulate_payment", {"days": 30, "test_user_only": True})
        await send_full_mode_welcome(m, u)
        return True

    if command == "/reset_test_user":
        if not (TEST_MODE or int(u.get("is_test_user") or 0) == 1 or int(u.get("fast_forward_enabled") or 0) == 1):
            await m.answer("Сначала включи тестовый режим для этого пользователя: /testmode_on 30. Боевые данные не трогаю.")
            return True
        await reset_current_user(uid, m.chat.id)
        await m.answer("Тестовые данные этого пользователя очищены. Боевые пользователи не затронуты. Напиши /start.")
        return True

    if command == "/testmode_on":
        parts = text.split()
        days = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) in {7, 14, 30} else 30
        u["is_test_user"] = 1
        u["fast_forward_enabled"] = 1
        u["payment_status"] = "test"
        u["trial_phase"] = "paid"
        u["free_mode"] = 0
        u["paid_until"] = paid_access_until(days, u.get("paid_until"))
        await save_user(u, DB_PATH)
        await log_event(uid, u.get("stage", ""), "testmode_on", {"days": days}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(f"""Тестовый режим включён на {days} дней.
Можно проходить дни без ожидания.

Команды:
/set_day 3
/force_next_day
/show_offer
/debug_user
/reset_me""")
        return True

    if command == "/testmode_off":
        u["is_test_user"] = 0
        u["fast_forward_enabled"] = 0
        if u.get("payment_status") == "test":
            u["payment_status"] = "trial"
            u["paid_until"] = None
        await save_user(u, DB_PATH)
        await log_event(uid, u.get("stage", ""), "testmode_off", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer("Тестовый режим выключен.")
        return True

    if command == "/set_day":
        parts = text.split()
        if len(parts) != 2 or not parts[1].isdigit() or not (1 <= int(parts[1]) <= 28):
            await m.answer("Формат: /set_day 1..28")
            return True
        day = int(parts[1])
        u["day"] = day
        if "current_day" in u:
            u["current_day"] = day
        u["pending_skill_day"] = None
        clear_day_core_lock(u)
        u["day_closed"] = 0
        u["today_closed"] = 0
        u["last_day_closed_at"] = None
        u["day_status"] = "open"
        u["stage"] = "waiting_next_day"
        await update_user_profile(uid, {"day_closed": 0, "today_closed": 0, "last_day_closed_at": None, "day_status": "open"}, DB_PATH, source="admin_set_day")
        await save_user(u, DB_PATH)
        await log_event(uid, "training", "admin_set_day", {"day": day}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(f"День установлен: {day}. Открываю новый навык дня.")
        await open_new_day_skill(m, u, day, "admin_set_day")
        return True

    if command == "/force_next_day":
        current_day = int(u.get("day") or u.get("current_day") or 1)
        next_day = min(current_day + 1, 28)
        if not u.get("current_day_id"):
            sid = current_skill_for_action(u)
            await ensure_user_day(u, DB_PATH, calendar_date=local_date_for_user(u), skill_id=sid, skill_name=(SKILLS_DB.get(sid) or {}).get("name") or sid)
        if not day_closed_today(u):
            await mark_day_closed(u, "admin_force_next_day_close_previous")
        old_day_id = u.get("current_day_id")
        u["day"] = next_day
        if "current_day" in u:
            u["current_day"] = next_day
        u["current_day_id"] = None
        u["pending_skill_day"] = None
        clear_day_core_lock(u)
        u["day_closed"] = 0
        u["today_closed"] = 0
        u["last_day_closed_at"] = None
        u["day_status"] = "open"
        u["today_started"] = 1
        u["daily_replacement_count"] = 0
        u["replacements_today"] = 0
        u["current_skill_completed_count"] = 0
        u["skill_attempts_today"] = 0
        # Keep current_task_* across test day transitions; only clear the transient daily target.
        u["today_target"] = u.get("current_task_title") or None
        u["current_task"] = None
        u["stage"] = "waiting_next_day"
        await update_user_profile(uid, {"day_closed": 0, "today_closed": 0, "last_day_closed_at": None, "day_status": "open"}, DB_PATH, source="admin_force_next_day")
        await save_user(u, DB_PATH)
        await log_event(uid, "training", "admin_force_next_day", {"from_day": current_day, "day": next_day, "old_day_id": old_day_id}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(f"Тестовый переход выполнен. Открыт День {next_day}.")
        await start_new_day(uid, m, u, "admin_force_next_day")
        return True

    if command == "/test_payment":
        await log_event(uid, "admin", "test_payment_opened", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer("Тестовая оплата", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🧪 Тестовая оплата", url=PAYMENT_TEST_URL or payment_month_url())]]))
        return True

    if command == "/reset":
        await reset_current_user(uid, m.chat.id)
        await m.answer("Твой тестовый профиль полностью сброшен. Напиши /start.")
        return True

    if command in {"/debug_map", "/debug_user"}:
        sid = current_skill_id(u)
        username = getattr(m.from_user, "username", None) or ""
        plan = get_current_plan(u)
        await m.answer(
            "DEBUG USER\n"
            f"telegram_id: {uid}\n"
            f"user_id: {uid}\n"
            f"username: @{username if username else '-'}\n"
            f"day: {u.get('day')}\n"
            f"current_state: {u.get('current_state')}\n"
            f"stage: {u.get('stage')}\n"
            f"plan: {plan}\n"
            f"is_testmode: {str(bool(int(u.get('is_test_user') or 0))).lower()}\n"
            f"is_paid: {str(bool(is_paid(u))).lower()}\n"
            f"subscription_until: {u.get('paid_until')}\n"
            f"paid_until: {u.get('paid_until')}\n"
            f"daily_skill_done: {u.get('daily_skill_done')}\n"
            f"current_skill: {sid}\n"
            f"selected_trainer: {u.get('trainer_key')}\n"
            f"trainer_key: {u.get('trainer_key')}\n"
            f"last_explanation_context: {u.get('last_explanation_context')}\n"
            f"last_payment_status: {u.get('payment_status')}\n"
            f"payment_status: {u.get('payment_status')}\n"
            f"trial_phase: {u.get('trial_phase')}\n"
            f"free_mode: {u.get('free_mode')}\n"
            f"fast_forward_enabled: {u.get('fast_forward_enabled')}\n"
            f"last_payment_click: {u.get('last_payment_click')}\n"
            f"last_offer_shown_at: {u.get('last_offer_shown_at')}\n"
            f"last_active: {u.get('last_active')}\n"
            f"profile_json_present: {str(bool(u.get('profile_json'))).lower()}\n"
            "profile_json: <hidden; contains internal profile_prompt>"
        )
        return True

    if command == "/whoami":
        username = getattr(m.from_user, "username", None) or "-"
        first_name = getattr(m.from_user, "first_name", None) or "-"
        await m.answer(
            f"user_id: {uid}\n"
            f"username: {username}\n"
            f"first_name: {first_name}"
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
            f"Payments configured {str(bool(ENABLE_PAYMENTS or PAYMENT_MONTH_URL or PAYMENT_URL_MONTH_1498 or PAYMENT_URL_FULL or PAYMENT_URL)).lower()}\n"
            f"Payment test url configured {str(bool(PAYMENT_TEST_URL)).lower()}\n"
            f"Payment accept any {str(bool(PAYMENT_ACCEPT_ANY)).lower()}\n"
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
        await grant_paid_access(target, "admin_mark_paid", {"admin_id": uid, "amount": 14.98})
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

    if command in {"/grant_full", "/simulate_paid"}:
        days = 30
        parts = text.split()
        if command == "/grant_full" and len(parts) >= 2 and parts[1].isdigit():
            days = int(parts[1])
        u["payment_status"] = "paid"
        u["trial_phase"] = "paid"
        u["free_mode"] = 0
        u["paid_until"] = paid_access_until(days, u.get("paid_until"))
        await save_user(u, DB_PATH)
        await log_event(uid, "admin", "full_access_granted", {"days": days, "command": command}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(f"Полный режим включён на {days} дней.")
        return True

    if command in {"/revoke_full", "/simulate_unpaid"}:
        u["payment_status"] = "free_mode"
        u["trial_phase"] = "trial3"
        u["paid_until"] = None
        u["free_mode"] = 1
        await save_user(u, DB_PATH)
        await log_event(uid, "admin", "full_access_revoked", {"command": command}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer("Полный режим выключен.")
        return True

    if command == "/payment_status":
        await m.answer(
            "PAYMENT STATUS\n"
            f"payment_status: {u.get('payment_status')}\n"
            f"trial_phase: {u.get('trial_phase')}\n"
            f"is_test_user: {u.get('is_test_user')}\n"
            f"paid_until: {u.get('paid_until')}\n"
            f"has_full_access: {str(is_paid(u)).lower()}"
        )
        return True

    return False


def user_timezone(u: Dict[str, Any]):
    try:
        return ZoneInfo(u.get("timezone") or "Europe/Vilnius")
    except Exception:
        return ZoneInfo("Europe/Vilnius")


def local_now_for_user(u: Dict[str, Any]) -> dt.datetime:
    return dt.datetime.now(user_timezone(u))


def time_of_day_greeting(u: Dict[str, Any]) -> str:
    hour = local_now_for_user(u).hour
    if 5 <= hour < 12:
        return "Доброе утро"
    if 12 <= hour < 18:
        return "Добрый день"
    return "Добрый вечер"


def time_of_day_greeting_with_name(u: Dict[str, Any]) -> str:
    name = str(u.get("name") or "").strip()
    if name and name.lower() != "пропустить":
        return f"{time_of_day_greeting(u)}, {name}."
    return f"{time_of_day_greeting(u)}."


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


MORNING_STATE_SKILL_MAP = {
    "📱 Залипаю": {
        "skill_id": "phone_far_3min",
        "avoidance_trigger": "уход в быстрый дофамин",
        "avoidance_pattern": "dopamine_avoidance",
        "attention_pattern": "scroll_autopilot",
        "message": "Ок. Сегодня берём не силу воли, а среду: меньше быстрых крючков.",
    },
    "🚪 Не могу начать": {
        "skill_id": "open_only",
        "avoidance_trigger": "не получается войти в задачу",
        "avoidance_pattern": "start_avoidance",
        "preferred_activation": "small_visible_step",
        "message": "Ок. Сегодня не разгоняем мотивацию. Снижаем цену входа.",
    },
    "😵 Нет сил": {
        "skill_id": "body_before_task",
        "avoidance_trigger": "мало энергии на вход",
        "avoidance_pattern": "low_energy",
        "energy_pattern": "low_start_energy",
        "message": "Ок. Сегодня сначала тело и ресурс, потом задача.",
    },
    "🌀 Всё слишком большое": {
        "skill_id": "task_naming",
        "avoidance_trigger": "задача кажется слишком большой",
        "avoidance_pattern": "entry_too_large",
        "downscale_pattern": "needs_smaller_step",
        "message": "Ок. Сегодня режем туман до одного маленького входа.",
    },
    "😬 Тревога": {
        "skill_id": "check_the_facts_light",
        "avoidance_trigger": "тревога перед входом",
        "avoidance_pattern": "anxiety_avoidance",
        "emotional_trigger": "anxiety",
        "message": "Ок. Сегодня не спорим с тревогой. Проверяем факт и делаем малый шаг.",
    },
}

LEGACY_MORNING_STATE_ALIASES = {
    "😐 норм": "🚪 Не могу начать",
    "😣 тяжело": "🌀 Всё слишком большое",
    "🔋 нет сил": "😵 Нет сил",
    "📱 отвлекаюсь": "📱 Залипаю",
    "🚪 не хочу начинать": "🚪 Не могу начать",
}


def morning_state_config(text: str) -> tuple[str, Dict[str, Any]]:
    state = LEGACY_MORNING_STATE_ALIASES.get(text, text)
    return state, MORNING_STATE_SKILL_MAP.get(state, MORNING_STATE_SKILL_MAP["🚪 Не могу начать"])


def apply_morning_skill_choice(u: Dict[str, Any], state: str, config: Dict[str, Any]):
    skill_id = config.get("skill_id") or "open_only"
    if skill_id not in SKILLS_DB:
        skill_id = "open_only" if "open_only" in SKILLS_DB else next(iter(SKILLS_DB.keys()))
    day = int(u.get("day") or 1)
    if has_stale_day_core_lock(u):
        clear_day_core_lock(u)
    if day_core_test_mode_enabled(u) or not (u.get("day_core_skill_id") in SKILLS_DB and u.get("day_core_skill_date") == local_date_for_user(u)):
        propose_plan_override(u, day, skill_id)
        u["pending_skill_id"] = skill_id
        u["pending_skill_day"] = day
    remember_checkin_state(u, "last_morning_state", state)
    remember_checkin_state(u, "last_morning_state_date", local_date_for_user(u))
    return skill_id


def detects_body_doubling_signal(text: str) -> bool:
    low = (text or "").lower()
    triggers = (
        "рядом с человеком",
        "рядом с кем",
        "рядом кто",
        "с человеком легче",
        "когда кто-то рядом",
        "если кто-то рядом",
        "в коворкинге",
        "коворкинг",
        "на созвоне",
        "созвон",
        "в звонке",
        "в дискорде",
        "в зуме",
        "zoom",
        "body doubling",
        "боди даблинг",
        "ощущение присутствия",
        "присутствие другого",
    )
    return any(t in low for t in triggers)


async def ask_today_action(m: Message, u: Dict[str, Any]):
    day = sync_calendar_day(u)
    await save_user(u, DB_PATH)
    if day > 3 and not is_paid(u) and int(u.get("free_mode") or 0) != 1 and not day_core_test_mode_enabled(u) and not TEST_MODE and not offer_shown_today(u):
        await log_event(u["user_id"], "offer", "paywall_after_trial", {"day": day}, DB_PATH, SHEETS_WEBHOOK_URL)
        await show_day3_offer(m, u, "paywall_after_trial")
        return

    today = local_date_for_user(u)
    analysis_data: Dict[str, Any] = {}
    try:
        analysis_data = json.loads(u.get("analysis_json") or "{}")
        if not isinstance(analysis_data, dict):
            analysis_data = {}
    except Exception:
        analysis_data = {}
    if analysis_data.get("last_morning_state_date") != today and not day_core_test_mode_enabled(u):
        u["stage"] = "morning_checkin"
        u["last_morning_checkin_date"] = today
        await save_user(u, DB_PATH)
        await log_event(u["user_id"], "morning_checkin", "morning_checkin_sent", {"source": "before_day_start"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(m, u, morning_checkin_text(u.get("name") or "друг"), kb_morning_checkin, "morning_checkin")
        return
    u["stage"] = "waiting_next_day"
    await save_user(u, DB_PATH)
    await start_day(m, u, int(u.get("day") or 1), DB_PATH, SHEETS_WEBHOOK_URL)


FAILED_REASON_SKILL_MAP = {
    "failed_too_hard": {
        "skill_ids": ["task_naming", "open_only", "visible_next_step"],
        "trigger": "шаг слишком большой",
        "avoidance_pattern": "entry_too_large",
        "downscale_pattern": "needs_smaller_step",
        "intro": "Ок. Значит, шаг был слишком большой. Не давим. Уменьшаем вход.",
    },
    "failed_no_energy": {
        "skill_ids": ["body_before_task", "minimum_viable_day", "open_only"],
        "trigger": "нет ресурса на вход",
        "avoidance_pattern": "low_energy",
        "downscale_pattern": "needs_resource_step",
        "energy_pattern": "low_start_energy",
        "intro": "Ок. Это не про дисциплину. Сначала ресурс, потом задача.",
    },
    "failed_stuck_phone": {
        "skill_ids": ["phone_far_3min", "one_tab_focus", "visible_next_step"],
        "trigger": "уход в быстрый дофамин",
        "avoidance_pattern": "dopamine_avoidance",
        "attention_pattern": "scroll_autopilot",
        "downscale_pattern": "needs_stimulus_control",
        "intro": (
            "Ок. Это залипание, не лень.\n\n"
            "🧭 Добавляю в карту:\n"
            "— при напряжении мозг уходит в быстрый дофамин\n"
            "— доступность телефона усиливает срыв\n"
            "— сначала нужен контроль среды, потом задача\n\n"
            "Сейчас не работаем над всей задачей.\n"
            "Только возвращаем 5% контроля."
        ),
    },
}


def failed_reason_skill(reason: str) -> tuple[str, Dict[str, Any]]:
    config = FAILED_REASON_SKILL_MAP.get(reason) or FAILED_REASON_SKILL_MAP["failed_too_hard"]
    for skill_id in config["skill_ids"]:
        if skill_id in SKILLS_DB:
            return skill_id, config
    return DOWNSCALE_PRIMARY_SKILL, config


def failed_reason_explanation(reason: str, u: Dict[str, Any]) -> str:
    target = current_task_label(u)
    if reason == "failed_stuck_phone":
        return (
            "Ок. Логика простая.\n\n"
            "Ты не обязан запрещать себе скролл.\n"
            "Сейчас тренируем момент ДО автопилота.\n\n"
            "Пример:\n"
            f"задача — {target}.\n"
            "Рука тянется к ленте → ты ловишь этот момент → убираешь телефон на 3 минуты или оставляешь одно окно.\n\n"
            "Физический шаг: положи телефон дальше руки или закрой лишние вкладки."
        )
    if reason == "failed_no_energy":
        return (
            "Ок. Логика простая.\n\n"
            "Если энергии нет, задача не уменьшается силой воли.\n"
            "Сначала снижаем физиологический перегруз.\n\n"
            "Пример:\n"
            f"задача — {target}.\n"
            "Перед входом: вода, плечи вниз, один выдох, 30 секунд контакта с местом задачи.\n\n"
            "Физический шаг: поставь воду рядом или встань на 20 секунд."
        )
    return (
        "Ок. Логика простая.\n\n"
        "Если шаг не сделался — он был слишком крупный или мутный.\n"
        "Мы не спорим с этим. Режем до физического действия.\n\n"
        "Пример:\n"
        f"задача — {target}.\n"
        "Не “поработать”, а назвать задачу одним словом, открыть место или оставить следующий шаг видимым.\n\n"
        "Физический шаг: напиши одно слово про задачу или открой место задачи без работы."
    )

async def send_downscale(m: Message, u: Dict[str, Any], reason: str):
    """Показать причино-специфичный маленький action-step внутри текущего loop."""
    profile = await get_user_profile(u["user_id"], DB_PATH)
    if is_free_after_day3(u) and int(profile.get("downscale_count_today") or 0) >= FREE_AFTER_DAY_3["downscales_per_day"]:
        await log_event(u["user_id"], "training", "free_downscale_limit_reached", {"reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(
            m,
            u,
            "На сегодня в коротком режиме достаточно.\n\n"
            "Ты уже сделал базовую тренировку.\n"
            "Сейчас лучше не собирать новые техники, а закрепить один вход.\n\n"
            "Можно:\n"
            "— закрыть день\n"
            "— посмотреть карту\n"
            "— продолжить в полном режиме",
            ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="🌙 Закрыть день")],
                    [KeyboardButton(text="🧭 Моя карта")],
                    [KeyboardButton(text="💳 Полный режим")],
                ],
                resize_keyboard=True,
            ),
            "waiting_next_day",
        )
        return
    skill_id, config = failed_reason_skill(reason)
    skill = dict(SKILLS_DB.get(skill_id) or SKILLS_DB[DOWNSCALE_PRIMARY_SKILL])
    skill.setdefault("skill_id", skill_id)
    u["skill_variant_label"] = "Если залип" if reason == "failed_stuck_phone" else "Упрощение"

    # Keep the current core skill fixed; this is only a smaller in-day response.
    u["pending_skill_id"] = None
    u["pending_skill_day"] = None
    _remember_downscale_pattern(u, skill_id)
    u["stage"] = "downscale_action"
    await save_user(u, DB_PATH)

    downscale_count = int(profile.get("downscale_count") or 0) + 1
    signal_patch = {
        "avoidance_pattern": config.get("avoidance_pattern"),
        "avoidance_trigger": config.get("trigger"),
        "attention_pattern": config.get("attention_pattern") or profile.get("attention_pattern"),
        "downscale_pattern": config.get("downscale_pattern"),
        "energy_pattern": config.get("energy_pattern") or profile.get("energy_pattern") or "unknown",
        "next_skill_hint": skill_id,
        "best_variant": skill_id,
        "attention_escape_count": int(profile.get("attention_escape_count") or 0) + (1 if reason == "failed_stuck_phone" else 0),
        "energy_signal": config.get("energy_pattern") or profile.get("energy_signal"),
        "downscale_count": downscale_count,
        **_today_profile_counter_patch(profile, "downscale_count_today", "downscale_count_date"),
    }
    await record_profile_signal(u["user_id"], "training", signal_patch, source=f"downscale_{reason}")
    await record_development_avatar_event(u["user_id"], "downscale", DB_PATH, {"reason": reason, "skill_id": skill_id})
    await bot_record_action_event(u, "step_reduced", skill_id=skill_id, metadata={"reason": reason})
    await log_event(
        u["user_id"],
        "training",
        "failed_reason_selected",
        {"reason": reason, "skill_id": skill_id, "avoidance_trigger": config.get("trigger")},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    await log_event(
        u["user_id"],
        "training",
        "downscale_triggered",
        {"reason": reason, "skill": skill_id},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )

    text = (
        f"{config.get('intro')}\n\n"
        f"{format_skill_card(u, skill, current_task_label(u))}"
    )
    mark_action_card_active(u)
    await save_user(u, DB_PATH)
    await answer_with_keyboard(m, u, text, action_keyboard(), "downscale")




def global_button_kind(text: str, low: str) -> str:
    if text in {"🧭 Моя карта", "📖 Полная карта"} or "моя карта" in low or "полная карта" in low:
        return "map"
    if text in {"💪 Давай действие", "🧭 Давай действие", "💪 Дать сегодняшний навык", "💪 Дать следующий шаг", "💪 Начать тренировку", "💪 Продолжить тренировку", "💪 Сделать следующий шаг"} or "давай действие" in low or "дать сегодняшний навык" in low or "дать следующий шаг" in low or "начать тренировку" in low:
        return "action"
    if text in {"🌙 Хватит на сегодня", "🌙 На сегодня хватит"} or "хватит" in low:
        return "enough"
    if text in {"🌙 Закрыть день", "✅ Закрыть день"} or "закрыть день" in low:
        return "close_day"
    if text == "🌙 До завтра" or "до завтра" in low:
        return "tomorrow"
    if text == "🔁 Ещё круг" or "ещё круг" in low or "еще круг" in low:
        return "repeat"
    if text in {"🔄 Сменить навык", "🔄 Выбрать другой навык", "🤷 Не моё"} or "сменить навык" in low or "выбрать другой навык" in low or low == "не моё":
        return "change_skill"
    if text == "🔁 Другой навык" or "другой навык" in low:
        return "other_skill"
    if text in {"🎭 Сменить тренера", "🔄 Сменить тренера"} or "сменить тренера" in low:
        return "trainer_switch"
    if text in {"🟡 Застрял / не вышло", "🟡 Не вышло", "🆘 Кризис прокрастинации"} or "застрял" in low or "не вышло" in low or "кризис прокрастинации" in low:
        return "stuck"
    if text in {"⚡ Я застрял", "⚡ Я уже застрял"} or "я застрял" in low:
        return "stuck"
    if text in {"Ещё", "Еще"} or low in {"ещё", "еще"}:
        return "more"
    if text == "📚 Почему это работает" or "почему это работает" in low:
        return "why"
    if text == "🧠 Почему этот навык" or "почему этот навык" in low:
        return "why_skill"
    if text in {"📚 Подробнее", "🤔 Зачем это?"} or low == "подробнее":
        return "details"
    if text == "Пропустить" or low == "пропустить":
        return "skip"
    return ""


async def close_day_from_global_button(m: Message, u: Dict[str, Any], source: str):
    if day_closed_today(u):
        u["stage"] = "day_core_stop"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, DAY_ALREADY_CLOSED_TEXT, kb_day_core_stop, "day_core_stop")
        return
    current_day = sync_calendar_day(u)
    await mark_day_closed(u, source)
    u["stage"] = "day_core_stop"
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "training", "day_closed_global_button", {"day": current_day, "day_id": u.get("current_day_id"), "source": source}, DB_PATH, SHEETS_WEBHOOK_URL)
    if await ask_product_value_feedback(m, u):
        return
    if await ask_day_value_feedback(m, u):
        return
    await answer_with_keyboard(m, u, await day_close_metrics_text(u), kb_day_core_stop, "day_core_stop")



async def handle_full_mode_buttons(m: Message, u: Dict[str, Any], text: str) -> bool:
    if not int(u.get("full_mode") or 0):
        return False
    if text == "Продолжить":
        if not str(u.get("current_task_title") or u.get("today_target") or "").strip() or str(u.get("today_target") or "") == "__target_not_selected__":
            u["stage"] = "await_training_target"
            await save_user(u, DB_PATH)
            await m.answer("На какой одной задаче сегодня проверим новый шаг?")
            return True
        await handle_action_request(u["user_id"], m, u)
        return True
    if text not in {"📩 Скопировать текст", "✅ Отправил(а)", "↩️ Хочу другой шаг"}:
        return False
    try:
        plan = json.loads(u.get("full_mode_plan_json") or "{}")
    except Exception:
        plan = {}
    if not plan:
        profile = await get_user_profile(u["user_id"], DB_PATH)
        plan = build_full_mode_plan(u, profile)
        u["full_mode_plan_json"] = json.dumps(plan, ensure_ascii=False)
        await save_user(u, DB_PATH)
    if text == "📩 Скопировать текст":
        await m.answer(plan.get("copy_text") or "Напиши человеку: я начинаю маленький тест и отмечу результат через 15 минут.", reply_markup=kb_full_mode_experiment)
        return True
    if text == "✅ Отправил(а)":
        await m.answer("Отлично. Первый эксперимент начался. Сейчас нужен только маленький тест, не весь результат.", reply_markup=kb_training_main)
        return True
    if text == "↩️ Хочу другой шаг":
        await m.answer("Ок. Тогда выбираем другой маленький вход: открыть задачу на 10 секунд или написать 3 сырых слова без редактуры.", reply_markup=kb_full_mode_experiment)
        return True
    return False

async def handle_global_button(m: Message, u: Dict[str, Any], text: str) -> bool:
    low = (text or "").lower().strip()
    kind = global_button_kind(text, low)
    if not kind:
        return False
    if kind == "map":
        await send_user_map(m, u, "full_map" if text == "📖 Полная карта" or "полная карта" in low else "global_button")
        return True
    if kind == "action":
        profile = await get_user_profile(u["user_id"], DB_PATH)
        if day_closed_today(u, profile):
            u["stage"] = "closed_day_continue_confirm"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, DAY_CLOSED_CONTINUE_PROMPT, kb_closed_day_continue, "closed_day_continue_confirm")
            return True
        if u.get("current_next_physical_step"):
            await m.answer(returning_to_task_text(u))
        await handle_action_request(u["user_id"], m, u)
        return True
    if kind == "enough":
        u["stage"] = "day_pause_confirm"
        set_current_state(u, STATE_PAUSED, close_action=True)
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Закрыть день или просто сделать паузу?", kb_day_pause_confirm, "day_pause_confirm")
        return True
    if kind in {"close_day", "tomorrow"}:
        await close_day_from_global_button(m, u, f"global_{kind}")
        return True
    if kind == "repeat":
        profile = await get_user_profile(u["user_id"], DB_PATH)
        if day_closed_today(u, profile):
            u["stage"] = "closed_day_continue_confirm"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, DAY_CLOSED_CONTINUE_PROMPT, kb_closed_day_continue, "closed_day_continue_confirm")
            return True
        await handle_action_request(u["user_id"], m, u, repeat=True)
        return True
    if kind == "other_skill":
        await replace_skill_or_request_rediagnosis(m, u, "global_other_skill")
        return True
    if kind == "change_skill":
        if text in {"🤷 Не моё", "🤷 Не мой навык"}:
            config = SKILL_CHANGE_REASON_CONFIG["not_my_skill"]
            await apply_skill_change(m, u, reason_code="not_my_skill", reason_text=config["reason"], new_sid=config["skill_id"], new_name=config["name"], intro=config["intro"], minimum=config["minimum"])
            return True
        await open_skill_change_reason(m, u)
        return True
    if kind == "trainer_switch":
        await open_trainer_switch(m, u, "global_button")
        return True
    if kind == "stuck":
        u["stage"] = "failed_options"
        set_current_state(u, STATE_AWAITING_STUCK_REASON)
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, STUCK_REASON_PROMPT, kb_failed, "failed_options")
        return True
    if kind == "more":
        await answer_with_keyboard(m, u, "Ещё действия:", kb_more_actions, "more_actions")
        return True
    if kind == "why":
        await answer_with_keyboard(m, u, day_lock_why_text(), kb_day_core_stop if day_closed_today(u) else kb_training_main, u.get("stage") or "training")
        return True
    if kind == "why_skill":
        await answer_with_keyboard(m, u, render_last_explanation_context(u), action_keyboard(), u.get("stage") or "training")
        return True
    if kind == "details":
        await m.answer(render_last_explanation_context(u))
        return True
    if kind == "skip":
        if u.get("stage") in {"ask_name", "await_training_target"}:
            return False
        profile = await get_user_profile(u["user_id"], DB_PATH)
        sid = current_skill_id(u) or current_skill_for_action(u)
        skip_count = int(profile.get("action_skip_count") or 0) + 1
        await bot_record_action_event(u, "skill_skipped", metadata={"source": "global_skip"})
        await record_profile_signal(u["user_id"], "training", {"action_skip_count": skip_count, "last_skipped_skill": sid, "avoidance_pattern": "step_skipped", "avoidance_trigger": "шаг ощущается большим или не подходит"}, source="global_skill_skipped")
        u["stage"] = "skip_options"
        mark_pending_return_after_disruption(u, "skip")
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Пропуск записал как данные. Что дальше?", kb_skip_data, "skip_options")
        return True
    return False


async def show_skill_for_current_task(m: Message, u: Dict[str, Any]):
    target = current_task_title(u, str(u.get("today_target") or "сегодняшняя задача"))
    screen = engine_get_next_screen(u, {"type": "target_submitted", "text": target})
    apply_engine_updates(u, screen)
    u["today_target"] = target
    mark_action_card_active(u)
    await save_user(u, DB_PATH)
    await log_engine_events(u, screen)
    await answer_with_keyboard(m, u, screen["text"], action_keyboard(), "skill_card")
    await maybe_show_micro_habit(m, u, "day_start")
    await m.answer(gamify_status_line(u))


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


    real_skill_state = bool(
        u.get("current_skill")
        or u.get("pending_skill_id")
        or u.get("day_core_skill_id")
        or u.get("daily_skill_id")
        or u.get("current_core_skill_id")
    )
    has_persistent_state = bool(
        u.get("stage") not in {None, "", "start", "ask_name"}
        or u.get("name")
        or u.get("first_start_date")
        or int(u.get("has_started_training") or 0) == 1
        or real_skill_state
    )
    if has_persistent_state:
        await save_user(u, DB_PATH)
        await log_event(uid, "start_resume", {"stage": u.get("stage"), "day": u.get("day")}, db_path=DB_PATH)
        if real_skill_state and current_skill_for_action(u):
            await m.answer("Продолжаем с того места, где остановились.")
            await send_current_skill(uid, m, u)
        else:
            await m.answer(
                "Продолжаем с того места, где остановились.\n\n"
                "Я сохранил твой шаг и не начинаю онбординг заново. "
                "Ответь на последний вопрос или выбери действие на клавиатуре."
            )
        return

    # Новый порядок онбординга:
    # 1. Экраны онбординга
    u["stage"] = "ask_name"
    set_current_state(u, STATE_ONBOARDING, close_action=True)
    await save_user(u, DB_PATH)
    await log_event(uid, "onboarding_started", {"stage": u.get("stage")}, db_path=DB_PATH)
    onboarding_screens = list(ONBOARDING_SCREENS)
    if onboarding_screens:
        onboarding_screens[0] = f"{time_of_day_greeting(u)}.\n\n{onboarding_screens[0]}"
    for screen in onboarding_screens:
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

    if u.get("first_start_date") or int(u.get("has_started_training") or 0) == 1 or u.get("day_core_skill_date"):
        sync_calendar_day(u)
        await save_user(u, DB_PATH)

    voice_text = await transcribe_voice_for_current_prompt(m, u)
    if voice_text == "":
        return
    if voice_text:
        text = voice_text.strip()
        low = text.lower()
    elif m.voice:
        await m.answer("Слушаю голосовое и сначала проверяю безопасность…")
        global_voice_text = await whisper_transcribe(m)
        if not global_voice_text:
            await m.answer("Не смог разобрать голосовое. Напиши коротко текстом, что сейчас мешает начать.")
            return
        text = global_voice_text.strip()
        low = text.lower()
        await log_event(u.get("user_id"), u.get("stage", ""), "global_voice_transcribed", {"len": len(text)}, DB_PATH, SHEETS_WEBHOOK_URL)

    # Global safety gate: every text/voice transcript is checked before any normal flow.
    if text and has_crisis_safety_signal(text, u.get("stage") or ""):
        await start_safety_interceptor(m, u, text, "global_text", explicit=False)
        return

    # If a safety block is already active, swallow all normal/productivity routing.
    if await handle_safety_mode(m, u, text):
        return

    if await handle_admin_command(m, u, text):
        return
    if await handle_user_command(m, u, text):
        return
    if TEST_CHEAT_CODE and text == TEST_CHEAT_CODE:
        await activate_test_cheat(m, u, "plain_code")
        return

    if u.get("stage") == "trainer_switch":
        await handle_trainer_switch_choice(m, u, text)
        return

    if await handle_closed_day_input(m, u, text, low):
        return

    if u.get("stage") == "extra_microstep" and (text == "✅ Сделал(а)" or text == "✅ Сделал" or ("сделал" in low and "не сделал" not in low)):
        u["stage"] = "success_limit"
        set_current_state(u, STATE_PAUSED, close_action=True)
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, trainer_wrap(u, SUCCESS_SECOND_STEP_DONE_TEXT, "continue"), kb_success_limit, "success_limit")
        return

    if u.get("stage") == "stuck_reason_text":
        if not text:
            await m.answer("Опиши как есть — одним сообщением или голосом. Не нужно формулировать красиво.")
            return
        await start_stuck_text_validation(m, u, text)
        return

    if await handle_skill_result_feedback(m, u, text):
        return

    if await handle_feedback_response(m, u, text):
        return

    if await handle_full_mode_buttons(m, u, text):
        return

    early_global_kind = global_button_kind(text, low) if is_known_reply_button(text) else ""
    if early_global_kind in {"action", "repeat", "enough", "close_day", "tomorrow", "other_skill", "change_skill", "trainer_switch", "skip", "why", "why_skill", "details", "map", "stuck"}:
        if await handle_global_button(m, u, text):
            return

    if should_reject_action_button(text, u):
        await show_action_changed_fallback(m, u, "action_button_context_mismatch")
        return

    current_kind = global_button_kind(text, low) if is_known_reply_button(text) else ""
    if u.get("current_state") == STATE_AWAITING_RESULT and u.get("stage") not in {"skill_change_reason", "skill_change_free_text", "skill_change_meaning", "stuck_validation_choice"} and is_known_reply_button(text) and text not in ACTION_OUTCOME_BUTTONS and current_kind not in {"map", "crisis"}:
        await show_action_changed_fallback(m, u, "old_action_button_hidden")
        return

    if text in {"🟡 Застрял / не вышло", "🟡 Не вышло"}:
        u["stage"] = "failed_options"
        set_current_state(u, STATE_AWAITING_STUCK_REASON)
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, STUCK_REASON_PROMPT, kb_failed, "failed_options")
        return

    if text == "⏸ Пауза":
        u["stage"] = "day_pause_confirm"
        set_current_state(u, STATE_PAUSED, close_action=True)
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Пауза без штрафа. Закрыть день или просто остановиться сейчас?", kb_day_pause_confirm, "day_pause_confirm")
        return

    if text == "➕ Ещё 2 минуты":
        prompt = extra_microstep_prompt(u)
        if not prompt or int(u.get("success_repeat_count") or 0) > 0:
            await send_success_limit_menu(m, u)
            return
        u["success_repeat_count"] = 1
        u["stage"] = "extra_microstep"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, prompt, kb_extra_microstep_done, "extra_microstep")
        return

    if text in {"🌙 Закрыть подход", "🌙 На сегодня достаточно"}:
        u["stage"] = "waiting_next_day"
        set_current_state(u, STATE_PAUSED, close_action=True)
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Ок. День можно закрыть спокойно: минимум уже выполнен. Если позже захочешь — тренировка остаётся доступной.", kb_training_main, "training_main")
        return

    if text in {"💪 Другое действие", "💪 Продолжить тренировку"}:
        u["success_repeat_count"] = 0
        await save_user(u, DB_PATH)
        await handle_action_request(u["user_id"], m, u)
        return

    if text == "🗣️ Что помогло?":
        u["stage"] = "success_help_note"
        await save_user(u, DB_PATH)
        await m.answer(SUCCESS_HELP_PROMPT, reply_markup=success_menu_keyboard(u))
        return

    pre_global_kind = global_button_kind(text, low) if is_known_reply_button(text) else ""
    if pre_global_kind in {"more"} and not button_fits_current_state(text, u):
        await show_context_fallback(m, u, "known_button_invalid_for_stage")
        return

    if await handle_global_button(m, u, text):
        return

    if is_known_reply_button(text) and not button_fits_current_state(text, u):
        await show_context_fallback(m, u, "known_button_invalid_for_stage")
        return

    if text in {"📖 Полная карта", "🧭 Моя карта"} or "моя карта" in low or "полная карта" in low:
        await send_user_map(m, u, "full_map" if text == "📖 Полная карта" or "полная карта" in low else "persistent_button")
        return

    if text in {"💳 Полный режим", "💳 Что даёт полный режим"} or "полный режим" in low and "плат" not in low:
        await log_event(u["user_id"], u.get("stage", ""), "offer_details_requested", {"source": "persistent_button"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_inline_screen(m, u, offer_details_full_mode_text(), offer_details_inline_keyboard(u["user_id"]), "offer")
        return

    if u.get("stage") == "trainer_switch":
        await handle_trainer_switch_choice(m, u, text)
        return

    if u.get("stage") == "stuck_validation_choice":
        if await handle_stuck_validation_choice(m, u, text):
            return

    if should_route_action_request(text, low, u):
        await handle_action_request(u["user_id"], m, u, repeat=("ещё круг" in low or "еще круг" in low or text == "🔁 Ещё круг"))
        return

    if text == "🔁 Другой навык" or "другой навык" in low:
        await replace_skill_or_request_rediagnosis(m, u, "route_other_skill")
        return

    if "сменить задачу" in low or "другая задача" in low or "новая задача" in low:
        u["stage"] = "await_training_target"
        await save_user(u, DB_PATH)
        await m.answer("Ок. Какую новую задачу берём? Старая задача останется на паузе.")
        return

    if u.get("stage") == "day_menu":
        if text == "💪 Давай действие" or "давай действие" in low:
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            await ask_today_action(m, u)
            return
        if text in {"🌙 Хватит на сегодня", "🌙 Закрыть день"} or "хватит" in low or "закрыть день" in low:
            u["stage"] = "day_pause_confirm"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Закрыть день или просто сделать паузу?", kb_day_pause_confirm, "day_pause_confirm")
            return
        if text in {"🎭 Сменить тренера", "🔄 Сменить тренера"} or "сменить тренера" in low:
            await open_trainer_switch(m, u, "day_menu")
            return
        await answer_with_keyboard(m, u, "Что сейчас сделать?", kb_day_menu, "day_menu")
        return

    if u.get("stage") == "day_pause_confirm":
        if text == "✅ Закрыть день" or text == "🌙 Закрыть день":
            if day_closed_today(u):
                u["stage"] = "day_core_stop"
                await save_user(u, DB_PATH)
                await answer_with_keyboard(m, u, DAY_ALREADY_CLOSED_TEXT, kb_day_core_stop, "day_core_stop")
                return
            current_day = sync_calendar_day(u)
            await mark_day_closed(u, "day_pause_confirm_closed")
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "training", "day_closed_confirmed", {"day": current_day, "day_id": u.get("current_day_id")}, DB_PATH, SHEETS_WEBHOOK_URL)
            if await ask_product_value_feedback(m, u):
                return
            if await ask_day_value_feedback(m, u):
                return
            await answer_with_keyboard(m, u, await day_close_metrics_text(u), kb_day_core_stop, "day_core_stop")
            return
        if text == "⏸ Просто пауза" or "пауза" in low:
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Ок. Это пауза, день не закрыт. Вернуться можно кнопкой «💪 Давай действие».", kb_training_main, "training_main")
            return
        if text == "↩️ Вернуться к навыку" or "вернуться" in low:
            await handle_action_request(u["user_id"], m, u)
            return
        await answer_with_keyboard(m, u, "Закрыть день или просто сделать паузу?", kb_day_pause_confirm, "day_pause_confirm")
        return

    if u.get("stage") == "skill_change_reason":
        if text == "↩️ Оставить текущий навык" or "оставить" in low:
            u["stage"] = "training"
            mark_action_card_active(u)
            await save_user(u, DB_PATH)
            sid = current_skill_for_action(u) or current_skill_id(u)
            skill = dict(SKILLS_DB.get(sid) or SKILLS_DB.get("open_only") or next(iter(SKILLS_DB.values())))
            skill.setdefault("skill_id", sid)
            await answer_with_keyboard(m, u, f"Ок. Оставляем текущий навык.\n\n{format_skill_card(u, skill, current_task_label(u))}", action_keyboard(), "skill_card")
            return
        if text == "🤷 Не понимаю, зачем это делать":
            u["stage"] = "skill_change_meaning"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(
                m,
                u,
                "Похоже, сейчас проблема не в сложности, а в контакте со смыслом.\nНе будем заставлять себя через пустоту.\n\nНовый навык:\n🧩 Вернуть смысл шага\n\nЧто ближе?",
                kb_skill_change_meaning,
                "skill_change_meaning",
            )
            return
        if text == "🎙️ Опишу ситуацию сам(а)":
            u["stage"] = "skill_change_free_text"
            await save_user(u, DB_PATH)
            await m.answer("Опиши одним сообщением или голосом: что именно не ложится в текущем навыке?")
            return
        code = skill_change_code_from_text(text)
        config = SKILL_CHANGE_REASON_CONFIG[code]
        await apply_skill_change(
            m,
            u,
            reason_code=code,
            reason_text=config["reason"] if text in config["buttons"] else (text or config["reason"]),
            new_sid=config["skill_id"],
            new_name=config["name"],
            intro=config["intro"],
            minimum=config["minimum"],
        )
        return

    if u.get("stage") == "skill_change_free_text":
        code = skill_change_code_from_text(text)
        config = SKILL_CHANGE_REASON_CONFIG[code]
        await apply_skill_change(
            m,
            u,
            reason_code=code,
            reason_text=text or config["reason"],
            new_sid=config["skill_id"],
            new_name=config["name"],
            intro=config["intro"],
            minimum=config["minimum"],
        )
        return

    if u.get("stage") == "skill_change_meaning":
        choice = text or "пока не понимаю"
        await apply_skill_change(
            m,
            u,
            reason_code="meaning",
            reason_text="не понимаю, зачем это делать",
            new_sid="task_naming",
            new_name="Вернуть смысл шага",
            intro="Похоже, сейчас проблема не в сложности, а в контакте со смыслом.\nНе будем заставлять себя через пустоту.",
            minimum=meaning_step_text(choice, current_task_label(u)),
            meaning_choice=choice,
        )
        return

    if text in {"🎭 Сменить тренера", "🔄 Сменить тренера"} or "сменить тренера" in low or "другой тренер" in low or "режим" in low and "трен" in low:
        if u.get("stage") in TRAINER_SWITCH_STAGES and (u.get("analysis_json") or int(u.get("has_started_training") or 0) == 1 or u.get("day_core_skill_date")):
            await open_trainer_switch(m, u, u.get("stage") or "unknown")
            return

    if u.get("stage") == "success_help_note" and text and not is_known_reply_button(text):
        note = " ".join(text.split()[:8]).strip()
        if note:
            await record_profile_signal(u["user_id"], "training", {"last_success_helper": note}, source="success_helper_note")
            await log_event(u["user_id"], "training", "success_helper_note_saved", {"note": note[:120]}, DB_PATH, SHEETS_WEBHOOK_URL)
        u["stage"] = "success_menu"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Записал как подсказку для следующих входов. Можно ничего больше не объяснять.", success_menu_keyboard(u), "success_menu")
        return

    if user_is_in_action_loop(u) and text and detects_body_doubling_signal(text):
        await record_profile_signal(u["user_id"], "training", {
            "preferred_activation": "body_doubling",
            "main_pattern": "needs_social_presence_for_start",
        }, source="body_doubling_signal")
        await log_event(u["user_id"], "training", "body_doubling_signal_detected", {"source": "free_text"}, DB_PATH, SHEETS_WEBHOOK_URL)

    perfectionism_triggers = ("идеально", "красиво", "могу лучше", "потом доделаю", "боюсь сделать плохо", "не хочу делать плохо", "должно быть качественно")
    if user_is_in_action_loop(u) and text and any(t in low for t in perfectionism_triggers):
        await record_profile_signal(u["user_id"], "training", {
            "main_pattern": "perfectionism_start_block",
            "avoidance_pattern": "perfectionism_start_block",
            "avoidance_trigger": "желание начать идеально",
            "avoidance_reason": "fear_of_bad_result",
            "emotional_trigger": "shame_or_anxiety",
            "next_theme": "perfectionism_or_shame",
        }, source="perfectionism_trigger")
        await log_event(u["user_id"], "training", "next_theme_detected", {"next_theme": "perfectionism_or_shame"}, DB_PATH, SHEETS_WEBHOOK_URL)

    # Legacy/initial stage recovery.
    # Some users may have persisted stage="start" in DB (legacy default),
    # which should map to the first onboarding question instead of unknown-stage.
    if u.get("stage") == "start":
        u["stage"] = "ask_name"
        await save_user(u, DB_PATH)
        await m.answer(
            "Давай продолжим 👇\n\nКак к тебе обращаться? (1 слово)",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Пропустить")]],
                resize_keyboard=True,
            ),
        )
        return

    morning_answers = set(MORNING_STATE_SKILL_MAP) | set(LEGACY_MORNING_STATE_ALIASES)
    if u.get("stage") == "morning_checkin" and text not in morning_answers:
        inferred = infer_morning_checkin_answer(text)
        if inferred:
            text = inferred
            low = text.lower()
    if u.get("stage") == "morning_checkin" and text in morning_answers:
        state, config = morning_state_config(text)
        skill_id = apply_morning_skill_choice(u, state, config)
        u["last_active"] = time.time()
        was_reactivation = int(u.get("reactivation_count") or 0) > 0
        u["stage"] = "waiting_next_day"
        profile = await get_user_profile(u["user_id"], DB_PATH)
        signal_patch = {
            "morning_state": state,
            "next_skill_hint": skill_id,
            "avoidance_trigger": config.get("avoidance_trigger"),
            "avoidance_pattern": config.get("avoidance_pattern"),
            "attention_pattern": config.get("attention_pattern") or profile.get("attention_pattern"),
            "energy_pattern": config.get("energy_pattern") or profile.get("energy_pattern"),
            "downscale_pattern": config.get("downscale_pattern") or profile.get("downscale_pattern"),
            "emotional_trigger": config.get("emotional_trigger") or profile.get("emotional_trigger"),
            "preferred_activation": config.get("preferred_activation") or profile.get("preferred_activation"),
        }
        await record_profile_signal(u["user_id"], "training", signal_patch, source="morning_state_check")
        await save_user(u, DB_PATH)
        await log_event(uid, "training", "morning_checkin_done", {"state": state, "skill_id": skill_id}, DB_PATH, SHEETS_WEBHOOK_URL)
        await log_event(uid, "training", "morning_core_skill_selected", {"state": state, "skill_id": skill_id}, DB_PATH, SHEETS_WEBHOOK_URL)
        if was_reactivation:
            await log_event(uid, "training", "reactivation_success", {"state": state}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(f"{config.get('message')}\n\nСегодняшний core skill подберу от этого состояния.")
        await ask_today_action(m, u)
        return

    evening_answers = {"✅ сделал", "😐 частично", "❌ не сделал", "↩️ срывался, но возвращался"}
    if u.get("stage") == "evening_checkin" and text not in evening_answers:
        inferred = infer_evening_checkin_answer(text)
        if inferred:
            text = inferred
            low = text.lower()
    if u.get("stage") == "evening_checkin" and text in evening_answers:
        remember_checkin_state(u, "last_evening_state", text)
        u["last_active"] = time.time()
        u["stage"] = "waiting_next_day"
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

    # Глобальный хук: кризис доступен из любого состояния, но не перехватывает диагностику
    # и длинные сообщения, где слово "кризис" используется как часть запроса/разбора.
    if should_open_global_crisis(text, u.get("stage") or ""):
        await start_safety_interceptor(m, u, text, "global_crisis_button", explicit=True)
        return

    # "Ты меня не понял" is a rebuild flow, not a dead-end explanation.
    if is_misunderstood_button(text) and u.get("stage") not in {"misunderstood_reason", "misunderstood_problem_await", "misunderstood_explain_await"}:
        if user_is_in_action_loop(u):
            await record_working_map_skill_result(u["user_id"], "failed_skills", current_skill_id(u))
        await open_misunderstood_flow(m, u, u.get("stage") or "unknown")
        return

    # Pre-skill target prompt: "Пропустить" here means "no specific task",
    # not an action-loop skip/failure. Keep it before action-loop skip handling.
    if u.get("stage") == "await_training_target":
        target_text = (text or "").strip()
        if target_text.lower() != "пропустить":
            await save_current_task(u, DB_PATH, title=target_text)
            await save_user(u, DB_PATH)
            if task_needs_physical_step(target_text) and not u.get("current_next_physical_step"):
                u["stage"] = "await_task_physical_step"
                await save_user(u, DB_PATH)
                await m.answer(
                    "Чтобы не расплываться: какой следующий физический шаг по боту?\n"
                    "Например: открыть файл с правками / написать пост / собрать форму / проверить оплату."
                )
                return
        await show_skill_for_current_task(m, u)
        return

    if u.get("stage") == "await_task_physical_step":
        step = (text or "").strip()
        if not step:
            await m.answer("Напиши один физический шаг: что открыть / написать / проверить первым?")
            return
        await update_current_task_step(u, DB_PATH, step)
        await save_user(u, DB_PATH)
        await m.answer(returning_to_task_text(u))
        await show_skill_for_current_task(m, u)
        return

    # Action-loop clarification/downscale: не запускаем повторную карту после старта тренировки
    if user_is_in_action_loop(u):
        if text == "Пропустить" or low == "пропустить":
            profile = await get_user_profile(u["user_id"], DB_PATH)
            sid = current_skill_id(u)
            skip_count = int(profile.get("action_skip_count") or 0) + 1
            await bot_record_action_event(u, "skill_skipped", metadata={"source": "propustit"})
            await record_profile_signal(u["user_id"], "training", {
                "action_skip_count": skip_count,
                "last_skipped_skill": sid,
                "avoidance_pattern": "step_skipped",
                "avoidance_trigger": "шаг ощущается большим или не подходит",
            }, source="action_skipped")
            await log_event(u["user_id"], "training", "action_skipped", {"skill_id": sid}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["stage"] = "skip_options"
            mark_pending_return_after_disruption(u, "skip")
            await save_user(u, DB_PATH)
            await answer_with_keyboard(
                m,
                u,
                "Ок. Пропуск тоже данные.\n\n"
                "🧭 Добавляю в карту:\n"
                "— в этот момент навык не подошёл\n"
                "— возможно, задача была неясной или слишком общей\n"
                "— нужен более простой вход\n\n"
                "Можно выбрать:\n"
                "🔁 Другой навык\n"
                "🧩 Уменьшить шаг\n"
                "🌙 На сегодня хватит",
                kb_skip_data,
                "skip_options",
            )
            return

        if u.get("stage") == "skip_options":
            if text == "🔁 Другой навык" or "другой" in low:
                await log_event(u["user_id"], "training", "skip_next_selected", {"choice": "other_skill"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await replace_skill_or_request_rediagnosis(m, u, "skip_other_skill")
                return
            if text in {"🧩 Уменьшить шаг", "😣 Сделать проще"} or "проще" in low or "уменьш" in low:
                await log_event(u["user_id"], "training", "skip_next_selected", {"choice": "downscale"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await send_downscale(m, u, "skip_make_simpler")
                return
            if text in {"🌙 На сегодня хватит", "🌙 Хватит на сегодня"} or "хватит" in low:
                u["stage"] = "day_pause_confirm"
                await save_user(u, DB_PATH)
                await answer_with_keyboard(m, u, "Закрыть день или просто сделать паузу?", kb_day_pause_confirm, "day_pause_confirm")
                return
            await answer_with_keyboard(m, u, "Выбери, что делаем дальше:", kb_skip_data, "skip_options")
            return

        if text == "❌ Не сделал" or "не сделал" in low:
            screen = engine_handle_action_result(u, "failed")
            apply_engine_updates(u, screen)
            mark_pending_return_after_disruption(u, "not_done")
            await save_user(u, DB_PATH)
            profile = await get_user_profile(u["user_id"], DB_PATH)
            sid = current_skill_id(u)
            failed_count = int(profile.get("action_failed_count") or 0) + 1
            await record_profile_signal(u["user_id"], "training", {
                "main_pattern": "entry_too_large",
                "avoidance_pattern": "entry_too_large",
                "avoidance_trigger": "перегруз перед стартом",
                "failed_skill": sid,
                "worst_skill": sid,
                "needs_downscale": True,
                "action_failed_count": failed_count,
                "failed_reason_count": failed_count,
                **_today_profile_counter_patch(profile, "failed_reason_count_today", "failed_reason_count_date"),
            }, source="action_failed")
            await record_development_avatar_event(u["user_id"], "slip_recorded", DB_PATH, {"skill_id": sid})
            await record_working_map_skill_result(u["user_id"], "failed_skills", sid)
            set_current_state(u, STATE_AWAITING_STUCK_REASON)
            await save_user(u, DB_PATH)
            await log_engine_events(u, screen)
            await answer_with_keyboard(m, u, screen["text"], kb_failed, "failed")
            return

        if u.get("stage") == "stuck_validation_choice":
            if await handle_stuck_validation_choice(m, u, text):
                return

        if u.get("stage") == "stuck_reason_text":
            if not text:
                await m.answer("Опиши как есть — одним сообщением или голосом.\nНе нужно формулировать красиво.\nЯ сначала попробую понять, что с тобой происходит, а не сразу дать совет.")
                return
            await start_stuck_text_validation(m, u, text)
            return

        if u.get("stage") == "failed_options":
            if text in {"📱 Ушёл в телефон / YouTube", "😬 Страшно, стыдно, боюсь ошибиться", "🧠 Слишком много всего", "🔋 Нет сил", "🫨 Тревога и перегруз", "🧨 Самокритика после срыва", "🤷 Не понимаю, зачем это делать"}:
                code = stuck_reason_code_from_text(text)
                if code == "phone":
                    u["last_event"] = "stuck"
                    mark_pending_return_after_disruption(u, "stuck_phone")
                await send_stuck_reason_skill(m, u, code)
                return
            if text == "🎙️ Опишу голосом или текстом":
                u["stage"] = "stuck_reason_text"
                set_current_state(u, STATE_AWAITING_STUCK_REASON)
                await save_user(u, DB_PATH)
                await m.answer("Опиши как есть — одним сообщением или голосом.\nНе нужно формулировать красиво.\nЯ сначала попробую понять, что с тобой происходит, а не сразу дать совет.")
                return
            if text == "😣 Слишком сложно" or is_too_hard(text):
                await send_stuck_reason_skill(m, u, "overwhelm")
                return
            if text == "😵 Нет сил" or "нет сил" in low:
                await send_stuck_reason_skill(m, u, "energy")
                return
            if text == "📱 Залип" or "залип" in low:
                u["last_event"] = "stuck"
                mark_pending_return_after_disruption(u, "stuck_phone")
                await send_stuck_reason_skill(m, u, "phone")
                return
            if text == "🤔 Не понял" or low in {"не понял", "не понимаю", "я не понимаю"}:
                u["stage"] = "waiting_next_day"
                await save_user(u, DB_PATH)
                await log_event(u["user_id"], "training", "dont_understand_clicked", {"source": "failed_options"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await log_event(u["user_id"], "training", "failed_reason_selected", {"reason": "failed_dont_understand"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await answer_with_keyboard(m, u, failed_reason_explanation("failed_too_hard", u), kb_microstep, "microstep")
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
                u["stage"] = "waiting_next_day"
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
                u["stage"] = "waiting_next_day"
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
                profile = await get_user_profile(u["user_id"], DB_PATH)
                downscale_count = int(profile.get("downscale_count") or 0) + 1
                await record_profile_signal(u["user_id"], "training", {
                    "main_pattern": "micro_entry_block",
                    "avoidance_pattern": "entry_too_large",
                    "avoidance_trigger": "шаг становится слишком большим",
                    "downscale_pattern": "needs_smaller_step",
                    "energy_pattern": "low_start_energy",
                    "needs_minimum_action": True,
                    "next_skill_hint": "task_naming",
                    "downscale_count": downscale_count,
                }, source="downscale_even_too_hard")
                await record_development_avatar_event(u["user_id"], "downscale", DB_PATH, {"reason": "even_open_too_hard", "skill_id": DOWNSCALE_FALLBACK_SKILL})
                await m.answer(trainer_failed_response(u.get("trainer_key") or "marsha"))
                await answer_with_keyboard(
                    m,
                    u,
                    "Ок. Тогда ещё меньше.\n\n"
                    "Не открывай задачу.\n"
                    "Просто напиши сюда название задачи одним словом или пришли голосовое.",
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
                mark_day_core_round_done(u)
                mark_current_skill_status(u, "completed")
                set_current_state(u, STATE_PAUSED, close_action=True)
                gamify_apply(u, 2, "downscale_done")
                u["stage"] = "waiting_next_day"
                await save_user(u, DB_PATH)
                profile = await get_user_profile(u["user_id"], DB_PATH)
                await record_return_after_slip_action_event_if_needed(u, "downscale_done")
                returned_after_disruption = await record_return_after_disruption_if_needed(u, profile, "done_after_disruption")
                if returned_after_disruption:
                    await save_user(u, DB_PATH)
                    profile = await get_user_profile(u["user_id"], DB_PATH)
                sid = current_skill_id(u) or DOWNSCALE_PRIMARY_SKILL
                await bot_record_action_event(u, "attempt_completed_self_reported", skill_id=sid, metadata={"source": "downscale_done"})
                await record_profile_signal(u["user_id"], "training", {
                    "last_completed_skill": sid,
                    "last_skill_effect": "unknown",
                    "preferred_activation": "small_visible_step",
                    "action_done_count": int(profile.get("action_done_count") or 0) + 1,
                    **_today_profile_counter_patch(profile, "action_done_count_today", "action_done_count_date"),
                }, source="downscale_done")
                await record_development_avatar_event(u["user_id"], "skill_done", DB_PATH, {"skill_id": sid, "after_downscale": True, "streak": int(u.get("streak") or 0), "target": u.get("today_target") or ""})
                await record_working_map_skill_result(u["user_id"], "completed_skills_effect_unknown", sid)
                if should_show_day3_offer(u, int(u.get("day") or 1)):
                    await show_day3_offer(m, u, "day3_auto")
                    return
                await send_success_menu(m, u, source="downscale_done")
                return

        if u.get("stage") == "downscale_name_task":
            if text == "✅ Написал" or "написал" in low or text:
                await log_event(u["user_id"], "training", "downscale_done", {"stage": "downscale_name_task", "day": int(u.get("day") or 1)}, DB_PATH, SHEETS_WEBHOOK_URL)
                previous_done = int(u.get("done_count") or 0)
                u["done_count"] = previous_done + 1
                mark_day_core_round_done(u)
                mark_current_skill_status(u, "completed")
                set_current_state(u, STATE_PAUSED, close_action=True)
                gamify_apply(u, 2, "downscale_done")
                u["stage"] = "waiting_next_day"
                await save_user(u, DB_PATH)
                profile = await get_user_profile(u["user_id"], DB_PATH)
                await record_return_after_slip_action_event_if_needed(u, "downscale_done")
                returned_after_disruption = await record_return_after_disruption_if_needed(u, profile, "done_after_disruption")
                if returned_after_disruption:
                    await save_user(u, DB_PATH)
                    profile = await get_user_profile(u["user_id"], DB_PATH)
                sid = current_skill_id(u) or DOWNSCALE_PRIMARY_SKILL
                await bot_record_action_event(u, "attempt_completed_self_reported", skill_id=sid, metadata={"source": "downscale_name_done"})
                await record_profile_signal(u["user_id"], "training", {
                    "last_completed_skill": sid,
                    "last_skill_effect": "unknown",
                    "preferred_activation": "small_visible_step",
                    "action_done_count": int(profile.get("action_done_count") or 0) + 1,
                    **_today_profile_counter_patch(profile, "action_done_count_today", "action_done_count_date"),
                }, source="downscale_done")
                await record_development_avatar_event(u["user_id"], "skill_done", DB_PATH, {"skill_id": sid, "after_downscale": True, "streak": int(u.get("streak") or 0), "target": u.get("today_target") or ""})
                await record_working_map_skill_result(u["user_id"], "completed_skills_effect_unknown", sid)
                if should_show_day3_offer(u, int(u.get("day") or 1)):
                    await show_day3_offer(m, u, "day3_auto")
                    return
                await send_success_menu(m, u, source="downscale_name_done")
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

    if u.get("stage") == "after_action_note":
        note = " ".join((text or "").split()[:5]).strip()
        if not note:
            await m.answer("Напиши 1–5 слов или пришли голосовое: что изменилось после шага?")
            return
        u["stage"] = "waiting_next_day"
        await save_user(u, DB_PATH)
        effect_tags = effect_tags_from_note(note)
        effect_patch = {"last_after_action_note": note, "last_effect_note": note, "effect_tags": effect_tags}
        if "relief" in effect_tags:
            effect_patch["effect_relief"] = True
        if "confidence_up" in effect_tags:
            effect_patch["effect_confidence"] = True
        if "anxiety_down" in effect_tags:
            effect_patch["effect_anxiety_down"] = True
        if "clarity_up" in effect_tags:
            effect_patch["effect_clarity"] = True
        sid = current_skill_id(u)
        helpful = bool({"relief", "confidence_up", "anxiety_down", "clarity_up"} & set(effect_tags))
        skill_patch = {"last_effect_note": note, "last_skill_effect": "helpful" if helpful else "unknown"}
        if helpful:
            skill_patch.update({"best_skill": sid, "last_successful_skill": sid})
        await record_profile_signal(u["user_id"], "training", {**effect_patch, **skill_patch}, source="after_action_note_saved")
        if helpful:
            await record_working_map_skill_result(u["user_id"], "successful_skills", sid)
        await log_event(u["user_id"], "training", "after_action_note_saved", {"len": len(note), "effect_tags": effect_tags}, DB_PATH, SHEETS_WEBHOOK_URL)
        await send_success_menu(m, u, source="after_action_note")
        return

    # Пост-выполнение: кнопки из меню после подхода/завершения дня должны
    # работать и после answer_with_keyboard(..., "done"/"day_core_stop").
    # Раньше stage менялся на "done" или "day_core_stop", и следующие нажатия
    # попадали в общий fallback "Выбери действие", хотя кнопки были валидными.
    if u.get("stage") in {"waiting_next_day", "done", "day_core_stop"}:
        trainer_key = u.get("trainer_key") or "marsha"
        if text in {"📖 Полная карта", "🧭 Моя карта"} or "моя карта" in low or "полная карта" in low:
            await send_user_map(m, u, "full_map" if text == "📖 Полная карта" or "полная карта" in low else "day_core_stop")
            return
        if text == "📚 Почему это работает" or "почему это работает" in low:
            await log_event(u["user_id"], "training", "day_lock_why_opened", {"day": int(u.get("day") or 1)}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, day_lock_why_text(), kb_day_core_stop, "day_core_stop")
            return
        if text == "🌙 До завтра" or "до завтра" in low:
            await log_event(u["user_id"], "training", "day_lock_until_tomorrow", {"day": int(u.get("day") or 1)}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, DAY_ALREADY_CLOSED_TEXT if day_closed_today(u) else "До завтра. Новый основной навык откроется после смены календарного дня.", kb_day_core_stop, "day_core_stop")
            if not day_closed_today(u):
                await maybe_show_micro_habit(m, u, "day_core_stop")
            return
        if (text == "📌 Что изменилось?" or "что изменилось" in low) and u.get("stage") != "day_core_stop":
            u["stage"] = "after_action_note"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "training", "after_action_note_requested", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Напиши 1–5 слов или пришли голосовое: что изменилось после шага?")
            return
        if (text == "🔁 Ещё круг" or "еще круг" in low or "ещё круг" in low) and u.get("stage") != "day_core_stop":
            await handle_action_request(u["user_id"], m, u, repeat=True)
            return
        if text in {"🌙 На сегодня хватит", "🌙 Хватит на сегодня"} or "хватит" in low:
            u["stage"] = "day_pause_confirm"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Закрыть день или просто сделать паузу?", kb_day_pause_confirm, "day_pause_confirm")
            return
        await show_context_fallback(m, u, "post_action_invalid_button")
        return



    if (
        text in {"📚 Подробнее", "🤔 Зачем это?"} or "подробнее" in (text or "").lower()
    ) and u.get("stage") not in {"confirm_analysis", "analysis_" + "contract", "analysis_next_step", "analysis_details", "offer"} and not bool(u.get("micro_habit_json")):
        await log_event(u["user_id"], u.get("stage", ""), "last_explanation_context_opened", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(render_last_explanation_context(u))
        return

    if (
        text in {"📚 Подробнее", "🤔 Зачем это?"}
        and u.get("stage") not in {"confirm_analysis", "analysis_" + "contract", "analysis_next_step", "offer"}
        and bool(u.get("micro_habit_json"))
    ):
        habit = {}
        try:
            habit = json.loads(u.get("micro_habit_json") or "{}")
        except Exception:
            habit = {}
        profile = await get_user_profile(u["user_id"], DB_PATH)
        await log_event(u["user_id"], "training", "system_day_details_opened", {"system_day_id": habit.get("id"), "habit_id": habit.get("id")}, DB_PATH, SHEETS_WEBHOOK_URL)
        await record_profile_signal(u["user_id"], "training", {
            "side_skill_interest": habit.get("id") or "details_opened",
            "last_system_day_id": habit.get("id") or "",
            "system_day_opened": _profile_append_unique(profile, "system_day_opened", habit.get("id") or ""),
        }, source="system_day_details")
        await m.answer(habit.get("why") or "Это системный принцип: он не требует отчёта, но постепенно снижает трение жизни.")
        return
    if text == "👍 Попробую":
        habit = {}
        try:
            habit = json.loads(u.get("micro_habit_json") or "{}")
        except Exception:
            habit = {}
        profile = await get_user_profile(u["user_id"], DB_PATH)
        await log_event(u["user_id"], "training", "system_day_try_clicked", {"system_day_id": habit.get("id"), "habit_id": habit.get("id")}, DB_PATH, SHEETS_WEBHOOK_URL)
        await record_profile_signal(u["user_id"], "training", {
            "side_skill_interest": habit.get("id") or "try_clicked",
            "last_system_day_id": habit.get("id") or "",
            "system_day_useful": _profile_append_unique(profile, "system_day_useful", habit.get("id") or ""),
        }, source="system_day_try")
        await answer_with_keyboard(m, u, "Ок. Это не отчёт и не обязательство. Просто заметим как полезный системный принцип.", kb_day_core_stop, "day_core_stop")
        return
    if text == "🤔 Уже делаю":
        habit = {}
        try:
            habit = json.loads(u.get("micro_habit_json") or "{}")
        except Exception:
            habit = {}
        profile = await get_user_profile(u["user_id"], DB_PATH)
        await log_event(u["user_id"], "training", "system_day_already_clicked", {"system_day_id": habit.get("id"), "habit_id": habit.get("id")}, DB_PATH, SHEETS_WEBHOOK_URL)
        await record_profile_signal(u["user_id"], "training", {
            "side_skill_interest": habit.get("id") or "already_used",
            "last_system_day_id": habit.get("id") or "",
            "system_day_already": _profile_append_unique(profile, "system_day_already", habit.get("id") or ""),
        }, source="system_day_already")
        await answer_with_keyboard(m, u, "Отлично. Сохраняю это как часть твоей системы, не как задание.", kb_day_core_stop, "day_core_stop")
        return
    if text in {"➡️ Дальше", "🤷 Не моё"}:
        habit = {}
        try:
            habit = json.loads(u.get("micro_habit_json") or "{}")
        except Exception:
            habit = {}
        await log_event(u["user_id"], "training", "system_day_skipped", {"system_day_id": habit.get("id"), "habit_id": habit.get("id")}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(m, u, "Ок. Это не задание. Ничего не считаем и не списываем.", kb_day_core_stop, "day_core_stop")
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
            await show_context_fallback(m, u, "await_trainer_invalid_button")
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
                f"{time_of_day_greeting_with_name(u)}\n\nКак удобнее собрать первую рабочую карту?",
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
            await m.answer("Ок. Напиши 2–5 предложений или пришли голосовое: что сейчас мешает делать важное?", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True))
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
            await answer_with_inline_screen(m, u, msg, create_test_question_keyboard(1), "test")
            return
        await show_context_fallback(m, u, "input_mode_invalid_button")
        return

    # Legacy stage: не показываем карту автоматически, сразу ведём к первому действию
    if u.get("stage") == "diagnosis_done":
        u["stage"] = "waiting_next_day"
        u["day"] = 1
        ensure_first_start_date(u)
        await save_user(u, DB_PATH)
        await start_day(m, u, calendar_program_day(u), DB_PATH, SHEETS_WEBHOOK_URL)
        return

    # choose_input_mode
    if u["stage"] == "choose_input_mode":
        low = text.lower().strip()
        if text == "🧠 Диагностика текстом" or "текст" in low:
            u["input_mode"] = "text"
            u["stage"] = "await_problem_text"
            await save_user(u, DB_PATH)
            await m.answer("Ок. Напиши 2–5 предложений или пришли голосовое: что сейчас мешает делать важное?", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True))
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
            await answer_with_inline_screen(m, u, msg, create_test_question_keyboard(1), "test")
            return
        await show_context_fallback(m, u, "input_mode_invalid_button")
        return

    # await_problem_text
    if u["stage"] == "await_problem_text":
        if m.voice:
            await m.answer("Слушаю голосовое и перевожу в текст…")
            user_text = await whisper_transcribe(m)
            if not user_text:
                await m.answer("Не смог разобрать голосовое. Напиши текстом 1–3 предложения или пришли голосовое ещё раз.")
                return
            await m.answer(f"Распознал: {clamp_str(user_text, 700)}")
        elif not text or text.lower() == "пропустить":
            user_text = "Прокрастинация/избегание, хочу начать, но откладываю."
        else:
            user_text = text
        u["analysis_json"] = json.dumps(safe_analysis_memory(user_text, {"bucket": u.get("bucket") or "mixed"}), ensure_ascii=False)
        u["stage"] = "run_analysis"
        await save_user(u, DB_PATH)
        await m.answer(analysis_loading_text(u.get("trainer_key") or "marsha"))
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
        u["analysis_json"] = json.dumps(safe_analysis_memory(t, {"bucket": u.get("bucket") or "mixed"}), ensure_ascii=False)
        u["stage"] = "run_analysis"
        await save_user(u, DB_PATH)
        await m.answer(analysis_loading_text(u.get("trainer_key") or "marsha"))
        await run_analysis(m, u, t, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
        return

    # Legacy post-analysis confirmation: keep old users moving into action without a course screen.
    if u.get("stage") in {"analysis_" + "contract", "analysis_next_step"}:
        low = (text or "").lower()

        # Обработка старых подтверждений и ответов "Да" после подробного текста
        if (
            text == "💪 Давай действие"
            or "действие" in low
            or "продолж" in low
            or "принимаю" in low
            or "принимают" in low
            or text == "✅ Да"
            or low.strip() == "да"
        ):
            u["stage"] = "waiting_next_day"
            u["day"] = 1
            ensure_first_start_date(u)
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "analysis", "day1_started", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer(guarantee_block(u.get("trainer_key")), reply_markup=kb_yes_no)
            # Запуск первого календарного дня сразу
            await start_day(m, u, calendar_program_day(u), DB_PATH, SHEETS_WEBHOOK_URL)
            return

        if text == "❌ Нет" or "нет" in low:
            await m.answer("Ок. Вернёмся позже.")
            return

    # analysis_map
    if u.get("stage") == "analysis_map":
        low = (text or "").lower()
        if "принимаю" in low or "принимают" in low:
            u["stage"] = "waiting_next_day"
            u["day"] = 1
            ensure_first_start_date(u)
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "analysis", "day1_started", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            # Явно запускаем первый календарный день тренировки
            await start_day(m, u, calendar_program_day(u), DB_PATH, SHEETS_WEBHOOK_URL)
            return
        if "нет" in low:
            await m.answer("Ок. Без гарантии — не стартуем.")
            return

    if u.get("stage") == "working_map":
        if text == "➡️ Переходим к первому навыку" or "первому навыку" in low or "давай действие" in low:
            await log_event(u["user_id"], "analysis", "analysis_action_started", {"source": "working_map"}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["stage"] = "waiting_next_day"
            ensure_first_start_date(u)
            await save_user(u, DB_PATH)
            await m.answer(
                "Ок.\n\n"
                "Теперь посмотрим,\n"
                "какие навыки реально помогают именно тебе."
            )
            await start_day(m, u, calendar_program_day(u), DB_PATH, SHEETS_WEBHOOK_URL)
            return
        if "подробнее" in low or text == "📚 Подробнее":
            await log_event(u["user_id"], "analysis", "analysis_details_requested", {"source": "working_map"}, DB_PATH, SHEETS_WEBHOOK_URL)
            try:
                comp = json.loads(u.get("analysis_json") or "{}")
            except Exception:
                comp = {}
            details = render_analysis_details_by_trainer(comp if isinstance(comp, dict) else {}, u.get("trainer_key") or "marsha")
            await answer_with_keyboard(m, u, details, kb_working_map, "analysis_details")
            return
        await show_context_fallback(m, u, "working_map_invalid_button")
        return

    # confirm_analysis
    if u["stage"] == "confirm_analysis":
        low = text.lower()
        if "давай действие" in low or text == "💪 Давай действие":
            await log_event(u["user_id"], "analysis", "analysis_action_started", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            profile = await get_user_profile(u["user_id"], DB_PATH)
            if not profile.get("social_support_prompt_shown"):
                u["stage"] = "social_support_await"
                u["pending_plan_change"] = json.dumps({"type": "social_support", "return_stage": "start_day"}, ensure_ascii=False)
                await update_user_profile(u["user_id"], {"social_support_prompt_shown": 1}, DB_PATH, source="social_support_prompt")
                await save_user(u, DB_PATH)
                await answer_with_keyboard(m, u, social_support_prompt_text(), kb_social_support, "social_support")
                return
            u["stage"] = "waiting_next_day"
            u["day"] = 1
            ensure_first_start_date(u)
            await save_user(u, DB_PATH)
            await start_day(m, u, calendar_program_day(u), DB_PATH, SHEETS_WEBHOOK_URL)
            return
        if "подробнее" in low or text == "📚 Подробнее":
            await log_event(u["user_id"], "analysis", "analysis_details_requested", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            try:
                comp = json.loads(u.get("analysis_json") or "{}")
            except Exception:
                comp = {}
            details = render_analysis_details_by_trainer(comp if isinstance(comp, dict) else {}, u.get("trainer_key") or "marsha")
            await answer_with_keyboard(m, u, details, kb_analysis_confirm, "analysis_details")
            return
        if "в точку" in low or (text == "✅ Да, в точку"):
            await log_event(u["user_id"], "analysis", "analysis_accepted", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            try:
                comp = json.loads(u.get("analysis_json") or "{}")
            except Exception:
                comp = {}
            comp = comp if isinstance(comp, dict) else {}
            updated_profile = await update_user_profile(u["user_id"], working_map_profile_patch(comp), DB_PATH, source="working_map_confirmed")
            u["profile_json"] = updated_profile
            if not updated_profile.get("social_support_prompt_shown"):
                u["stage"] = "social_support_await"
                u["pending_plan_change"] = json.dumps({"type": "social_support", "return_stage": "working_map"}, ensure_ascii=False)
                await update_user_profile(u["user_id"], {"social_support_prompt_shown": 1}, DB_PATH, source="social_support_prompt")
                await save_user(u, DB_PATH)
                await answer_with_keyboard(m, u, social_support_prompt_text(), kb_social_support, "social_support")
                return
            u["stage"] = "working_map"
            u["day"] = 1
            ensure_first_start_date(u)
            set_last_explanation_context(
                u,
                "map",
                "рабочая карта",
                "Карта показывает не диагноз, а рабочую схему: где стопор, что запускает избегание и какой навык проверяем первым.",
                ["гипотеза подтверждена пользователем", "карта будет уточняться по действиям", "важны повторяющиеся сигналы, а не один идеальный ответ"],
                "Нажми «Давай действие», чтобы проверить карту практикой."
            )
            await save_user(u, DB_PATH)
            await answer_with_keyboard(
                m,
                u,
                preliminary_development_map_from_analysis(comp),
                kb_working_map,
                "working_map",
            )
            return
        if "немного" in low or "не так" in low or "не совсем" in low or text in {"🤔 Немного не так", "🤔 Не совсем"}:
            await open_misunderstood_flow(m, u, "confirm_analysis")
            return
        await show_context_fallback(m, u, "confirm_analysis_invalid_button")
        return

    # Подробное объяснение после анализа без курса/карты до первого действия.
    if u.get("stage") in {"analysis_" + "contract", "analysis_next_step", "analysis_details"} and (text == "📚 Подробнее" or "подробнее" in text.lower()):
        try:
            comp = json.loads(u.get("analysis_json") or "{}")
        except Exception:
            comp = {}
        await answer_with_keyboard(
            m,
            u,
            render_analysis_details_by_trainer(comp if isinstance(comp, dict) else {}, u.get("trainer_key") or "marsha"),
            kb_analysis_confirm,
            "analysis_details",
        )
        return

    # misunderstood_reason: rebuild analysis/map/skill instead of defending old answer
    if u.get("stage") == "misunderstood_reason":
        low = text.lower().strip()
        reason = ""
        if text.startswith("1") or "не та проблема" in low:
            reason = "wrong_problem"
            u["stage"] = "misunderstood_problem_await"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "analysis", "misunderstood_reason_selected", {"reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Ок. Какая проблема точнее? Одно сообщение, 1–2 предложения.")
            return
        if text.startswith("2") or "общ" in low:
            reason = "too_generic"
            await log_event(u["user_id"], "analysis", "misunderstood_reason_selected", {"reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
            await rebuild_analysis_lightweight(m, u, "Ответ был слишком общий. Нужен конкретный разбор по паттернам входа, избегания и полезного сигнала.", reason)
            return
        if text.startswith("3") or "не тот навык" in low:
            reason = "wrong_skill"
            await log_event(u["user_id"], "analysis", "misunderstood_reason_selected", {"reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
            await rebuild_analysis_lightweight(m, u, "Не тот навык. Нужно полностью пересобрать гипотезу и подобрать другой вход.", reason, replace_skill=True)
            return
        if text.startswith("4") or "не про лень" in low:
            reason = "not_laziness"
            await log_event(u["user_id"], "analysis", "misunderstood_reason_selected", {"reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
            try:
                comp = json.loads(u.get("analysis_json") or "{}")
                if not isinstance(comp, dict):
                    comp = {}
            except Exception:
                comp = {}
            safe_prompt = stored_analysis_user_text(u)
            comp = normalize_analysis(comp, safe_prompt)
            comp.pop("user_text", None)
            comp["specific_pattern"] = "страх оценки / стыд / цена ошибки"
            comp["avoidance_behavior"] = "заморозка перед безопасным черновиком"
            comp["useful_signal"] = "ленивость исключена из модели; проверяем не внимание, а безопасный черновик"
            comp["selected_skill"] = "bad_first_draft" if "bad_first_draft" in SKILLS_DB else comp.get("selected_skill")
            comp.update(safe_analysis_memory(safe_prompt, comp))
            u["analysis_json"] = json.dumps(comp, ensure_ascii=False)
            source = misunderstood_context(u).get("source") or "analysis"
            u["pending_plan_change"] = None
            u["stage"] = "confirm_analysis" if source == "confirm_analysis" else "waiting_next_day"
            await save_user(u, DB_PATH)
            patch = {
                "not_laziness_confirmed": True,
                "last_misunderstood_reason": reason,
                "main_hypothesis": "страх оценки / стыд / цена ошибки",
                "main_pattern": "fear_of_evaluation",
                "avoidance_trigger": "цена ошибки",
                "avoidance_pattern": "shame_or_evaluation_freeze",
                "attention_pattern": "",
            }
            await update_user_profile(u["user_id"], patch, DB_PATH)
            await log_event(u["user_id"], "analysis", "analysis_rebuilt", {"reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
            await log_event(u["user_id"], "analysis", "profile_map_updated", {"source": "misunderstood", **patch}, DB_PATH, SHEETS_WEBHOOK_URL)
            markup = kb_analysis_confirm if u["stage"] == "confirm_analysis" else kb_training_main
            await answer_with_keyboard(
                m,
                u,
                "Ок. Исправляю карту.\n\n"
                "Старую гипотезу убираю.\n"
                "Новая гипотеза:\n"
                "— страх оценки / стыд / цена ошибки\n\n"
                "Сегодня проверяем не внимание,\n"
                "а безопасный черновик.\n\n"
                + format_comprehensive_analysis(comp, trainer_key=u.get("trainer_key") or "marsha"),
                markup,
                "analysis_rebuilt",
            )
            return
        if text.startswith("5") or "объяснить иначе" in low or "по-другому" in low or "по другому" in low:
            reason = "explain_differently"
            u["stage"] = "misunderstood_explain_await"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "analysis", "misunderstood_reason_selected", {"reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Ок. Напиши иначе одним сообщением или пришли голосовое. Я пересоберу карту без полного онбординга.")
            return
        await answer_with_keyboard(m, u, misunderstood_prompt_text(), kb_misunderstood_reasons, "misunderstood_reasons")
        return

    if u.get("stage") == "misunderstood_problem_await":
        if not text:
            await m.answer("Напиши 1–2 предложения или пришли голосовое: какая проблема точнее?")
            return
        await rebuild_analysis_lightweight(m, u, f"Не та проблема. Точнее: {text}", "wrong_problem")
        return

    if u.get("stage") == "misunderstood_explain_await":
        if not text:
            await m.answer("Напиши одним сообщением или пришли голосовое: как объяснить точнее.")
            return
        await rebuild_analysis_lightweight(m, u, f"Пользователь объяснил иначе: {text}", "explain_differently", replace_skill=True)
        return

    # analysis_need_more
    if u.get("stage") == "analysis_need_more":
        if not text:
            await answer_with_keyboard(m, u, "Выбери, что чаще ломает вход 👇", kb_analysis_need_more, "analysis_need_more")
            return
        previous_text = ""
        try:
            previous_text = stored_analysis_user_text(u)
        except Exception:
            previous_text = ""
        combined_text = analysis_need_more_expanded_text(previous_text, text)
        u["analysis_json"] = json.dumps(safe_analysis_memory(combined_text, {"bucket": u.get("bucket") or "mixed"}), ensure_ascii=False)
        u["stage"] = "run_analysis"
        await save_user(u, DB_PATH)
        await log_event(u["user_id"], "analysis", "analysis_extra_signal_added", {"answer": text[:80]}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer("Ок. Теперь точнее.")
        await run_analysis(m, u, combined_text, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
        return

    # analysis_retry_await_clarification
    if u.get("stage") == "analysis_retry_await_clarification":
        if not text:
            await m.answer("Напиши или пришли голосовое: что не совпадает с реальностью. (1–3 предложения)")
            return
        u["analysis_json"] = json.dumps(safe_analysis_memory(text, {"bucket": u.get("bucket") or "mixed"}), ensure_ascii=False)
        u["stage"] = "run_analysis"
        await save_user(u, DB_PATH)
        await m.answer("Ок. Переразбор…")
        await run_analysis(m, u, text, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
        return

    # analysis_refine
    if u["stage"] == "analysis_refine":
        if not text:
            await m.answer("Напиши 1–2 предложения или пришли голосовое, чтобы я пересобрал вывод.")
            return
        # Объединяем исходный текст и уточнение, чтобы модель видела весь контекст
        base_user_text = ""
        try:
            if u.get("analysis_json"):
                base_user_text = stored_analysis_user_text(u)
        except Exception:
            base_user_text = ""

        combined_text = base_user_text.strip()
        if combined_text:
            combined_text += "\n\nУточнение пользователя: " + text
        else:
            combined_text = text

        u["stage"] = "run_analysis"
        await save_user(u, DB_PATH)
        await m.answer("Ок. Пересобираю вывод…")
        await run_analysis(m, u, combined_text, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
        return

    # taking_test
    # Если пользователь отправил текст вместо ответа через callback-кнопки,
    # возвращаем к текущему вопросу и сохраняем прогресс теста.
    if u.get("stage") == "taking_test":
        test_answers = u.get("test_answers") or []
        next_q_num = len(test_answers) + 1
        next_q = next((x for x in TEST_QUESTIONS if x["id"] == next_q_num), None)
        if not next_q:
            next_q_num = 1
            next_q = TEST_QUESTIONS[0]
            u["test_answers"] = []
            await save_user(u, DB_PATH)
        await answer_with_inline_screen(
            m,
            u,
            f"Чтобы пройти тест, выбери вариант кнопкой ниже 👇\n\n❓ Вопрос {next_q_num}/5:\n\n{next_q['text']}",
            create_test_question_keyboard(next_q_num),
            "test",
        )
        return

    # TRAINING stage
    if u.get("stage") == "training":
        low = text.lower().strip()
        day = int(u.get("day") or 1)

        if text == "😣 Слишком сложно" or is_too_hard(text):
            await bot_record_action_event(u, "too_hard_reported", metadata={"source": "training"})
            await send_downscale(m, u, "new_day_too_hard")
            return
        if text == "😵 Нет сил" or "нет сил" in low:
            await bot_record_action_event(u, "no_energy_reported", metadata={"source": "training"})
            await send_downscale(m, u, "new_day_no_energy")
            return
        if text == "📱 Залип" or "залип" in low:
            await bot_record_action_event(u, "slip_reported", metadata={"source": "training"})
            u["last_event"] = "stuck"
            mark_pending_return_after_disruption(u, "stuck_phone")
            await send_downscale(m, u, "new_day_stuck_phone")
            return
        if text == "🔁 Другой навык" or "другой" in low or "заменить" in low:
            await replace_skill_or_request_rediagnosis(m, u, "new_day_other_skill")
            return

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
                "'✅ Сделал' или '❌ Не сделал'."
            )
            await log_event(u["user_id"], "training", "repeat_practice", {"day": day, "sid": sid}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, trainer_say(trainer_key, f"{detail}\n\n{prompt}"), kb_training_main, "training_main")
            return

        if text in {"📚 Подробнее", "ℹ️ Подробнее", "ℹ️ Подробнее про навык"} or "подробнее" in low:
            plan = get_current_plan(u)
            idx = max(0, min(len(plan) - 1, int(u.get("day") or 1) - 1))
            sid = plan[idx]
            await log_event(u["user_id"], "training", "details_clicked", {"skill_id": sid}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, render_last_explanation_context(u), kb_more_clarify, "detail")
            return

        if text in {"🤔 Я не понимаю", "🤔 Не понял"} or low in {"я не понимаю", "не понял", "не понимаю"}:
            await log_event(u["user_id"], "training", "dont_understand_clicked", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, simple_explain_text(), kb_microstep, "microstep")
            return

        if text == "Ещё" or text == "Еще" or low in {"ещё", "еще"}:
            await answer_with_keyboard(m, u, "Ещё действия:", kb_more_actions, "more_actions")
            return

        if text in {"📖 Полная карта", "🧭 Моя карта"} or "моя карта" in low or "полная карта" in low:
            await send_user_map(m, u, "full_map" if text == "📖 Полная карта" or "полная карта" in low else "training")
            return

        if text == "📚 Что это значит" or "что это значит" in low:
            await m.answer("Это не медицинское заключение, а рабочая гипотеза по твоим действиям. Сейчас активен только модуль прокрастинации и запуска; остальные направления — будущие.")
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

        if text in {"📊 Мой прогресс", "📊 Прогресс"} or "мой прогресс" in low or "прогресс" in low:
            await m.answer("Пока прогресс-цифры скрыты до проверки точности. Итоги покажу при закрытии дня.")
            return

        if text == "✅ Сделал(а)" or ("сделал" in low and "не сделал" not in low):
            screen = engine_handle_action_result(u, "done")
            previous_done = int(u.get("done_count") or 0)
            u["done_count"] = previous_done + 1
            mark_day_core_round_done(u)
            mark_current_skill_status(u, "completed")
            set_current_state(u, STATE_PAUSED, close_action=True)
            gamify_apply(u, 2, "done")
            apply_engine_updates(u, screen)
            await save_user(u, DB_PATH)
            profile = await get_user_profile(u["user_id"], DB_PATH)
            await record_return_after_slip_action_event_if_needed(u, "done")
            returned_after_disruption = await record_return_after_disruption_if_needed(u, profile, "done_after_disruption")
            if returned_after_disruption:
                await save_user(u, DB_PATH)
                profile = await get_user_profile(u["user_id"], DB_PATH)
            sid = current_skill_id(u)
            await bot_record_action_event(u, "attempt_completed_self_reported", skill_id=sid, metadata={"source": "done_button"})
            done_count = int(profile.get("action_done_count") or 0) + 1
            preferred_activation = "body_doubling" if sid == "body_doubling_plan" else ("phone_away" if sid == "phone_far_3min" else "small_visible_step")
            await record_profile_signal(u["user_id"], "training", {
                "last_completed_skill": sid,
                "last_skill_effect": "unknown",
                "preferred_activation": preferred_activation,
                "action_done_count": done_count,
                **_today_profile_counter_patch(profile, "action_done_count_today", "action_done_count_date"),
            }, source="action_done")
            await record_development_avatar_event(u["user_id"], "skill_done", DB_PATH, {"skill_id": sid, "streak": int(u.get("streak") or 0), "target": u.get("today_target") or ""})
            await record_working_map_skill_result(u["user_id"], "completed_skills_effect_unknown", sid)
            await log_engine_events(u, screen)
            if should_show_day3_offer(u, day):
                await show_day3_offer(m, u, "day3_auto")
                return
            await send_success_menu(m, u, source="action_done")
            return

        if text == "↩️ Вернулся(лась)" or "вернулся" in low:
            screen = engine_handle_action_result(u, "return")
            u["return_count"] = int(u.get("return_count") or 0) + 1
            mark_day_core_round_done(u)
            mark_current_skill_status(u, "completed")
            set_current_state(u, STATE_PAUSED, close_action=True)
            gamify_apply(u, 1, "return")
            apply_engine_updates(u, screen)
            await save_user(u, DB_PATH)
            return_pattern = "strong_return_skill" if int(u.get("return_count") or 0) >= 2 else "return_after_slip"
            profile = await get_user_profile(u["user_id"], DB_PATH)
            await record_profile_signal(u["user_id"], "training", {
                "return_pattern": return_pattern,
                "slip_pattern": return_pattern,
                "return_count": int(u.get("return_count") or 0),
                **_today_profile_counter_patch(profile, "return_count_today", "return_count_date"),
            }, source="return_after_slip")
            await record_development_avatar_event(u["user_id"], "return_after_slip", DB_PATH, {"return_count": int(u.get("return_count") or 0)})
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
                await show_day3_offer(m, u, "day3_auto")
                return
            if not TEST_MODE and day >= 7 and u.get("trial_phase") in ("trial3", "trial7", None) and not offer_shown_today(u):
                await show_day3_offer(m, u, "trial_paywall_after_return")
                return
            await send_success_menu(m, u, source="return")
            return

        if text in {"❓ Сомневаюсь", "❓ Сомневаюсь, работает ли"} or "сомневаюсь" in low:
            await log_event(u["user_id"], "training", "skeptic_clicked", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, skeptic_text(), kb_skeptic, "skeptic")
            return

        if "не пошло" in low or "не подходит" in low or "не работает" in low or text == "🔁 Заменить навык" or "заменить" in low:
            await log_event(u["user_id"], "training", "skill_replace_requested", {"day": day, "reason": text or "button"}, DB_PATH, SHEETS_WEBHOOK_URL)
            await replace_skill_or_request_rediagnosis(m, u, text or "button")
            return

        await answer_with_keyboard(m, u, "Выбери действие:", kb_training_main, "training_main")
        return

    # crisis stabilization: calm -> skill -> choice
    if u.get("stage") == "crisis_stabilize":
        low = (text or "").lower().strip()
        if text == "✅ Сделал" or low == "сделал":
            await log_event(u["user_id"], "crisis_stabilize", "crisis_stabilize_done", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            pending = {}
            try:
                pending = json.loads(u.get("pending_plan_change") or "{}")
            except Exception:
                pending = {}
            if isinstance(pending, dict) and pending.get("type") == "crisis_voice_pattern" and pending.get("pattern"):
                pattern = str(pending.get("pattern") or "unknown")
                u["pending_plan_change"] = None
                await save_user(u, DB_PATH)
                await send_crisis_tool(m, u, pattern)
                return
            await show_crisis_tool_prompt(m, u)
            return
        if text == "↩️ Вернуться в тренировку" or "вернуться" in low:
            await log_event(u["user_id"], "crisis_stabilize", "crisis_return_blocked_until_safety_done", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(
                m,
                u,
                "Сначала завершим блок безопасности: сделай стабилизацию и нажми ✅ Сделал. После этого я спрошу эффект и только потом верну к тренировке.",
                kb_crisis_stabilize,
                "crisis_stabilize",
            )
            return
        if text == "🆘 Мне всё ещё плохо" or "всё ещё плохо" in low or "все еще плохо" in low:
            await log_event(u["user_id"], "crisis_stabilize", "crisis_still_bad", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, crisis_still_bad_text(), kb_crisis_stabilize, "crisis_stabilize")
            return
        if text == "✍️ Написать, что происходит" or "написать" in low:
            u["stage"] = "crisis_text"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "crisis_stabilize", "crisis_write_opened", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Напиши 1–3 предложения или пришли голосовое: что происходит прямо сейчас?")
            return
        await answer_with_keyboard(m, u, crisis_stabilize_text(), kb_crisis_stabilize, "crisis_stabilize")
        return

    # crisis tool selection: second layer after stabilization
    if u.get("stage") == "crisis_tool_select":
        if m.voice:
            t = await whisper_transcribe(m)
            if not t:
                await m.answer("Я не смог разобрать голос. Напиши 1–2 фразы или выбери кнопками, что ближе.", reply_markup=crisis_multiselect_keyboard(_selected_crisis_patterns(u)))
                return
            u["crisis_text"] = t
            await save_user(u, DB_PATH)
            await send_crisis_tool(m, u, t)
            return
        if not text:
            await m.answer(crisis_tool_prompt_text(), reply_markup=crisis_multiselect_keyboard(_selected_crisis_patterns(u)))
            return
        if text == "✅ Всё выбрал" or "всё выбрал" in (text or "").lower() or "все выбрал" in (text or "").lower():
            selected = _selected_crisis_patterns(u)
            if not selected:
                await m.answer("Выбери хотя бы одно состояние или напиши 1–2 фразы.", reply_markup=crisis_multiselect_keyboard(selected))
                return
            u["pending_plan_change"] = None
            crisis_stack = detect_crisis_stack("", selected)
            if crisis_stack == "HIGH_RISK":
                u["stage"] = CRISIS_WAITING_INPUT
                await save_user(u, DB_PATH)
                await log_event(u["user_id"], "crisis", "crisis_high_risk_buttons", {"selected": selected, "crisis_stack": crisis_stack}, DB_PATH, SHEETS_WEBHOOK_URL)
                await m.answer(crisis_tool_text(crisis_stack))
                return
            set_last_explanation_context(u, "crisis", "Комбинированный кризисный стек", "Ты выбрал несколько состояний, поэтому я собираю порядок: стабилизация → убрать стимул/перегруз → микро-шаг.", selected, "Сделай самый маленький пункт из комбинированного стека.")
            await save_user(u, DB_PATH)
            u["stage"] = "crisis_action_await"
            await save_user(u, DB_PATH)
            await m.answer(combined_crisis_tool_text(selected))
            await answer_with_keyboard(m, u, "Сделай минимум из блока и отметь, что получилось.", kb_crisis_action, "crisis_action")
            return
        button_pattern = _crisis_pattern_from_button(text)
        if button_pattern:
            selected = _selected_crisis_patterns(u)
            if button_pattern in selected:
                selected = [x for x in selected if x != button_pattern]
            else:
                selected.append(button_pattern)
            _save_selected_crisis_patterns(u, selected)
            await save_user(u, DB_PATH)
            await m.answer("Отметил. Можно выбрать ещё или нажать ✅ Всё выбрал.", reply_markup=crisis_multiselect_keyboard(selected))
            return
        u["crisis_text"] = text
        await save_user(u, DB_PATH)
        await send_crisis_tool(m, u, text)
        return

    # crisis waiting input: text and voice share one analyzer
    if u.get("stage") in {"crisis_choose_mode", CRISIS_WAITING_INPUT}:
        low = (text or "").lower().strip()

        # Если сразу прислал голосовое — обрабатываем без лишних шагов
        if m.voice:
            await m.answer("Слушаю голосовое и перевожу в текст…")
            t = await whisper_transcribe(m)
            if t:
                u["crisis_text"] = t
                await save_user(u, DB_PATH)
                await m.answer(f"Распознал: {clamp_str(t, 700)}")
                await send_crisis_tool(m, u, t)
                return
            await m.answer("Я не смог разобрать голос. Напиши 1–2 фразы или выбери кнопками, что ближе.", reply_markup=crisis_multiselect_keyboard([]))
            u["stage"] = "crisis_tool_select"
            await save_user(u, DB_PATH)
            return

        if text == "⬅️ Назад" or "назад" in low:
            await log_event(u["user_id"], "crisis", "crisis_return_blocked_until_safety_done", {"stage": u.get("stage")}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, "Сначала завершим блок безопасности. Выбери способ описать состояние или нажми кнопки.", kb_crisis_mode, "crisis_mode")
            return
        if text in {"🎙 Голосом", "🎙 Кризис голосом"} or "голос" in low:
            u["crisis_input_mode"] = "voice"
            u["stage"] = CRISIS_WAITING_INPUT
            await save_user(u, DB_PATH)
            await m.answer("Пришли голосовое. Я переведу его в текст и передам в тот же кризисный анализатор, что и текст.")
            return
        if text in {"✍️ Текстом", "✍️ Кризис текстом"} or "текст" in low:
            u["crisis_input_mode"] = "text"
            u["stage"] = CRISIS_WAITING_INPUT
            await save_user(u, DB_PATH)
            await m.answer("Напиши 1–3 предложения: что происходит прямо сейчас?")
            return
        if "выбрать" in low or "кноп" in low:
            _save_selected_crisis_patterns(u, [])
            u["stage"] = "crisis_tool_select"
            await save_user(u, DB_PATH)
            await m.answer(crisis_tool_prompt_text(), reply_markup=crisis_multiselect_keyboard([]))
            return
        if text:
            u["crisis_input_mode"] = "text"
            u["crisis_text"] = text
            await save_user(u, DB_PATH)
            await send_crisis_tool(m, u, text)
            return
        await answer_with_keyboard(m, u, crisis_entry_text(), kb_crisis_mode, "crisis_mode")
        return

    if u.get("stage") == "crisis_text":
        if m.voice and not text:
            await m.answer("Слушаю голосовое и перевожу в текст…")
            t = await whisper_transcribe(m)
            if not t:
                await m.answer("Не смог разобрать. Напиши текстом 1–3 предложения или пришли голосовое ещё раз.")
                return
            text = t.strip()
            low = text.lower()
            u["crisis_text"] = text
            await save_user(u, DB_PATH)
            await m.answer(f"Распознал: {clamp_str(text, 700)}")
        if text and text.lower().strip() in {"⬅️ назад", "назад"}:
            await log_event(u["user_id"], "crisis", "crisis_return_blocked_until_safety_done", {"stage": u.get("stage")}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, "Сначала завершим блок безопасности. Напиши 1–3 предложения или пришли голосовое.", kb_crisis_stabilize, "crisis_stabilize")
            return
        if not text:
            await m.answer("Напиши 1–3 предложения или пришли голосовое.")
            return
        u["crisis_text"] = text
        await save_user(u, DB_PATH)
        await send_crisis_tool(m, u, text)
        return

    if u.get("stage") == "crisis_voice":
        if text and text.lower().strip() in {"⬅️ назад", "назад"}:
            await log_event(u["user_id"], "crisis", "crisis_return_blocked_until_safety_done", {"stage": u.get("stage")}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, "Сначала завершим блок безопасности. Напиши 1–3 предложения или пришли голосовое.", kb_crisis_stabilize, "crisis_stabilize")
            return
        if not m.voice:
            await m.answer("Пришли голосовое 🎙")
            return
        t = await whisper_transcribe(m)
        if not t:
            await m.answer("Не смог разобрать. Напиши текстом 1–3 предложения или пришли голосовое ещё раз.")
            u["stage"] = "crisis_text"
            await save_user(u, DB_PATH)
            return
        u["crisis_text"] = t
        await save_user(u, DB_PATH)
        await send_crisis_tool(m, u, t)
        return

    async def _continue_after_social_support(return_stage: str):
        if return_stage == "working_map":
            try:
                comp = json.loads(u.get("analysis_json") or "{}")
            except Exception:
                comp = {}
            comp = comp if isinstance(comp, dict) else {}
            u["stage"] = "working_map"
            u["day"] = 1
            ensure_first_start_date(u)
            u["pending_plan_change"] = None
            set_last_explanation_context(
                u,
                "map",
                "рабочая карта",
                "Карта показывает не диагноз, а рабочую схему: где стопор, что запускает избегание и какой навык проверяем первым.",
                ["учтён блок социальных опор", "карта будет уточняться по действиям", "важны повторяющиеся сигналы, а не один идеальный ответ"],
                "Нажми «Давай действие», чтобы проверить карту практикой."
            )
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, preliminary_development_map_from_analysis(comp), kb_working_map, "working_map")
            return
        if return_stage == "start_day":
            u["stage"] = "waiting_next_day"
            u["day"] = 1
            ensure_first_start_date(u)
            u["pending_plan_change"] = None
            await save_user(u, DB_PATH)
            await start_day(m, u, calendar_program_day(u), DB_PATH, SHEETS_WEBHOOK_URL)
            return
        u["stage"] = "waiting_next_day"
        u["pending_plan_change"] = None
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Возвращаемся к основному навыку дня.", kb_training_main, "training_main")

    if u.get("stage") == "social_support_await":
        value = (text or "").strip()
        if not value:
            await answer_with_keyboard(m, u, social_support_prompt_text(), kb_social_support, "social_support")
            return
        no_support = "нет опоры" in value.lower()
        presence_help = "среди людей" in value.lower()
        external_start = any(x in value.lower() for x in ("коллег", "партн", "чат", "групп", "комьюнити", "человек", "написать"))
        short_report = not no_support and any(x in value.lower() for x in ("человек", "коллег", "партн", "сем", "близ", "чат", "групп", "комьюнити"))
        patch = {
            "social_support_choice": value,
            "social_support_available": 0 if no_support else 1,
            "social_support_notes": value,
            "social_support_prompt_shown": 1,
            "social_support_can_message": 0 if no_support else int(any(x in value.lower() for x in ("написать", "человек", "коллег", "партн", "сем", "близ", "чат", "групп", "комьюнити"))),
            "social_support_presence_helps": int(presence_help),
            "social_support_external_start": int(external_start),
            "social_support_short_report": int(short_report),
            "social_support_map": social_support_map_text(),
        }
        await update_user_profile(u["user_id"], patch, DB_PATH, source="social_support")
        await log_event(u["user_id"], "social_support", "social_support_recorded", patch, DB_PATH, SHEETS_WEBHOOK_URL)
        try:
            pending = json.loads(u.get("pending_plan_change") or "{}")
        except Exception:
            pending = {}
        if isinstance(pending, dict) and pending.get("type") == "crisis_aftercare":
            u["pending_plan_change"] = None
            await save_user(u, DB_PATH)
            await show_safety_support(m, u, "social_support_after_crisis")
            return
        return_stage = pending.get("return_stage") if isinstance(pending, dict) and pending.get("type") == "social_support" else "waiting_next_day"
        if no_support:
            await m.answer(
                "Ок. Тогда пока не строим план на поддержке других людей. Будем искать автономные опоры: среда, таймер, маленький шаг, ритуал старта.\n\n"
                f"{social_support_map_text()}"
            )
        else:
            await m.answer(f"Записал в карту.\n\n{social_support_map_text()}")
        await _continue_after_social_support(str(return_stage or "waiting_next_day"))
        return

    if u.get("stage") == "crisis_action_await":
        low = (text or "").lower().strip()
        pattern = u.get("pending_crisis_pattern") or "unknown"
        if text in {"✅ Сделал", "✅ Написал"} or "сделал" in low or "написал" in low or "вернулся" in low:
            profile = await get_user_profile(u["user_id"], DB_PATH)
            recorded_return = await record_return_after_stuck_if_needed(u, profile, "crisis_action_done")
            u["stage"] = "crisis_effect_await"
            await save_user(u, DB_PATH)
            effect_prompt = crisis_effect_prompt_text()
            if recorded_return:
                effect_prompt = f"{return_after_stuck_text()}\n\n{effect_prompt}"
            await answer_with_keyboard(m, u, effect_prompt, kb_crisis_effect, "crisis_effect")
            return
        if text == "😣 Не могу" or "не могу" in low:
            await log_event(u["user_id"], "crisis", "crisis_action_blocked", {"pattern": pattern}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(
                m,
                u,
                "Ок. Тогда не давим. Сделай только минимум: один выдох / один клик / открыть чат / сесть и глоток воды. Потом нажми ✅ Сделал.",
                kb_crisis_action,
                "crisis_action",
            )
            return
        if text == "🧩 Ещё меньше" or "ещё меньше" in low or "еще меньше" in low:
            await log_event(u["user_id"], "crisis", "crisis_downscale_requested", {"pattern": pattern}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(
                m,
                u,
                "Ещё меньше: не решай задачу. Только один телесный или физический микрошаг: выдох, глоток воды, свернуть экран, открыть файл или открыть чат.",
                kb_crisis_action,
                "crisis_action",
            )
            return
        if text == "🆘 Мне всё ещё плохо" or "всё ещё плохо" in low or "все еще плохо" in low:
            u["stage"] = "crisis_stabilize"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, crisis_still_bad_text(), kb_crisis_stabilize, "crisis_stabilize")
            return
        await answer_with_keyboard(m, u, "Выбери кнопку: ✅ Сделал / 😣 Не могу / 🧩 Ещё меньше / 🆘 Мне всё ещё плохо", kb_crisis_action, "crisis_action")
        return

    if u.get("stage") == "crisis_effect_await":
        effect = crisis_effect_code(text)
        profile = await get_user_profile(u["user_id"], DB_PATH)
        pattern = u.get("pending_crisis_pattern") or profile.get("last_crisis_pattern") or "unknown"
        skill = u.get("pending_crisis_skill") or profile.get("last_crisis_skill") or crisis_skill_for_pattern(pattern)
        total = int(profile.get("crisis_effect_count") or 0) + 1
        success = int(profile.get("crisis_effect_success_count") or 0) + (1 if effect == "better" else 0)
        skill_success_counts = _crisis_skill_counts(profile)
        if effect == "better":
            skill_success_counts[skill] = skill_success_counts.get(skill, 0) + 1
        patch = {
            "crisis_pattern": pattern,
            "crisis_skill": skill,
            "crisis_effect": effect,
            "last_crisis_effect": effect,
            "most_effective_crisis_skill": _top_key(skill_success_counts) or skill,
            "crisis_skill_success_counts": skill_success_counts,
            "crisis_effect_count": total,
            "crisis_effect_success_count": success,
            "crisis_success_rate": round(success / total, 2) if total else 0,
        }
        await record_profile_signal(u["user_id"], "crisis", patch, source="crisis_effect")
        await log_event(u["user_id"], "crisis", "crisis_effect_recorded", {"crisis_pattern": pattern, "crisis_skill": skill, "crisis_effect": effect}, DB_PATH, SHEETS_WEBHOOK_URL)
        if effect == "worse":
            u["stage"] = "crisis_stabilize"
            u["pending_crisis_pattern"] = None
            u["pending_crisis_skill"] = None
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, crisis_still_bad_text(), kb_crisis_stabilize, "crisis_stabilize")
            return
        profile_after = await get_user_profile(u["user_id"], DB_PATH)
        if not profile_after.get("social_support_prompt_shown"):
            u["stage"] = "social_support_await"
            u["pending_plan_change"] = json.dumps({"type": "crisis_aftercare"}, ensure_ascii=False)
            u["pending_crisis_pattern"] = None
            u["pending_crisis_skill"] = None
            await update_user_profile(u["user_id"], {"social_support_prompt_shown": 1}, DB_PATH, source="social_support_prompt")
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, social_support_prompt_text(), kb_social_support, "social_support")
            return
        u["pending_crisis_pattern"] = None
        u["pending_crisis_skill"] = None
        await show_safety_support(m, u, "crisis_effect_completed")
        await log_event(u["user_id"], "crisis", "crisis_productivity_return_deferred_until_safety_aftercare", {"effect": effect}, DB_PATH, SHEETS_WEBHOOK_URL)
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
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Возвращаемся в тренировку.", kb_training_main, "training_main")
            return
        if text == "❌ Нет" or "нет" in low:
            u["pending_plan_change"] = None
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], u.get("stage", ""), "plan_change_reject", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, "Ок. План не меняю. Возвращаемся.", kb_training_main, "training_main")
            return
        await m.answer("Выбери: ✅ Да / ❌ Нет", reply_markup=kb_yes_no)
        return

    # OFFER stage
    if u.get("stage") == "offer":
        low = text.lower().strip()
        if text in {"💳 Продолжить полный режим", "💳 Продолжить за €14.98", "💳 Месяц — €14.98"} or "полный режим" in low or "месяц" in low or "€14.98" in low or "14.98" == low:
            await log_event(u["user_id"], "offer", "payment_click_month_1498", {"payment_click": "month_1498", "amount": 14.98}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["payment_status"] = "pending_month_1498"
            u["last_payment_click"] = "month_14_98"
            await save_user(u, DB_PATH)
            pay_url = payment_month_url()
            profile = await get_user_profile(u["user_id"], DB_PATH)
            profile["_skill_map"] = await build_skill_map_data(u, profile)
            payment_intro = day3_personal_offer_text(build_profile_map_summary(u, profile), profile)
            if pay_url:
                await m.answer(f"{payment_intro}\n\nНажми кнопку ниже для оплаты.")
                await m.answer(" ", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 Продолжить за €14.98", url=pay_url)]]))
                if PAYMENT_ACCEPT_ANY:
                    await answer_with_inline_screen(m, u, "Для теста: после любой успешной оплаты по ссылке нажми подтверждение ниже — я засчитаю её как правильную.", test_payment_confirm_keyboard(), "offer")
            else:
                await log_event(u["user_id"], "offer", "payment_error", {"error_type": "payment_url_missing", "payment_click": "month_1498", "amount": 14.98}, DB_PATH, SHEETS_WEBHOOK_URL)
                await log_event(u["user_id"], "offer", "payment_stub_shown", {"price_month": "14.98"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await m.answer(payment_month_1498_stub_text())
            return
        if is_action_request(text, low):
            await handle_action_request(u["user_id"], m, u)
            return
        if text in {"🌙 Хватит на сегодня", "🌙 Закрыть день"} or "хватит" in low:
            u["stage"] = "day_pause_confirm"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Закрыть день или просто сделать паузу?", kb_day_pause_confirm, "day_pause_confirm")
            return
        if text in {"🤔 Остаться в коротком режиме", "🤔 Подумаю"} or "коротком режиме" in low or "подумаю" in low:
            await log_event(u["user_id"], "offer", "payment_declined_soft", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await log_event(u["user_id"], "offer", "free_mode_started", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["free_mode"] = 1
            u["payment_status"] = "free_mode"
            u["stage"] = "feedback_offer"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(
                m,
                u,
                "Это нормально. Короткий режим остаётся.\nДля теста скажи: чего пока не хватает, чтобы полный режим выглядел нужным?",
                kb_feedback_offer,
                "feedback_offer",
            )
            return
        if text in {"📚 Что входит", "📚 Подробнее о карте", "📚 Что будет дальше"} or "что входит" in low or "подробнее" in low or "что будет дальше" in low:
            await log_event(u["user_id"], "offer", "profile_map_details_opened", {"price_month": "14.98"}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_inline_screen(m, u, offer_details_full_mode_text(), offer_details_inline_keyboard(u["user_id"]), "offer")
            return
        if text in {"🧭 Показать карту", "🧭 Показать мои сигналы", "🧭 Показать карту ещё раз", "🧭 Показать мою карту"} or "показать" in low and ("сигнал" in low or "карт" in low):
            profile = await get_user_profile(u["user_id"], DB_PATH)
            profile["_skill_map"] = await build_skill_map_data(u, profile)
            await log_event(u["user_id"], "offer", "profile_signals_opened", {"source": "offer"}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_inline_screen(m, u, trainer_wrap(u, render_short_user_map(profile, u.get("name")), "map"), offer_inline_keyboard(u["user_id"]), "offer")
            return
        if text == "⬅️ Назад" or "назад" in low:
            profile = await get_user_profile(u["user_id"], DB_PATH)
            profile["_skill_map"] = await build_skill_map_data(u, profile)
            summary = build_profile_map_summary(u, profile)
            await answer_with_inline_screen(m, u, trainer_wrap(u, day3_conclusion_and_map_text(summary, profile), "offer"), offer_inline_keyboard(u["user_id"]), "offer")
            return
        await show_context_fallback(m, u, "offer_invalid_button")
        return

    # Если дошли до сюда — неизвестный этап. Не сбрасываем пользователя в /start:
    # возвращаем к безопасному меню текущего дня.
    raw_stage = str(u.get("stage") or "")
    if raw_stage != "post_done_reflection":
        await log_event(
            u["user_id"],
            raw_stage,
            "unknown_stage_recovered",
            {"stage": raw_stage, "message_text": text or ""},
            DB_PATH,
            SHEETS_WEBHOOK_URL,
        )
        u["stage"] = "day_menu"
        await save_user(u, DB_PATH)
        await answer_with_keyboard(
            m,
            u,
            "Кажется, я потерял место в маршруте. Возвращаю тебя к текущему дню.\n\nЧто сейчас сделать?",
            kb_day_menu,
            "day_menu",
        )

    if is_known_reply_button(text):
        await show_context_fallback(m, u, "known_button_invalid_for_stage")
        return



STALE_CALLBACK_TEXT = (
    "Этот шаг уже завершён.\n"
    "Продолжаем с текущего места."
)

LOST_CALLBACK_TEXT = (
    "Я потерял один ответ и не хочу гадать.\n"
    "Что сейчас сильнее мешает начать?"
)

kb_lost_callback = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="😬 Страх ошибки / оценки")],
        [KeyboardButton(text="📱 Ушёл в телефон")],
        [KeyboardButton(text="😵 Слишком тяжело")],
        [KeyboardButton(text="🌀 Слишком много вариантов")],
        [KeyboardButton(text="😶 Не понимаю, с чего начать")],
    ],
    resize_keyboard=True,
)


def new_screen_id(prefix: str = "scr") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def callback_with_screen(action: str, screen_id: str) -> str:
    return f"{action}|sid:{screen_id}"


def split_screen_callback(data: str) -> tuple[str, str]:
    data = data or ""
    marker = "|sid:"
    if marker not in data:
        return data, ""
    action, sid = data.rsplit(marker, 1)
    return action, sid


def bind_inline_screen(markup: InlineKeyboardMarkup, screen_id: str) -> InlineKeyboardMarkup:
    rows = []
    for row in markup.inline_keyboard:
        bound_row = []
        for button in row:
            cb = getattr(button, "callback_data", None)
            if cb:
                bound_row.append(InlineKeyboardButton(text=button.text, callback_data=callback_with_screen(cb, screen_id)))
            else:
                bound_row.append(button)
        rows.append(bound_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def set_active_screen(u: Dict[str, Any], screen_id: str):
    u["current_screen_id"] = screen_id
    await save_user(u, DB_PATH)


async def reject_stale_callback(c: CallbackQuery, u: Dict[str, Any], source: str):
    await log_event(u["user_id"], u.get("stage", ""), "stale_callback_ignored", {"source": source}, DB_PATH, SHEETS_WEBHOOK_URL)
    await c.message.answer(STALE_CALLBACK_TEXT, reply_markup=kb_context_fallback)
    await c.answer()


async def reject_lost_callback(c: CallbackQuery, u: Dict[str, Any], source: str):
    await log_event(u["user_id"], u.get("stage", ""), "callback_without_current_screen", {"source": source}, DB_PATH, SHEETS_WEBHOOK_URL)
    u["stage"] = "failed_options"
    set_current_state(u, STATE_AWAITING_STUCK_REASON)
    await save_user(u, DB_PATH)
    await c.message.answer(LOST_CALLBACK_TEXT, reply_markup=kb_lost_callback)
    await c.answer()


async def validate_callback_screen(c: CallbackQuery, u: Dict[str, Any], source: str) -> tuple[bool, str]:
    action, screen_id = split_screen_callback(c.data or "")
    current = u.get("current_screen_id") or ""
    if not screen_id or not current:
        await reject_lost_callback(c, u, source)
        return False, action
    if screen_id != current:
        await reject_stale_callback(c, u, source)
        return False, action
    u["current_screen_id"] = ""
    await save_user(u, DB_PATH)
    return True, action


async def answer_with_inline_screen(m: Message, u: Dict[str, Any], text: str, markup: InlineKeyboardMarkup, prefix: str):
    screen_id = new_screen_id(prefix)
    await set_active_screen(u, screen_id)
    await m.answer(text, reply_markup=bind_inline_screen(markup, screen_id))


async def edit_with_inline_screen(message, u: Dict[str, Any], text: str, markup: InlineKeyboardMarkup, prefix: str):
    screen_id = new_screen_id(prefix)
    await set_active_screen(u, screen_id)
    await message.edit_text(text, reply_markup=bind_inline_screen(markup, screen_id))

# ============================================================
# CALLBACKS
# ============================================================

@router.callback_query(lambda c: split_screen_callback(c.data or "")[0] in {"offer_details", "show_map", "stay_free", "continue_free", "test_payment", "confirm_test_payment"})
async def on_offer_callbacks(c: CallbackQuery):
    uid = c.from_user.id
    u = await get_user(uid, DB_PATH)
    data = c.data or ""

    if await handle_safety_callback(c, u, data):
        return
    valid, data = await validate_callback_screen(c, u, "offer")
    if not valid:
        return

    if u.get("stage") != "offer" and data != "confirm_test_payment":
        await reject_lost_callback(c, u, "offer_stage_mismatch")
        return

    if data == "offer_details":
        await log_event(uid, "offer", "profile_map_details_opened", {"price_month": "14.98", "source": "inline"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_inline_screen(c.message, u, offer_details_full_mode_text(), offer_details_inline_keyboard(uid), "offer")
        await c.answer()
        return

    if data == "show_map":
        profile = await get_user_profile(uid, DB_PATH)
        profile["_skill_map"] = await build_skill_map_data(u, profile)
        await log_event(uid, "offer", "profile_signals_opened", {"source": "inline_offer"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_inline_screen(c.message, u, trainer_wrap(u, render_short_user_map(profile, u.get("name")), "map"), offer_inline_keyboard(uid), "offer")
        await c.answer()
        return

    if data == "stay_free":
        await log_event(uid, "offer", "payment_declined_soft", {"source": "inline"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await log_event(uid, "offer", "free_mode_started", {"source": "inline"}, DB_PATH, SHEETS_WEBHOOK_URL)
        u["free_mode"] = 1
        u["payment_status"] = "free_mode"
        u["stage"] = "feedback_offer"
        await save_user(u, DB_PATH)
        await c.message.answer(
            "Это нормально. Короткий режим остаётся.\nДля теста скажи: чего пока не хватает, чтобы полный режим выглядел нужным?",
            reply_markup=kb_feedback_offer,
        )
        await c.answer()
        return

    if data == "continue_free":
        u["stage"] = "waiting_next_day"
        await save_user(u, DB_PATH)
        await c.message.answer(stay_free_text(), reply_markup=kb_short_mode_main)
        await c.answer()
        return

    if data == "confirm_test_payment":
        if PAYMENT_ACCEPT_ANY:
            await grant_paid_access(u, "test_payment_confirm_button", {"accept_any_payment": True})
            await send_full_mode_welcome(c.message, u)
        else:
            await c.message.answer("Автоподтверждение оплаты выключено. Нужен PAYMENT_ACCEPT_ANY=1 или админская /mark_paid.")
        await c.answer()
        return

    if data == "test_payment":
        if is_admin(uid):
            await c.message.answer("Тестовая оплата", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🧪 Тестовая оплата", url=PAYMENT_TEST_URL or payment_month_url())]]))
        else:
            await c.message.answer("Выбери действие 👇", reply_markup=kb_training_main)
        await c.answer()
        return


@router.callback_query(lambda c: split_screen_callback(c.data or "")[0] in {"yes", "no", "noop"})
async def on_callbacks(c: CallbackQuery):
    uid = c.from_user.id
    u = await get_user(uid, DB_PATH)
    if await handle_safety_callback(c, u, c.data or ""):
        return
    valid, data = await validate_callback_screen(c, u, "yes_no")
    if not valid:
        return
    if data == "noop":
        await reject_lost_callback(c, u, "noop")
        return
    if u.get("stage") == "confirm_analysis":
        if data == "yes":
            try:
                comp = json.loads(u.get("analysis_json") or "{}")
            except Exception:
                comp = {}
            comp = comp if isinstance(comp, dict) else {}
            updated_profile = await update_user_profile(u["user_id"], working_map_profile_patch(comp), DB_PATH, source="working_map_confirmed")
            u["profile_json"] = updated_profile
            if not updated_profile.get("social_support_prompt_shown"):
                u["stage"] = "social_support_await"
                u["pending_plan_change"] = json.dumps({"type": "social_support", "return_stage": "working_map"}, ensure_ascii=False)
                await update_user_profile(u["user_id"], {"social_support_prompt_shown": 1}, DB_PATH, source="social_support_prompt")
                await save_user(u, DB_PATH)
                await answer_with_keyboard(c.message, u, social_support_prompt_text(), kb_social_support, "social_support")
            else:
                u["stage"] = "working_map"
                u["day"] = 1
                ensure_first_start_date(u)
                set_last_explanation_context(
                    u,
                    "map",
                    "рабочая карта",
                    "Карта показывает не диагноз, а рабочую схему: где стопор, что запускает избегание и какой навык проверяем первым.",
                    ["гипотеза подтверждена пользователем", "карта будет уточняться по действиям", "важны повторяющиеся сигналы, а не один идеальный ответ"],
                    "Нажми «Давай действие», чтобы проверить карту практикой."
                )
                await save_user(u, DB_PATH)
                await answer_with_keyboard(
                    c.message,
                    u,
                    preliminary_development_map_from_analysis(comp),
                    kb_working_map,
                    "working_map",
                )
        else:
            u["stage"] = "await_problem_text"
            await save_user(u, DB_PATH)
            await c.message.answer("Ок. Тогда уточни: что больше всего мешает? (2–3 предложения)")
        await c.answer()
        return
    await reject_lost_callback(c, u, "yes_no_stage_mismatch")

@router.callback_query(lambda c: split_screen_callback(c.data or "")[0].startswith("test_q"))
async def on_test_answer(c: CallbackQuery):
    uid = c.from_user.id
    u = await get_user(uid, DB_PATH)
    if await handle_safety_callback(c, u, c.data or ""):
        return
    valid, data = await validate_callback_screen(c, u, "test")
    if not valid:
        return
    if u.get("stage") != "taking_test":
        await reject_lost_callback(c, u, "test_stage_mismatch")
        return
    try:
        parts = data.split("_")
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
                await edit_with_inline_screen(c.message, u, f"❓ Вопрос {next_q_num}/5:\n\n{next_q['text']}", create_test_question_keyboard(next_q_num), "test")
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

@router.callback_query()
async def on_unknown_callback(c: CallbackQuery):
    uid = c.from_user.id
    u = await get_user(uid, DB_PATH)
    if await handle_safety_callback(c, u, c.data or ""):
        return
    await reject_lost_callback(c, u, "unknown")

async def show_comprehensive_analysis(m: Message, u: Dict[str, Any]):
    bucket = u.get("bucket") or "mixed"
    user_text = stored_analysis_user_text(u) or f"У меня проблемы с {bucket}"
    comp = await ai_analyze_comprehensive(user_text, u.get("trainer_key", "marsha"), client, OPENAI_CHAT_MODEL)
    comp = normalize_analysis(comp, user_text)
    comp["trainer_key"] = u.get("trainer_key", "marsha")
    if comp.get("analysis_fallback"):
        await log_event(u["user_id"], "analysis", "openai_error", {"error_type": "analysis_fallback", "error_source": "show_comprehensive_analysis"}, DB_PATH, SHEETS_WEBHOOK_URL)
    comp.pop("user_text", None)
    comp.update(safe_analysis_memory(user_text, comp))
    analysis_result = build_analysis_result(comp, user_text)
    comp["analysis_result"] = analysis_result
    u["analysis_json"] = json.dumps(comp, ensure_ascii=False)
    u["bucket"] = comp.get("bucket", bucket)
    plan_ids = build_28_day_plan(u["bucket"])
    recommended_variant = analysis_result.get("recommended_variant")
    if recommended_variant in SKILLS_DB:
        plan_ids[0] = recommended_variant
    if comp.get("analysis_fallback") and "open_only" in SKILLS_DB and recommended_variant not in SKILLS_DB:
        plan_ids[0] = "open_only"
    u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
    u["day"] = 1
    u["stage"] = "confirm_analysis"
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "analysis", "diagnosis_completed", {"bucket": u.get("bucket")}, DB_PATH, SHEETS_WEBHOOK_URL)
    diagnosis_profile_patch = {**profile_patch_from_diagnosis(comp), **live_analysis_profile_patch(str(comp.get("live_pattern") or ""))}
    updated_profile = await update_user_profile(u["user_id"], diagnosis_profile_patch, DB_PATH, source="initial_map")
    u["profile_json"] = updated_profile
    await log_event(
        u["user_id"],
        "analysis",
        "profile_signal_detected",
        {"source": "initial_map", **diagnosis_profile_patch},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    await log_event(
        u["user_id"],
        "analysis",
        "profile_map_updated",
        {"source": "initial_map", **diagnosis_profile_patch},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    await log_event(u["user_id"], "analysis", "recommended_track_shown", {"recommended_track": "procrastination"}, DB_PATH, SHEETS_WEBHOOK_URL)
    await log_event(u["user_id"], "analysis", "analysis_shown", {"bucket": u.get("bucket")}, DB_PATH, SHEETS_WEBHOOK_URL)
    comp_for_message = dict(comp)
    preliminary_conclusion = preliminary_diagnosis_conclusion_text(
        comp_for_message.get("specific_pattern") or comp_for_message.get("live_pattern") or "",
        comp_for_message.get("useful_signal") or "",
        comp_for_message.get("skills_focus") if isinstance(comp_for_message.get("skills_focus"), list) else [],
        (analysis_result.get("first_check") or analysis_result.get("recommended_skill_name") or ""),
        (analysis_result.get("recommended_skill_reason") or ""),
    )
    set_last_explanation_context(
        u,
        "hypothesis",
        comp_for_message.get("specific_pattern") or comp_for_message.get("live_pattern") or "рабочая карта прокрастинации",
        "Это рабочая гипотеза по твоему описанию: я связываю повторяющийся стопор, избегание и первый навык для проверки.",
        [str(x) for x in (comp_for_message.get("skills_focus") or [])[:3] if x],
        "Подтверди, похоже ли это на тебя, или нажми «Подробнее», чтобы разобрать гипотезу."
    )
    await save_user(u, DB_PATH)
    msg = f"{preliminary_conclusion}\n\nЭто похоже на тебя?"
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
        "keyboard_shown" if button_count <= MAX_KEYBOARD_BUTTONS else "keyboard_warning",
        {"keyboard": keyboard_name, "button_count": button_count, "source": "background"},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    try:
        await bot.send_message(
            u["chat_id"],
            text,
            reply_markup=reply_markup if button_count <= MAX_KEYBOARD_BUTTONS else None,
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
