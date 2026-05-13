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
from texts import (
    TRAINERS, PRAISE, DAILY_LIVE_LINES, TEST_QUESTIONS, ONBOARDING_SCREENS,
    trainer_say, trainer_confirm_text, kb_trainers, kb_input_mode, kb_yes_no,
    kb_training_main, kb_more_actions, kb_crisis_mode, kb_analysis_confirm, kb_pay_choice,
    kb_skill_card, kb_done, kb_failed, kb_action_clarify, kb_downscale,
    kb_downscale_name_task, kb_microstep, kb_skeptic, kb_doubt_response,
    kb_more_clarify, payment_inline_discount, payment_inline_full,
    CRISIS_LIMIT, resolve_bucket_from_test, create_test_question_keyboard,
    analysis_contract_short, month_map_text, guarantee_block, offer_day_3_text,
    gamify_status_line, format_skill_card, trainer_done_response,
    trainer_failed_response, skill_detail_text, simple_explain_text, skeptic_text,
    inactivity_ping, keyboard_button_count
)
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
    send_trainer_photo_if_any, send_trainer_introduction, run_analysis,
    send_weekly_summary, send_progress_report, ai_analyze, ai_analyze_comprehensive,
    _extract_json, clamp_str
)
from nlp_fallback import is_misunderstood, is_too_hard, is_timer_too_hard

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
    if button_count > 5:
        log.warning("Keyboard %s has %s buttons; sending text without markup", keyboard_name, button_count)
        await m.answer(text)
        return
    await m.answer(text, reply_markup=reply_markup)


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


async def send_downscale(m: Message, u: Dict[str, Any], reason: str):
    """Показать уменьшенный action-step внутри текущего тренировочного loop."""
    skill_id = _select_downscale_skill(u)
    u["stage"] = "downscale_action"
    await save_user(u, DB_PATH)
    await log_event(
        u["user_id"],
        "training",
        "downscale_triggered",
        {"reason": reason, "pattern": DOWNSCALE_PATTERN, "skill": skill_id, "day": int(u.get("day") or 1)},
        DB_PATH,
        SHEETS_WEBHOOK_URL,
    )
    await answer_with_keyboard(
        m,
        u,
        "Понял.\n\n"
        "Тогда таймер — уже слишком большой шаг.\n\n"
        "Не ставим таймер.\n\n"
        "Сделай только это:\n"
        "1. Открой место, где лежит задача.\n"
        "2. Не работай.\n"
        "3. Назови следующий физический шаг.\n\n"
        "Минимум:\n"
        "одно слово.\n\n"
        "Это и есть тренировка входа.",
        kb_downscale,
        "downscale",
    )

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
            await log_event(u["user_id"], "training", "not_done", {"day": int(u.get("day") or 1)}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["stage"] = "failed_options"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, trainer_failed_response(u.get("trainer_key") or "marsha"), kb_failed, "failed")
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
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "training", "done_more_round", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            plan = get_current_plan(u)
            day = int(u.get("day") or 1)
            sid = plan[max(0, min(len(plan) - 1, day - 1))] if plan else next(iter(SKILLS_DB.keys()))
            skill = SKILLS_DB.get(sid) or list(SKILLS_DB.values())[0]
            target = u.get("today_target") or "Прокрастинация в целом"
            await answer_with_keyboard(m, u, format_skill_card(u, skill, target), kb_skill_card, "skill_card")
            return
        if text == "🌙 На сегодня хватит" or "хватит" in low:
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], "training", "done_enough_today", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await answer_with_keyboard(m, u, "Ок. На сегодня фиксируем подход.", kb_training_main, "training_main")
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
        from texts import send_trainer_introduction
        await send_trainer_introduction(m, u)
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
        target = clamp_str(text or "", 200)
        if not target or target.lower() == "пропустить":
            target = "Прокрастинация в целом"

        day = int(u.get("pending_skill_day") or u.get("day") or 1)
        plan = get_current_plan(u)
        sid = u.get("pending_skill_id")
        if not sid or sid not in SKILLS_DB:
            if plan:
                idx = max(0, min(len(plan) - 1, day - 1))
                sid = plan[idx]
            else:
                sid = next(iter(SKILLS_DB.keys()))

        trainer_key = u.get("trainer_key") or "marsha"
        skill = SKILLS_DB.get(sid) or list(SKILLS_DB.values())[0]
        msg = format_skill_card(u, skill, target)

        u["today_target"] = target
        u["pending_skill_id"] = None
        u["pending_skill_day"] = None
        u["stage"] = "training"
        await save_user(u, DB_PATH)
        await log_event(u["user_id"], "training", "target_set", {"day": day, "text": target}, DB_PATH, SHEETS_WEBHOOK_URL)
        button_count = sum(len(row) for row in kb_skill_card.keyboard)
        await log_event(
            u["user_id"],
            "training",
            "skill_card_shown",
            {"skill_id": sid, "trainer_key": trainer_key, "button_count": button_count},
            DB_PATH,
            SHEETS_WEBHOOK_URL,
        )

        await answer_with_keyboard(m, u, msg, kb_skill_card, "skill_card")
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
            await log_event(u["user_id"], "training_done", "done", {"day": day})
            previous_done = int(u.get("done_count") or 0)
            u["done_count"] = previous_done + 1
            gamify_apply(u, 2, "done")
            trainer = u.get("trainer_key") or "marsha"
            await m.answer(trainer_done_response(trainer))
            if trainer == "skinny":
                await m.answer("Что почувствовал во время выполнения?")
            elif trainer == "beck":
                await m.answer("Что заметил во время выполнения?")
            else:
                await m.answer("Как тебе было это делать?")
            # post_done_reflection этап убран, сразу переходим к следующему этапу
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            if previous_done == 0:
                await show_route(m, u, "first_done")
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
            if not TEST_MODE and day == 3 and u.get("trial_phase") == "trial3":
                await show_route(m, u, "day3_summary")
                await answer_with_keyboard(m, u, "Ты уже видел(а):\nэто не мотивация.\nЭто тренировка.\n\n💳 Сейчас — цена со скидкой.", kb_pay_choice, "pay_choice")
                u["stage"] = "offer"
                await save_user(u, DB_PATH)
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
        if TEST_MODE:
            u["stage"] = "training"
            u["trial_phase"] = "paid"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Тестовый режим: продолжаем без оплаты.", kb_training_main, "training_main")
            return
        low = text.lower().strip()
        if text == "7 дней — €20" or "7 дней" in low or "€20" in low or "20" == low:
            await log_event(u["user_id"], "offer", "payment_click_20", {"payment_click": "20"}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["payment_status"] = "pending_20"
            u["last_payment_click"] = "20"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Выбери действие:", kb_training_main, "training_main")
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
        if "ещ" in low:
            u["trial_days"] = 7
            u["trial_phase"] = "trial7"
            await save_user(u, DB_PATH)
            await answer_with_keyboard(m, u, "Ок. Ещё 4 дня в пробе. Продолжаем.", kb_training_main, "training_main")
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
    msg = f"{comp.get('short_summary', 'Похоже на тебя?')}\n\nЭто похоже на тебя?"
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

async def main():
    try:
        if not BOT_TOKEN:
            raise RuntimeError("BOT_TOKEN is empty")
        bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
        dp = Dispatcher()
        dp.include_router(router)
        await init_db(DB_PATH)
        await migrate_db(DB_PATH)
        asyncio.create_task(background_checkins(bot))
        asyncio.create_task(sheets_sync_loop(DB_PATH))
        log.info("Bot started")
        await dp.start_polling(bot)
    except asyncio.exceptions.CancelledError:
        log.info("Polling cancelled, shutting down...")
    except KeyboardInterrupt:
        log.info("Bot stopped by user (KeyboardInterrupt)")
    except Exception as e:
        log.exception(f"Unexpected error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
