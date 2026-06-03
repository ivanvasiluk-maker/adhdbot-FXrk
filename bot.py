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
    get_user_profile, update_user_profile, render_short_user_map, label, SKILL_LABELS
)
from flows import (
    start_day, start_day1, start_day_simple, advance_day, handle_crisis,
    send_trainer_photo_if_any, run_analysis,
    send_weekly_summary, send_progress_report, ai_analyze, ai_analyze_comprehensive,
    format_comprehensive_analysis, normalize_analysis, safe_analysis_memory, _extract_json, clamp_str,
    live_analysis_profile_patch, render_analysis_details_by_trainer
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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini").strip()
OPENAI_WHISPER_MODEL = os.getenv("OPENAI_WHISPER_MODEL", "whisper-1").strip()
DB_PATH = os.getenv("DB_PATH", "bot.db").strip()
PAYMENT_URL = os.getenv("PAYMENT_URL", "").strip()
PAYMENT_URL_DISCOUNT = os.getenv("PAYMENT_URL_DISCOUNT", "").strip()
PAYMENT_URL_FULL = os.getenv("PAYMENT_URL_FULL", "").strip()
PAYMENT_URL_MONTH_1498 = os.getenv("PAYMENT_URL_MONTH_1498", "").strip()
ENABLE_PAYMENTS = os.getenv("ENABLE_PAYMENTS", "").lower() in {"1", "true", "yes", "on"}
SHEETS_WEBHOOK_URL = os.getenv("SHEETS_WEBHOOK_URL", "").strip()

# Unlock full flow while testing (set TEST_MODE=1)
TEST_MODE = os.getenv("TEST_MODE", "").lower() in {"1", "true", "yes", "on", "debug"}
TEST_CHEAT_CODE = os.getenv("TEST_CHEAT_CODE", "SKILLER_TEST_1498").strip()
STARTUP_CHECK = os.getenv("BOT_STARTUP_CHECK", "").lower() in {"1", "true", "yes", "on", "check"}
MAX_CRISIS_MATCHES_PER_DAY = 3

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
MAX_SKILL_REPLACEMENTS_BEFORE_REDIAGNOSIS = 3

ACTION_RELATED_STAGES = {
    "training",
    "await_training_target",
    "action_clarification",
    "downscale_action",
    "downscale_name_task",
    "failed_options",
    "skip_options",
}


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
    u["day_core_skill_id"] = None
    u["day_core_skill_date"] = None
    u["day_core_round_count"] = 0
    u["current_core_skill_id"] = None
    u["current_skill_variant_id"] = None
    u["current_core_skill_date"] = None


def _skill_label(skill_id: Optional[str], fallback: str = "маленький вход") -> str:
    return label(SKILL_LABELS, skill_id, fallback) if skill_id else fallback


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
        "best_skills_text": _best_skills_text(profile),
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
    await update_user_profile(user_id, safe_patch, DB_PATH)
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


def analysis_loading_text(trainer_key: str) -> str:
    if trainer_key == "skinny":
        return "Ок. Смотрю, где ломается вход."
    if trainer_key == "beck":
        return "Ок. Смотрю механизм."
    if trainer_key == "marsha":
        return "Ок. Давай аккуратно посмотрим, где стало тяжело."
    return "Ок. Смотрю паттерн…"


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

def done_flow_text(include_system_line: bool = False, u: Optional[Dict[str, Any]] = None, profile: Optional[Dict[str, Any]] = None) -> str:
    text = "Есть.\n\nПодход засчитан."
    if u is not None:
        profile = profile or {}
        done_today = int(u.get("day_core_round_count") or 0)
        return_today = int(profile.get("return_count_today") or profile.get("return_count") or 0)
        failed_today = int(profile.get("failed_reason_count_today") or profile.get("action_failed_count_today") or 0)
        text += (
            "\n\nСегодня уже:\n"
            f"✔ {done_today} запуск\n"
            f"✔ {failed_today} срывов не стали концом\n"
            f"✔ {return_today} возврат"
        )
    if include_system_line:
        text += "\n\nЭто данные."
    return text


def today_progress_text(u: Dict[str, Any], profile: Dict[str, Any]) -> str:
    done_today = int(u.get("day_core_round_count") or 0)
    downscale_today = int(profile.get("downscale_count_today") or 0)
    return_today = int(profile.get("return_count_today") or 0)
    failed_today = int(profile.get("failed_reason_count_today") or profile.get("action_failed_count_today") or 0)
    return (
        "Сегодня уже:\n"
        f"— {done_today} подхода\n"
        f"— {downscale_today} раз уменьшили шаг\n"
        f"— {return_today} возврата\n"
        f"— {failed_today} сбоя не стали концом\n\n"
        "Это уже тренировка, а не “ничего не сделал”."
    )


def _today_profile_counter_patch(profile: Dict[str, Any], counter_key: str, date_key: str) -> Dict[str, Any]:
    today = _today_iso()
    current = int(profile.get(counter_key) or 0) if profile.get(date_key) == today else 0
    return {date_key: today, counter_key: current + 1}




def crisis_pattern_from_text(text: str) -> str:
    low = (text or "").lower().strip()
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


crisis_tool_reason_from_text = crisis_pattern_from_text


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
    return is_paid(u)


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
    if "👍" in text or low in {"да", "yes", "y"} or "легче" in low:
        return "better"
    if "👎" in text or low in {"нет", "no", "n"} or "хуже" in low:
        return "no"
    return "same"


async def classify_crisis_pattern(reason_text: str) -> str:
    fallback = crisis_pattern_from_text(reason_text)
    if not AI_ANALYSIS_ENABLED or client is None or not reason_text:
        return fallback
    try:
        prompt = (
            "Classify this crisis message into exactly one code: "
            "attention_escape, task_entry_block, perfectionism, overwhelm, low_energy, self_attack, anxiety_loop, unknown. "
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
        allowed = {"attention_escape", "task_entry_block", "perfectionism", "overwhelm", "low_energy", "self_attack", "anxiety_loop", "unknown"}
        return code if code in allowed else fallback
    except Exception as e:
        log.warning("crisis_pattern_ai_failed: %s", e)
        return fallback


async def show_crisis_entry(m: Message, u: Dict[str, Any], source: str):
    u["stage"] = "crisis_choose_mode"
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "crisis", "crisis_entry_shown", {"source": source}, DB_PATH, SHEETS_WEBHOOK_URL)
    await answer_with_keyboard(m, u, crisis_entry_text(), kb_crisis_mode, "crisis_mode")


async def show_crisis_tool_prompt(m: Message, u: Dict[str, Any]):
    u["stage"] = "crisis_tool_select"
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "crisis", "crisis_tool_prompt_shown", {}, DB_PATH, SHEETS_WEBHOOK_URL)
    await m.answer(crisis_tool_prompt_text(), reply_markup=kb_crisis_tool_select)


async def send_crisis_tool(m: Message, u: Dict[str, Any], reason_text: str):
    profile = await get_user_profile(u["user_id"], DB_PATH)
    count = _crisis_tool_count_today(profile, u)
    if not crisis_paid_unlimited(u) and count >= MAX_CRISIS_MATCHES_PER_DAY:
        u["stage"] = "waiting_next_day"
        await save_user(u, DB_PATH)
        await log_event(u["user_id"], "crisis", "crisis_tool_limit_reached", {"count": count}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(m, u, crisis_tool_limit_text(), kb_training_main, "training_main")
        return
    pattern = await classify_crisis_pattern(reason_text)
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
        "crisis_skill": skill,
        "last_crisis_pattern": pattern,
        "last_crisis_skill": skill,
        "most_common_crisis_pattern": _top_key(pattern_counts),
        "crisis_pattern_counts": pattern_counts,
        "crisis_count": crisis_count,
    }
    await record_profile_signal(u["user_id"], "crisis", patch, source="crisis_tool")
    await log_event(u["user_id"], "crisis", "crisis_tool_selected", {"crisis_pattern": pattern, "crisis_skill": skill, "count": count + 1}, DB_PATH, SHEETS_WEBHOOK_URL)
    u["stage"] = "crisis_effect_await"
    u["crisis_count"] = crisis_count
    u["pending_crisis_pattern"] = pattern
    u["pending_crisis_skill"] = skill
    await save_user(u, DB_PATH)
    await m.answer(crisis_tool_text(pattern))
    await answer_with_keyboard(m, u, crisis_effect_prompt_text(), kb_crisis_effect, "crisis_effect")

async def send_crisis_stabilize(m: Message, u: Dict[str, Any], source: str):
    u["stage"] = "crisis_stabilize"
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

    msg = format_comprehensive_analysis(comp)
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
    new_sid = suggest_alternative_skill(track, current_sid) or current_sid
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
    suggested = suggest_alternative_skill(track, current_sid)
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


async def replace_skill_or_request_rediagnosis(m: Message, u: Dict[str, Any], reason: str) -> bool:
    profile = await get_user_profile(u["user_id"], DB_PATH)
    today = local_date_for_user(u)
    if not day_core_test_mode_enabled(u):
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
            f"Вариация текущего навыка:\n\n{format_skill_card(u, skill, u.get('today_target') or 'текущая задача')}",
            kb_skill_card,
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
    new_sid = choose_replacement_skill(u, seen_today)
    plan = get_current_plan(u) or build_28_day_plan(u.get("bucket") or "mixed")
    day = int(u.get("day") or 1)
    idx = max(0, min(len(plan) - 1, day - 1))
    plan[idx] = new_sid
    u["plan_json"] = json.dumps(plan, ensure_ascii=False)
    u["stage"] = "training"
    replace_day_core_skill(u, new_sid)
    await save_user(u, DB_PATH)

    seen_today.append(new_sid)
    await record_profile_signal(u["user_id"], "training", {
        "skill_replace_date": today,
        "skill_replace_count_today": count + 1,
        "skill_replace_seen_today": seen_today[-MAX_SKILL_REPLACEMENTS_BEFORE_REDIAGNOSIS:],
        "last_replacement_skill": new_sid,
    }, source="skill_replace")
    await log_event(u["user_id"], "training", "skill_replaced", {
        "skill_id": new_sid,
        "count": count + 1,
        "max_count": MAX_SKILL_REPLACEMENTS_BEFORE_REDIAGNOSIS,
        "reason": reason,
    }, DB_PATH, SHEETS_WEBHOOK_URL)

    skill = dict(SKILLS_DB[new_sid])
    skill.setdefault("skill_id", new_sid)
    text = (
        f"Ок. Предлагаю другой навык. Замена {count + 1}/{MAX_SKILL_REPLACEMENTS_BEFORE_REDIAGNOSIS}.\n\n"
        f"{format_skill_card(u, skill, u.get('today_target') or 'текущая задача')}"
    )
    await answer_with_keyboard(m, u, text, kb_skill_card, "skill_card")
    return True


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
    """Map diagnosis output to profile categories only (no free text)."""
    bucket = (comp.get("bucket") or "mixed").strip()
    mapping = {
        "anxiety": {
            "main_pattern": "anxiety_avoidance",
            "avoidance_reason": "fear_of_bad_result",
            "emotional_trigger": "shame_or_anxiety",
        },
        "low_energy": {
            "main_pattern": "start_avoidance",
            "avoidance_reason": "low_energy",
            "emotional_trigger": "fatigue_or_overload",
        },
        "distractibility": {
            "main_pattern": "start_avoidance",
            "avoidance_reason": "unclear_first_step",
            "emotional_trigger": "distraction_or_restlessness",
        },
        "mixed": {
            "main_pattern": "start_avoidance",
            "avoidance_reason": "task_too_big",
            "emotional_trigger": "shame_or_anxiety",
        },
    }
    patch = dict(mapping.get(bucket, mapping["mixed"]))
    patch["recommended_track"] = "procrastination"
    return patch


async def show_day3_offer(m: Message, u: Dict[str, Any], source: str):
    """Show the adaptive day-3 map and paid continuation offer."""
    u["stage"] = "offer"
    u["last_offer_shown_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    await save_user(u, DB_PATH)

    profile = await get_user_profile(u["user_id"], DB_PATH)
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
    }
    await update_user_profile(u["user_id"], profile_patch, DB_PATH)

    offer_meta = {
        "source": source,
        "day": int(u.get("day") or 0),
        "price_month": "14.98",
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
    }
    await log_event(u["user_id"], "offer", "offer_shown", offer_meta, DB_PATH, SHEETS_WEBHOOK_URL)
    await log_event(u["user_id"], "offer", "profile_map_updated", {"source": source, **profile_patch}, DB_PATH, SHEETS_WEBHOOK_URL)
    await log_event(u["user_id"], "offer", "day3_conclusion_shown", offer_meta, DB_PATH, SHEETS_WEBHOOK_URL)
    await log_event(u["user_id"], "offer", "adaptive_offer_shown", offer_meta, DB_PATH, SHEETS_WEBHOOK_URL)

    await answer_with_keyboard(
        m,
        u,
        day3_primary_map_text(
            summary["start_pattern_text"],
            summary["avoidance_trigger"],
            summary["best_skills_text"],
            summary["downscale_pattern"],
            summary["preferred_activation"],
            summary["return_pattern"],
            summary.get("system_day_signals", ""),
        ),
        kb_pay_choice,
        "pay_choice",
    )


def should_show_day3_offer(u: Dict[str, Any], day: int) -> bool:
    """Day 3 offer is shown only for unpaid users outside free mode.

    Admin fast-forward (testmode/flag) allows testing offer path without waiting 3 days.
    """
    if day_core_test_mode_enabled(u):
        if is_paid(u) or int(u.get("free_mode") or 0) == 1:
            return False
        return True
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
    return is_admin(uid) and (int(u.get("is_test_user") or 0) == 1 or int(u.get("fast_forward_enabled") or 0) == 1)


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
    # System of Day is shown only after the training day is closed.
    # It is not a second skill, not progress, and not a streak action.
    allowed_sources = {"day_closed", "day_core_stop", "done_enough_today"}
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
    fresh = default_user(uid)
    fresh["chat_id"] = chat_id
    fresh["stage"] = "ask_name"
    await save_user(fresh, DB_PATH)
    return fresh


async def activate_test_cheat(m: Message, u: Dict[str, Any], source: str):
    u["is_test_user"] = 1
    u["fast_forward_enabled"] = 1
    u["free_mode"] = 0
    u["payment_status"] = "trial"
    u["trial_phase"] = "trial3"
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], u.get("stage", ""), "test_cheat_activated", {"source": source}, DB_PATH, SHEETS_WEBHOOK_URL)
    await m.answer(
        "Тестовый чит включён для этого пользователя.\n\n"
        "Календарные переходы, новый день и ручной offer всё равно доступны только ADMIN.\n\n"
        "Доступно:\n"
        "/debug_user\n"
        "/reset_me\n"
        "/testmode_off"
    )


async def handle_user_command(m: Message, u: Dict[str, Any], text: str) -> bool:
    """Handle simple user commands; does not require admin access."""
    if not text or not text.startswith("/"):
        return False
    uid = m.from_user.id
    command = text.split(maxsplit=1)[0].lower()

    if command == "/test_access":
        code = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) == 2 else ""
        if TEST_CHEAT_CODE and code == TEST_CHEAT_CODE:
            await activate_test_cheat(m, u, "command")
        else:
            await log_event(uid, u.get("stage", ""), "test_cheat_failed", {"source": "command"}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Код не подошёл.")
        return True

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
        await log_event(uid, u.get("stage", ""), "crisis_clicked", {"source": "command"}, DB_PATH, SHEETS_WEBHOOK_URL)
        await show_crisis_entry(m, u, "command")
        return True

    return False


async def handle_admin_command(m: Message, u: Dict[str, Any], text: str) -> bool:
    """Handle admin-only test commands. Returns True when command was consumed."""
    uid = m.from_user.id
    command = (text.split(maxsplit=1)[0] if text else "").lower()
    admin_commands = {
        "/testmode_on", "/testmode_off", "/set_day", "/force_next_day", "/show_offer",
        "/reset_me", "/debug_user", "/whoami", "/health", "/mark_paid", "/mark_free", "/sync_sheets", "/stats",
    }
    if command not in admin_commands:
        return False
    test_user_commands = {"/debug_user", "/reset_me", "/testmode_off", "/whoami", "/health"}
    if not is_admin(uid):
        if not (int(u.get("is_test_user") or 0) == 1 and command in test_user_commands):
            await m.answer("Команда недоступна.")
            return True

    if command == "/testmode_on":
        u["is_test_user"] = 1
        u["fast_forward_enabled"] = 1
        await save_user(u, DB_PATH)
        await log_event(uid, u.get("stage", ""), "testmode_on", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer("""Тестовый режим включён.
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
        u["stage"] = "waiting_next_day"
        await save_user(u, DB_PATH)
        await log_event(uid, "training", "admin_set_day", {"day": day}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(f"День установлен: {day}.\nМожно вызвать /show_offer.")
        return True

    if command == "/force_next_day":
        current_day = int(u.get("day") or u.get("current_day") or 1)
        next_day = min(current_day + 1, 28)
        u["day"] = next_day
        if "current_day" in u:
            u["current_day"] = next_day
        u["pending_skill_day"] = None
        clear_day_core_lock(u)
        u["stage"] = "waiting_next_day"
        await save_user(u, DB_PATH)
        await log_event(uid, "training", "admin_force_next_day", {"from_day": current_day, "day": next_day}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(f"Ок. Следующий день: {next_day}. Core skill можно назначить заново.")
        return True

    if command == "/show_offer":
        if not is_admin(uid):
            await m.answer("Команда недоступна.")
            return True
        await log_event(uid, u.get("stage", ""), "admin_show_offer", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await show_day3_offer(m, u, "manual_test")
        return True

    if command == "/reset_me":
        u["stage"] = "ask_name"
        u["day"] = 1
        if "current_day" in u:
            u["current_day"] = 1
        u["trainer_key"] = None
        u["bucket"] = None
        u["analysis_json"] = None
        u["profile_json"] = {}
        u["pending_skill_id"] = None
        u["today_target"] = None
        u["first_start_date"] = None
        clear_day_core_lock(u)
        u["payment_status"] = "free"
        u["free_mode"] = 0
        if int(u.get("is_test_user") or 0) != 1:
            u["fast_forward_enabled"] = 0
        await save_user(u, DB_PATH)
        await log_event(uid, "admin", "admin_reset_user", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer("Твой тестовый профиль сброшен. Напиши /start.")
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
            f"current_day: {u.get('current_day', u.get('day'))}\n"
            f"trainer_key: {u.get('trainer_key')}\n"
            f"bucket: {u.get('bucket')}\n"
            f"pending_skill_id: {u.get('pending_skill_id')}\n"
            f"today_target: {u.get('today_target')}\n"
            f"payment_status: {u.get('payment_status')}\n"
            f"trial_phase: {u.get('trial_phase')}\n"
            f"free_mode: {u.get('free_mode')}\n"
            f"is_test_user: {u.get('is_test_user')}\n"
            f"fast_forward_enabled: {u.get('fast_forward_enabled')}\n"
            f"paid_until: {u.get('paid_until')}\n"
            f"last_payment_click: {u.get('last_payment_click')}\n"
            f"last_offer_shown_at: {u.get('last_offer_shown_at')}\n"
            f"last_active: {u.get('last_active')}\n"
            f"profile_json_present: {str(bool(u.get('profile_json'))).lower()}\n"
            f"profile_json: {u.get('profile_json')}"
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
            f"Payments configured {str(bool(ENABLE_PAYMENTS or PAYMENT_URL_MONTH_1498 or PAYMENT_URL_FULL or PAYMENT_URL)).lower()}\n"
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
    if day > 3 and not is_paid(u) and not day_core_test_mode_enabled(u) and not TEST_MODE:
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
        "intro": "Ок. Это залипание, не лень. Работаем не запретом, а паузой и средой.",
    },
}


def failed_reason_skill(reason: str) -> tuple[str, Dict[str, Any]]:
    config = FAILED_REASON_SKILL_MAP.get(reason) or FAILED_REASON_SKILL_MAP["failed_too_hard"]
    for skill_id in config["skill_ids"]:
        if skill_id in SKILLS_DB:
            return skill_id, config
    return DOWNSCALE_PRIMARY_SKILL, config


def failed_reason_explanation(reason: str, u: Dict[str, Any]) -> str:
    target = u.get("today_target") or "текущая задача"
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

    profile = await get_user_profile(u["user_id"], DB_PATH)
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
        f"{format_skill_card(u, skill, u.get('today_target') or 'текущая задача')}"
    )
    await answer_with_keyboard(m, u, text, kb_downscale, "downscale")


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
    if TEST_CHEAT_CODE and text == TEST_CHEAT_CODE:
        await activate_test_cheat(m, u, "plain_code")
        return

    if u.get("first_start_date") or int(u.get("has_started_training") or 0) == 1 or u.get("day_core_skill_date"):
        sync_calendar_day(u)
        await save_user(u, DB_PATH)

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

    # Глобальный хук: кризис доступен из любого состояния, но не перебиваем активный кризис-флоу
    if (text == "🆘 Кризис" or "кризис" in low) and u.get("stage") not in {"crisis_stabilize", "crisis_choose_mode", "crisis_voice", "crisis_text", "crisis_plan_confirm", "crisis_tool_select", "crisis_effect_await"}:
        await show_crisis_entry(m, u, "global")
        return

    # "Ты меня не понял" is a rebuild flow, not a dead-end explanation.
    if is_misunderstood_button(text) and u.get("stage") not in {"misunderstood_reason", "misunderstood_problem_await", "misunderstood_explain_await"}:
        await open_misunderstood_flow(m, u, u.get("stage") or "unknown")
        return

    # Action-loop clarification/downscale: не запускаем повторную карту после старта тренировки
    if user_is_in_action_loop(u):
        if text == "Пропустить" or low == "пропустить":
            profile = await get_user_profile(u["user_id"], DB_PATH)
            sid = current_skill_id(u)
            skip_count = int(profile.get("action_skip_count") or 0) + 1
            await record_profile_signal(u["user_id"], "training", {
                "action_skip_count": skip_count,
                "last_skipped_skill": sid,
                "avoidance_pattern": "step_skipped",
                "avoidance_trigger": "шаг ощущается большим или не подходит",
            }, source="action_skipped")
            await log_event(u["user_id"], "training", "action_skipped", {"skill_id": sid}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["stage"] = "skip_options"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(
                m,
                u,
                "Ок.\n\n"
                "Это тоже информация.\n\n"
                "Похоже,\n"
                "сейчас даже этот шаг ощущается большим\n"
                "или не подходит.\n\n"
                "Записал.\n\n"
                "Что делаем дальше?",
                kb_skip_data,
                "skip_options",
            )
            return

        if u.get("stage") == "skip_options":
            if text == "🔁 Другой навык" or "другой" in low:
                await log_event(u["user_id"], "training", "skip_next_selected", {"choice": "other_skill"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await replace_skill_or_request_rediagnosis(m, u, "skip_other_skill")
                return
            if text == "😣 Сделать проще" or "проще" in low:
                await log_event(u["user_id"], "training", "skip_next_selected", {"choice": "downscale"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await send_downscale(m, u, "skip_make_simpler")
                return
            if text in {"🌙 На сегодня хватит", "🌙 Хватит на сегодня"} or "хватит" in low:
                current_day = sync_calendar_day(u)
                u["pending_skill_id"] = None
                u["pending_skill_day"] = None
                u["today_target"] = None
                u["stage"] = "waiting_next_day"
                await save_user(u, DB_PATH)
                await log_event(u["user_id"], "training", "day_training_closed_after_skip", {"day": current_day}, DB_PATH, SHEETS_WEBHOOK_URL)
                await answer_with_keyboard(m, u, day_training_closed_text(), kb_day_core_stop, "day_core_stop")
                await maybe_show_micro_habit(m, u, "day_closed")
                return
            await answer_with_keyboard(m, u, "Выбери, что делаем дальше:", kb_skip_data, "skip_options")
            return

        if text == "❌ Не сделал" or "не сделал" in low:
            screen = engine_handle_action_result(u, "failed")
            apply_engine_updates(u, screen)
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
                mark_day_core_round_done(u)
                gamify_apply(u, 2, "downscale_done")
                u["stage"] = "waiting_next_day"
                await save_user(u, DB_PATH)
                profile = await get_user_profile(u["user_id"], DB_PATH)
                sid = current_skill_id(u) or DOWNSCALE_PRIMARY_SKILL
                await record_profile_signal(u["user_id"], "training", {
                    "best_skill": sid,
                    "last_successful_skill": sid,
                    "preferred_activation": "small_visible_step",
                    "action_done_count": int(profile.get("action_done_count") or 0) + 1,
                }, source="downscale_done")
                if should_show_day3_offer(u, int(u.get("day") or 1)):
                    await show_day3_offer(m, u, "day3_auto")
                    return
                progress_profile = {**profile, "action_done_count": int(profile.get("action_done_count") or 0) + 1}
                await answer_with_keyboard(m, u, done_flow_text(random.random() < 0.25, u, progress_profile), kb_done, "done")
                return

        if u.get("stage") == "downscale_name_task":
            if text == "✅ Написал" or "написал" in low or (text and text != "🆘 Кризис"):
                await log_event(u["user_id"], "training", "downscale_done", {"stage": "downscale_name_task", "day": int(u.get("day") or 1)}, DB_PATH, SHEETS_WEBHOOK_URL)
                previous_done = int(u.get("done_count") or 0)
                u["done_count"] = previous_done + 1
                mark_day_core_round_done(u)
                gamify_apply(u, 2, "downscale_done")
                u["stage"] = "waiting_next_day"
                await save_user(u, DB_PATH)
                profile = await get_user_profile(u["user_id"], DB_PATH)
                sid = current_skill_id(u) or DOWNSCALE_PRIMARY_SKILL
                await record_profile_signal(u["user_id"], "training", {
                    "best_skill": sid,
                    "last_successful_skill": sid,
                    "preferred_activation": "small_visible_step",
                    "action_done_count": int(profile.get("action_done_count") or 0) + 1,
                }, source="downscale_done")
                if should_show_day3_offer(u, int(u.get("day") or 1)):
                    await show_day3_offer(m, u, "day3_auto")
                    return
                progress_profile = {**profile, "action_done_count": int(profile.get("action_done_count") or 0) + 1}
                await answer_with_keyboard(m, u, done_flow_text(False, u, progress_profile), kb_done, "done")
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
            await m.answer("Напиши 1–5 слов: что изменилось после шага?")
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
        await update_user_profile(u["user_id"], effect_patch, DB_PATH)
        await log_event(u["user_id"], "training", "after_action_note_saved", {"len": len(note), "effect_tags": effect_tags}, DB_PATH, SHEETS_WEBHOOK_URL)
        await answer_with_keyboard(m, u, after_action_note_saved_text(u.get("trainer_key") or "marsha"), kb_done, "done")
        return

    # Пост-выполнение: только два варианта, без перегруза кнопками
    if u.get("stage") == "waiting_next_day":
        trainer_key = u.get("trainer_key") or "marsha"
        if text == "🧭 Моя карта" or "моя карта" in low:
            profile = await get_user_profile(u["user_id"], DB_PATH)
            txt = render_short_user_map(profile, u.get("name"))
            await log_event(u["user_id"], "training", "profile_map_requested", {"source": "day_core_stop"}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, txt, kb_done, "done")
            return
        if text == "📚 Почему это работает" or "почему это работает" in low:
            await log_event(u["user_id"], "training", "day_lock_why_opened", {"day": int(u.get("day") or 1)}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, day_lock_why_text(), kb_day_core_stop, "day_core_stop")
            return
        if text == "🌙 До завтра" or "до завтра" in low:
            await log_event(u["user_id"], "training", "day_lock_until_tomorrow", {"day": int(u.get("day") or 1)}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, "До завтра. Новый основной навык откроется после смены календарного дня.", kb_day_core_stop, "day_core_stop")
            await maybe_show_micro_habit(m, u, "day_core_stop")
            return
        if text == "📌 Что изменилось?" or "что изменилось" in low:
            u["stage"] = "after_action_note"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "training", "after_action_note_requested", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Напиши 1-5 слов: что изменилось после шага?")
            return
        if text == "🔁 Ещё круг" or "еще круг" in low or "ещё круг" in low:
            profile = await get_user_profile(u["user_id"], DB_PATH)
            if int(u.get("day_core_round_count") or 0) >= 3 and not day_core_test_mode_enabled(u):
                await log_event(u["user_id"], "training", "daily_progression_stop_suggested", {"rounds": int(u.get("day_core_round_count") or 0)}, DB_PATH, SHEETS_WEBHOOK_URL)
                await answer_with_keyboard(m, u, today_progress_text(u, profile), kb_day_core_stop, "day_core_stop")
                return
            if should_show_today_progress(u, profile):
                await m.answer(today_progress_text(u, profile))
                await record_today_progress_shown(u, profile)
            if not day_core_test_mode_enabled(u) and has_stale_day_core_lock(u):
                previous_day = int(u.get("day") or 1)
                sync_calendar_day(u)
                await save_user(u, DB_PATH)
                await log_event(u["user_id"], "training", "day_core_date_rollover", {"from_day": previous_day, "to_day": u.get("day")}, DB_PATH, SHEETS_WEBHOOK_URL)
                await ask_today_action(m, u)
                return
            screen = engine_get_next_screen(u, {"type": "repeat_skill_card"})
            apply_engine_updates(u, screen)
            await save_user(u, DB_PATH)
            await log_engine_events(u, screen)
            markup = kb_day_core_stop if screen.get("buttons") == ["🧭 Моя карта", "📚 Почему это работает", "🌙 До завтра"] else kb_skill_card
            await answer_with_keyboard(m, u, screen["text"], markup, "day_core_stop" if markup is kb_day_core_stop else "skill_card")
            return
        if text in {"🌙 На сегодня хватит", "🌙 Хватит на сегодня"} or "хватит" in low:
            current_day = sync_calendar_day(u)
            u["pending_skill_id"] = None
            u["pending_skill_day"] = None
            u["today_target"] = None
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            await log_event(
                u["user_id"],
                "training",
                "day_training_closed",
                {"day": current_day, "day_core_skill_id": u.get("day_core_skill_id")},
                DB_PATH,
                SHEETS_WEBHOOK_URL,
            )
            await log_event(u["user_id"], "training", "done_enough_today", {"day": current_day}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, day_training_closed_text(), kb_day_core_stop, "day_core_stop")
            await maybe_show_micro_habit(m, u, "day_closed")
            return
        await answer_with_keyboard(m, u, "Выбери кнопкой 👇", kb_done, "done")
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
                f"{u['name']}, как удобнее собрать первую рабочую карту?",
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

    # confirm_analysis
    if u["stage"] == "confirm_analysis":
        low = text.lower()
        if "давай действие" in low or text == "💪 Давай действие":
            await log_event(u["user_id"], "analysis", "analysis_action_started", {}, DB_PATH, SHEETS_WEBHOOK_URL)
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
            await log_event(u["user_id"], "analysis", "analysis_action_started", {"source": "accepted"}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["stage"] = "waiting_next_day"
            u["day"] = 1
            ensure_first_start_date(u)
            await save_user(u, DB_PATH)
            await m.answer(
                "Ок.\n\n"
                "Пока это рабочая гипотеза.\n\n"
                "Дальше посмотрим,\n"
                "какие навыки реально помогут именно тебе."
            )
            await start_day(m, u, calendar_program_day(u), DB_PATH, SHEETS_WEBHOOK_URL)
            return
        if "немного" in low or "не так" in low or "не совсем" in low or text in {"🤔 Немного не так", "🤔 Не совсем"}:
            await open_misunderstood_flow(m, u, "confirm_analysis")
            return
        await answer_with_keyboard(m, u, "Выбери кнопку 👇", kb_analysis_confirm, "analysis")
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
            comp["useful_signal"] = "ленивость исключена из модели; смотрим на вход в действие"
            comp.update(safe_analysis_memory(safe_prompt, comp))
            u["analysis_json"] = json.dumps(comp, ensure_ascii=False)
            source = misunderstood_context(u).get("source") or "analysis"
            u["pending_plan_change"] = None
            u["stage"] = "confirm_analysis" if source == "confirm_analysis" else "waiting_next_day"
            await save_user(u, DB_PATH)
            patch = {"not_laziness_confirmed": True, "last_misunderstood_reason": reason}
            await update_user_profile(u["user_id"], patch, DB_PATH)
            await log_event(u["user_id"], "analysis", "analysis_rebuilt", {"reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
            await log_event(u["user_id"], "analysis", "profile_map_updated", {"source": "misunderstood", **patch}, DB_PATH, SHEETS_WEBHOOK_URL)
            markup = kb_analysis_confirm if u["stage"] == "confirm_analysis" else kb_training_main
            await answer_with_keyboard(m, u, "Да. Убираю лень из модели.\n\n" + format_comprehensive_analysis(comp), markup, "analysis_rebuilt")
            return
        if text.startswith("5") or "объяснить иначе" in low or "по-другому" in low or "по другому" in low:
            reason = "explain_differently"
            u["stage"] = "misunderstood_explain_await"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "analysis", "misunderstood_reason_selected", {"reason": reason}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Ок. Напиши иначе одним сообщением. Я пересоберу карту без полного онбординга.")
            return
        await answer_with_keyboard(m, u, misunderstood_prompt_text(), kb_misunderstood_reasons, "misunderstood_reasons")
        return

    if u.get("stage") == "misunderstood_problem_await":
        if not text:
            await m.answer("Напиши 1–2 предложения: какая проблема точнее?")
            return
        await rebuild_analysis_lightweight(m, u, f"Не та проблема. Точнее: {text}", "wrong_problem")
        return

    if u.get("stage") == "misunderstood_explain_await":
        if not text:
            await m.answer("Напиши одним сообщением, как объяснить точнее.")
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
        combined_text = clamp_str(f"{previous_text}\n\nЧаще ломает вход: {text}", 1500)
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
            await m.answer("Напиши, пожалуйста, что не совпадает с реальностью. (1–3 предложения)")
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
            await m.answer("Напиши 1–2 предложения, чтобы я пересобрал вывод.")
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
        await m.answer(
            f"Чтобы пройти тест, выбери вариант кнопкой ниже 👇\n\n❓ Вопрос {next_q_num}/5:\n\n{next_q['text']}",
            reply_markup=create_test_question_keyboard(next_q_num),
        )
        return

    # Вопрос перед выдачей навыка
    if u.get("stage") == "await_training_target":
        screen = engine_get_next_screen(u, {"type": "target_submitted", "text": text})
        apply_engine_updates(u, screen)
        await save_user(u, DB_PATH)
        await log_engine_events(u, screen)
        await answer_with_keyboard(m, u, screen["text"], kb_skill_card, "skill_card")
        await maybe_show_micro_habit(m, u, "day_start")
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
                "'✅ Сделал' или '❌ Не сделал'."
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

        if text == "🧭 Моя карта" or "моя карта" in low:
            profile = await get_user_profile(u["user_id"], DB_PATH)
            txt = render_short_user_map(profile, u.get("name"))
            await log_event(u["user_id"], "training", "profile_map_requested", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, txt, ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="💪 Давай действие")],[KeyboardButton(text="📚 Что это значит")],[KeyboardButton(text="⬅️ Назад")]], resize_keyboard=True), "profile_map")
            return

        if text == "📚 Что это значит" or "что это значит" in low:
            await m.answer("Это не диагноз, а рабочая гипотеза по твоим действиям. Сейчас активен только модуль прокрастинации и запуска; остальные направления — будущие.")
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
            await show_crisis_entry(m, u, "training_main")
            return

        if text in {"📊 Мой прогресс", "📊 Прогресс"} or "мой прогресс" in low or "прогресс" in low:
            await send_progress_report(m, u, DB_PATH)
            return

        if text == "✅ Сделал(а)" or ("сделал" in low and "не сделал" not in low):
            screen = engine_handle_action_result(u, "done")
            previous_done = int(u.get("done_count") or 0)
            u["done_count"] = previous_done + 1
            mark_day_core_round_done(u)
            gamify_apply(u, 2, "done")
            apply_engine_updates(u, screen)
            await save_user(u, DB_PATH)
            profile = await get_user_profile(u["user_id"], DB_PATH)
            sid = current_skill_id(u)
            done_count = int(profile.get("action_done_count") or 0) + 1
            preferred_activation = "body_doubling" if sid == "body_doubling_plan" else ("phone_away" if sid == "phone_far_3min" else "small_visible_step")
            await record_profile_signal(u["user_id"], "training", {
                "best_skill": sid,
                "last_successful_skill": sid,
                "preferred_activation": preferred_activation,
                "action_done_count": done_count,
            }, source="action_done")
            await log_engine_events(u, screen)
            if should_show_day3_offer(u, day):
                await show_day3_offer(m, u, "day3_auto")
                return
            progress_profile = {**profile, "action_done_count": done_count}
            await answer_with_keyboard(m, u, done_flow_text(random.random() < 0.25, u, progress_profile), kb_done, "done")
            return

        if text == "↩️ Вернулся(лась)" or "вернулся" in low:
            screen = engine_handle_action_result(u, "return")
            u["return_count"] = int(u.get("return_count") or 0) + 1
            mark_day_core_round_done(u)
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
            await replace_skill_or_request_rediagnosis(m, u, text or "button")
            return

        await answer_with_keyboard(m, u, "Выбери действие:", kb_training_main, "training_main")
        return

    # crisis stabilization: calm -> skill -> choice
    if u.get("stage") == "crisis_stabilize":
        low = (text or "").lower().strip()
        if text == "✅ Сделал" or low == "сделал":
            await log_event(u["user_id"], "crisis_stabilize", "crisis_stabilize_done", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await show_crisis_tool_prompt(m, u)
            return
        if text == "↩️ Вернуться в тренировку" or "вернуться" in low:
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "crisis_stabilize", "crisis_return_training", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, "Ок. Только маленький шаг. Без героизма.", kb_training_main, "training_main")
            return
        if text == "🆘 Мне всё ещё плохо" or "всё ещё плохо" in low or "все еще плохо" in low:
            await log_event(u["user_id"], "crisis_stabilize", "crisis_still_bad", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, crisis_still_bad_text(), kb_crisis_stabilize, "crisis_stabilize")
            return
        if text == "✍️ Написать, что происходит" or "написать" in low:
            u["stage"] = "crisis_text"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "crisis_stabilize", "crisis_write_opened", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Напиши 1–3 предложения: что происходит прямо сейчас?")
            return
        await answer_with_keyboard(m, u, crisis_stabilize_text(), kb_crisis_stabilize, "crisis_stabilize")
        return

    # crisis tool selection: second layer after stabilization
    if u.get("stage") == "crisis_tool_select":
        if m.voice:
            t = await whisper_transcribe(m)
            if not t:
                await m.answer("Не смог разобрать голос. Выбери пункт или напиши текстом.")
                return
            await send_crisis_tool(m, u, t)
            return
        if not text:
            await m.answer(crisis_tool_prompt_text(), reply_markup=kb_crisis_tool_select)
            return
        await send_crisis_tool(m, u, text)
        return

    # crisis_choose_mode
    if u.get("stage") == "crisis_choose_mode":
        low = (text or "").lower().strip()

        # Если сразу прислал голосовое — обрабатываем без лишних шагов
        if m.voice:
            t = await whisper_transcribe(m)
            if t:
                await send_crisis_stabilize(m, u, "voice_entry")
                return
            await m.answer("Не смог разобрать голос. Выбери голосом/текстом кнопкой.")
            return

        if text == "⬅️ Назад" or "назад" in low:
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Ок. Возвращаемся в тренировку.", kb_training_main, "training_main")
            return
        if text in {"🎙 Голосом", "🎙 Кризис голосом"} or "голос" in low:
            u["crisis_input_mode"] = "voice"
            await save_user(u, DB_PATH)
            await send_crisis_stabilize(m, u, "voice_entry")
            return
        if text in {"✍️ Текстом", "✍️ Кризис текстом"} or "текст" in low:
            u["crisis_input_mode"] = "text"
            await save_user(u, DB_PATH)
            await send_crisis_stabilize(m, u, "text_entry")
            return
        if text:
            u["crisis_input_mode"] = "text"
            await save_user(u, DB_PATH)
            await send_crisis_stabilize(m, u, "text_entry")
            return
        await answer_with_keyboard(m, u, crisis_entry_text(), kb_crisis_mode, "crisis_mode")
        return

    if u.get("stage") == "crisis_text":
        if text and text.lower().strip() in {"⬅️ назад", "назад"}:
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Ок. Возвращаемся в тренировку.", kb_training_main, "training_main")
            return
        if not text:
            await m.answer("Напиши 1–3 предложения.")
            return
        await send_crisis_tool(m, u, text)
        return

    if u.get("stage") == "crisis_voice":
        if text and text.lower().strip() in {"⬅️ назад", "назад"}:
            u["stage"] = "waiting_next_day"
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
        await send_crisis_tool(m, u, t)
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
        u["stage"] = "waiting_next_day"
        u["pending_crisis_pattern"] = None
        u["pending_crisis_skill"] = None
        await save_user(u, DB_PATH)
        await answer_with_keyboard(m, u, "Записал. Это данные для карты. Возвращаемся к основному навыку дня.", kb_training_main, "training_main")
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
        if text == "💳 Продолжить за €14.98" or text == "💳 Месяц — €14.98" or "месяц" in low or "€14.98" in low or "14.98" == low:
            await log_event(u["user_id"], "offer", "payment_click_month_1498", {"payment_click": "month_1498", "amount": 14.98}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["payment_status"] = "pending_month_1498"
            u["last_payment_click"] = "month_14_98"
            await save_user(u, DB_PATH)
            pay_url = PAYMENT_URL_MONTH_1498 or PAYMENT_URL_FULL or PAYMENT_URL
            payment_intro = (
                "Продолжаем строить персональную систему.\n\n"
                "Сейчас у нас уже есть первые сигналы.\n"
                "Но устойчивые паттерны появляются только через повторения.\n\n"
                "Следующий этап —\n"
                "не просто упражнения,\n"
                "а сбор устойчивой модели:\n"
                "что помогает именно тебе,\n"
                "где ломается внимание,\n"
                "и как выстроить систему,\n"
                "в которую мозгу легче возвращаться.\n\n"
                "Цена: €14.98 за 30 дней."
            )
            if pay_url:
                await m.answer(f"{payment_intro}\n\nНажми кнопку ниже для оплаты.")
                await m.answer(" ", reply_markup=payment_inline_month_1498(pay_url))
            else:
                await log_event(u["user_id"], "offer", "payment_error", {"error_type": "payment_url_missing", "payment_click": "month_1498", "amount": 14.98}, DB_PATH, SHEETS_WEBHOOK_URL)
                await log_event(u["user_id"], "offer", "payment_stub_shown", {"price_month": "14.98"}, DB_PATH, SHEETS_WEBHOOK_URL)
                await m.answer(payment_month_1498_stub_text())
            return
        if text == "🤔 Подумаю" or "подумаю" in low:
            await log_event(u["user_id"], "offer", "payment_declined_soft", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await log_event(u["user_id"], "offer", "free_mode_started", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["free_mode"] = 1
            u["payment_status"] = "free_mode"
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            await m.answer(payment_declined_soft_text())
            await answer_with_keyboard(m, u, "Выбери действие:", kb_training_main, "training_main")
            return
        if text == "📚 Подробнее о карте" or text == "📚 Что будет дальше" or "подробнее" in low or "что будет дальше" in low:
            await log_event(u["user_id"], "offer", "profile_map_details_opened", {"price_month": "14.98"}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer(profile_map_details_text())
            await answer_with_keyboard(m, u, "Можно продолжить или посмотреть сигналы.", kb_pay_choice, "pay_choice")
            return
        if text == "🧭 Показать мои сигналы" or text == "🧭 Показать карту ещё раз" or text == "🧭 Показать мою карту" or "показать" in low and ("сигнал" in low or "карт" in low):
            profile = await get_user_profile(u["user_id"], DB_PATH)
            summary = build_profile_map_summary(u, profile)
            await log_event(u["user_id"], "offer", "profile_signals_opened", {"source": "offer"}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer(profile_signals_text(
                summary["return_count"],
                summary["downscale_count"],
                summary["done_count"],
                summary["avoidance_trigger"],
                summary["best_skills_text"],
                summary["preferred_activation"],
                summary.get("effect_notes", ""),
                summary.get("failed_reason_count", 0),
                summary.get("attention_escape_count", 0),
                summary.get("shame_signal", ""),
                summary.get("energy_signal", ""),
                summary.get("system_day_signals", ""),
            ))
            await answer_with_keyboard(m, u, "Что дальше?", kb_pay_choice, "pay_choice")
            return
        if text == "⬅️ Назад" or "назад" in low:
            profile = await get_user_profile(u["user_id"], DB_PATH)
            summary = build_profile_map_summary(u, profile)
            await answer_with_keyboard(
                m,
                u,
                day3_primary_map_text(
                    summary["start_pattern_text"],
                    summary["avoidance_trigger"],
                    summary["best_skills_text"],
                    summary["downscale_pattern"],
                    summary["preferred_activation"],
                    summary["return_pattern"],
                    summary.get("system_day_signals", ""),
                ),
                kb_pay_choice,
                "pay_choice",
            )
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
            u["stage"] = "waiting_next_day"
            u["day"] = 1
            ensure_first_start_date(u)
            await save_user(u, DB_PATH)
            # Показываем первый календарный навык сразу после онбординга (через callback)
            await start_day(m=c.message, u=u, day=calendar_program_day(u), db_path=DB_PATH, sheets_webhook=SHEETS_WEBHOOK_URL)
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
    user_text = stored_analysis_user_text(u) or f"У меня проблемы с {bucket}"
    comp = await ai_analyze_comprehensive(user_text, u.get("trainer_key", "marsha"), client, OPENAI_CHAT_MODEL)
    comp = normalize_analysis(comp, user_text)
    comp["trainer_key"] = u.get("trainer_key", "marsha")
    if comp.get("analysis_fallback"):
        await log_event(u["user_id"], "analysis", "openai_error", {"error_type": "analysis_fallback", "error_source": "show_comprehensive_analysis"}, DB_PATH, SHEETS_WEBHOOK_URL)
    comp.pop("user_text", None)
    comp.update(safe_analysis_memory(user_text, comp))
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
    diagnosis_profile_patch = {**profile_patch_from_diagnosis(comp), **live_analysis_profile_patch(str(comp.get("live_pattern") or ""))}
    await update_user_profile(u["user_id"], diagnosis_profile_patch, DB_PATH)
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
    msg = f"{format_comprehensive_analysis(comp_for_message, trainer_key=u.get('trainer_key', 'marsha'))}\n\nЭто похоже на тебя?"
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
