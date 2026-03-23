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
import re
import json
import time
import asyncio
import logging
import threading
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
    kb_training_main, kb_crisis_mode, kb_analysis_confirm, kb_pay_choice,
    kb_doubt_response, kb_more_clarify, payment_inline_discount, payment_inline_full,
    CRISIS_LIMIT, resolve_bucket_from_test, create_test_question_keyboard,
    analysis_contract_short, month_map_text, guarantee_block, offer_day_3_text,
    gamify_status_line, skill_explain, skill_detail_text, inactivity_ping
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
    log_event, gamify_apply, is_paid, should_ping, EXTRA_USER_COLS
)
from flows import (
    start_day, start_day1, start_day_simple, advance_day, handle_crisis,
    send_trainer_photo_if_any, send_trainer_introduction, run_analysis,
    send_weekly_summary, send_progress_report, ai_analyze, ai_analyze_comprehensive,
    _extract_json, clamp_str
)

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

# Unlock full flow while testing (set TEST_MODE=1)
TEST_MODE = os.getenv("TEST_MODE", "").lower() in {"1", "true", "yes", "on", "debug"}

AI_ANALYSIS_ENABLED = bool(OPENAI_API_KEY)

# Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

print(f"BOT_TOKEN: {repr(BOT_TOKEN)}")
print(f"DB_PATH: {DB_PATH}")

# OpenAI client
openai = None
client = None
if AI_ANALYSIS_ENABLED:
    try:
        import openai as oa
        openai = oa
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        print(f"⚠️  OpenAI import failed: {e}")
        client = None
        AI_ANALYSIS_ENABLED = False
        openai = None
    if openai is None:
        print("⚠️  OpenAI import unavailable, continuing without AI features")


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

@router.message(CommandStart())
async def cmd_start(m: Message):
    uid = m.from_user.id
    u = await get_user(uid, DB_PATH)
    u["chat_id"] = m.chat.id


    # Новый порядок онбординга:
    # 1. Экраны онбординга
    u["stage"] = "ask_name"
    await save_user(u, DB_PATH)
    for screen in ONBOARDING_SCREENS:
        await m.answer(screen)
        await asyncio.sleep(0.3)

    # 2. Вопрос имени
    await m.answer(
        "Привет! Я тренер навыков саморегуляции. Как тебя зовут? (1 слово)",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True),
    )

@router.message()
async def main_flow(m: Message):
    uid = m.from_user.id
    u = await get_user(uid, DB_PATH)
    text = (m.text or "").strip()
    low = text.lower()

    # Глобальный хук: кризис доступен из любого состояния, но не перебиваем активный кризис-флоу
    if (text == "🆘 Кризис" or "кризис" in low) and u.get("stage") not in {"crisis_choose_mode", "crisis_voice", "crisis_text", "crisis_plan_confirm"}:
        u["stage"] = "crisis_choose_mode"
        await save_user(u, DB_PATH)
        await log_event(u["user_id"], u["stage"], "crisis_open", {}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer("🆘 Ок. Как удобнее?", reply_markup=kb_crisis_mode)
        return

    # Пост-рефлексия после выполнения
    if u.get("stage") == "waiting_next_day":
        trainer_key = u.get("trainer_key") or "marsha"
        reply = await ai_micro_reflect(text or "", trainer_key, client, OPENAI_CHAT_MODEL)
        await log_event(u["user_id"], "training", "post_done_reflect", {"len": len(text or "")}, DB_PATH, SHEETS_WEBHOOK_URL)
        await m.answer(trainer_say(trainer_key, reply), reply_markup=kb_training_main)
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
        u["stage"] = "trainer_intro"
        await save_user(u, DB_PATH)
        # Описание и фото тренера
        await send_trainer_photo_if_any(m.chat.id, chosen, BOT_TOKEN)
        from texts import send_trainer_introduction
        await send_trainer_introduction(m, u)
        await m.answer("Готов начать разбор и перейти к первому дню?", reply_markup=kb_yes_no)
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

    # После карты навыков — запросить подтверждение и только потом стартовать День 1
    if u.get("stage") == "diagnosis_done":
        await m.answer(month_map_text(u.get("bucket")))
        accept_kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📜 Принимаю план")], [KeyboardButton(text="❌ Нет")]],
            resize_keyboard=True
        )
        u["stage"] = "analysis_map"
        await save_user(u, DB_PATH)
        await m.answer("Принять этот план и начать День 1?", reply_markup=accept_kb)
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
        if not text or text.lower() == "пропустить":
            user_text = "Прокрастинация/избегание,хочу начать, но откладываю."
        else:
            user_text = text
        u["analysis_json"] = json.dumps({"user_text": clamp_str(user_text, 1000)}, ensure_ascii=False)
        u["stage"] = "run_analysis"
        await save_user(u, DB_PATH)
        await m.answer("Ок. Быстрый разбор…")
        await run_analysis(m, u, user_text, DB_PATH, SHEETS_WEBHOOK_URL, client, OPENAI_CHAT_MODEL)
        # После анализа — явно завершаем стадию
        u["stage"] = "diagnosis_done"
        await save_user(u, DB_PATH)
        # Подробный разбор после кейса
        patterns = [
            {
                "name": "Прокрастинация",
                "desc": "Откладывание важных задач",
                "manifest": "Задачи не стартуют вовремя, появляется чувство вины"
            },
            {
                "name": "Тревожный цикл",
                "desc": "Избегание из-за страха ошибки",
                "manifest": "Есть ощущение, что не получится, поэтому не начинаешь"
            },
            {
                "name": "Отвлечения",
                "desc": "Частые переключения внимания",
                "manifest": "Внимание уходит на телефон, соцсети, мелкие дела"
            }
        ]
        missing_skills = [
            "Навык запуска (старт задачи)",
            "Навык удержания внимания",
            "Навык управления тревогой"
        ]
        detailed_text = "🔎 Подробный разбор:\n\n"
        detailed_text += "Паттерны поведения и их проявления:\n"
        for p in patterns:
            detailed_text += f"• {p['name']} — {p['desc']}\n  Как проявляется: {p['manifest']}\n"
        detailed_text += "\nКаких навыков не хватает:\n"
        detailed_text += "\n".join([f"• {s}" for s in missing_skills])
        await m.answer(detailed_text)
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
        t = await whisper_transcribe(m)
        if not t:
            u["stage"] = "await_problem_text"
            await save_user(u, DB_PATH)
            await m.answer("Не смог разобрать. Напиши текстом 1–3 предложения.")
            return
        u["analysis_json"] = json.dumps({"user_text": clamp_str(t, 1000)}, ensure_ascii=False)
        u["stage"] = "run_analysis"
        await save_user(u, DB_PATH)
        await m.answer("Ок. Быстрый разбор…")
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
            await m.answer(month_map_text(u.get("bucket")))
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
        if "в точку" in low or (text == "✅ Да, в точку"):
            await log_event(u["user_id"], "analysis", "analysis_accepted", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["stage"] = "analysis_contract"
            await save_user(u, DB_PATH)
            await m.answer(analysis_contract_short(u.get("name") or "друг", u.get("trainer_key"), u.get("bucket")),
                            reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Подробнее о контракте")], [KeyboardButton(text="📜 Принимаю контракт на 4 недели")]], resize_keyboard=True))
            return
        if "немного" in low or "не так" in low or (text == "🤔 Немного не так"):
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
        await m.answer("Выбери кнопку 👇", reply_markup=kb_analysis_confirm)
        return

    # Подробнее о контракте
    if u.get("stage") == "analysis_contract" and (text == "Подробнее о контракте" or "подробнее о контракте" in text.lower()):
        from texts import contract_full_text
        await m.answer(contract_full_text(u.get("name") or "друг", u.get("trainer_key"), u.get("bucket")), reply_markup=kb_yes_no)
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
        how_text = skill_explain(trainer_key, skill)
        minimum = skill.get("minimum") or skill.get("micro") or ""
        msg = (
            f"📌 Дело на сегодня: {target}\n\n"
            f"🧩 Навык дня: {skill['name']}\n"
            f"🎯 Цель: {skill['goal']}\n"
            f"✅ Как: {how_text}"
        )
        if minimum:
            msg += f"\nМинимум: {minimum}"

        u["today_target"] = target
        u["pending_skill_id"] = None
        u["pending_skill_day"] = None
        u["stage"] = "training"
        await save_user(u, DB_PATH)
        await log_event(u["user_id"], "training", "target_set", {"day": day, "text": target}, DB_PATH, SHEETS_WEBHOOK_URL)

        await m.answer(trainer_say(trainer_key, msg), reply_markup=kb_training_main)
        await m.answer(gamify_status_line(u))
        return

    # TRAINING stage
    if u.get("stage") == "training":
        low = text.lower().strip()
        day = int(u.get("day") or 1)

        if text == "💪 Давай тренировать навык" or ("давай" in low and "трен" in low):
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
            await m.answer(trainer_say(trainer_key, f"{detail}\n\n{prompt}"), reply_markup=kb_training_main)
            return

        if text == "ℹ️ Подробнее про навык" or "подробнее" in low:
            plan = get_current_plan(u)
            idx = max(0, min(len(plan) - 1, int(u.get("day") or 1) - 1))
            sid = plan[idx]
            skill = SKILLS_DB.get(sid, {})
            msg = skill_detail_text(skill)
            await m.answer(msg, reply_markup=kb_more_clarify)
            return

        if text == "👍 Понял(а), продолжаем" or text == "📚 Подробнее почему это работает" or "подробнее почему" in low:
            trainer_key = u.get("trainer_key") or "marsha"
            if text == "👍 Понял(а), продолжаем" or ("понял" in low and "подробнее" not in low):
                await log_event(u["user_id"], "training", "doubt_understood", {"trainer": trainer_key}, DB_PATH, SHEETS_WEBHOOK_URL)
                await m.answer(trainer_say(trainer_key, PRAISE.get(trainer_key, "Идём дальше!")), reply_markup=kb_training_main)
                return
            else:
                await log_event(u["user_id"], "training", "doubt_details_requested", {"trainer": trainer_key}, DB_PATH, SHEETS_WEBHOOK_URL)
                if trainer_key == "skinny":
                    details_text = "📊 Почему микро-тренули работают:\n\n• 60 сек — это минимум для активации нейро-связей\n• Повторяемость важнее объёма\n• 3 дня подряд = установка нового паттерна\n• Эффект накапливается. Видно на день 3-4.\n\nСделал → умеешь. Так работает мозг."
                elif trainer_key == "beck":
                    details_text = "🧬 Нейро-механика повторения:\n\n• Синапс усиливается при каждом выполнении (Hebb's Law)\n• Миелинизация идёт на 3-7 день регулярности\n• Метрика done/return показывает адаптацию мозга\n• Долгосрочная потенциация = стабильный навык\n\nГрафики покажут, когда функция встроилась."
                else:
                    details_text = "🌱 Как работает безопасный рост:\n\n• Микро-шаги = нет перегрузки и стыда\n• Повтор = уверенность, не сомнения\n• Каждый успех закраски невидимым рост\n• Если день не пошёл — просто возвращаемся завтра\n\nЭффект видно не на неделе, а на двух."
                await m.answer(trainer_say(trainer_key, details_text), reply_markup=kb_training_main)
                return

        if text == "Ты меня не понял" or "не понял" in low:
            u["analysis_retry_count"] = int(u.get("analysis_retry_count") or 0) + 1
            retry_count = u["analysis_retry_count"]
            if retry_count > 2:
                u["stage"] = "training"
                await save_user(u, DB_PATH)
                await log_event(u["user_id"], "analysis", "retry_limit_reached", {}, DB_PATH, SHEETS_WEBHOOK_URL)
                await m.answer(trainer_say(u["trainer_key"], "Я уже трижды пытался понять. 😊\n\nДавай начнём тренировку и посмотрим, как это будет работать в жизни.\n\nМожет быть, это станет яснее когда ты начнёшь."), reply_markup=kb_training_main)
                await start_day(m=m, u=u, day=1, db_path=DB_PATH, sheets_webhook=SHEETS_WEBHOOK_URL)
                return
            await save_user(u, DB_PATH)
            u["stage"] = "analysis_retry_await_clarification"
            await save_user(u, DB_PATH)
            await m.answer(trainer_say(u["trainer_key"], f"Ок. Уточни ещё раз (попытка {retry_count}/2):\n\nЧто конкретно здесь не правда? Расскажи подробнее."))
            return

        if text == "🆘 Кризис" or "кризис" in low:
            u["stage"] = "crisis_choose_mode"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], u["stage"], "crisis_open", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("🆘 Ок. Как удобнее?", reply_markup=kb_crisis_mode)
            return

        if text == "📊 Мой прогресс" or "мой прогресс" in low or "прогресс" in low:
            await send_progress_report(m, u, DB_PATH)
            return

        if text == "✅ Сделал(а)" or "сделал" in low:
            await log_event(u["user_id"], "training_done", "done", {"day": day})
            gamify_apply(u, 2, "done")
            trainer = u.get("trainer_key")
            # 1️⃣ Базовая реакция
            if trainer == "skinny":
                await m.answer("🐈‍⬛ Сделал. Факт есть. Это тренировка.")
                await m.answer("Что ты почувствовал во время выполнения?")
            elif trainer == "marsha":
                await m.answer("🐶 Я рада, что ты попробовал. Это шаг.")
                await m.answer("Как тебе было это делать?")
            else:
                await m.answer("🧠 Фиксируем опыт. Это формирование навыка.")
                await m.answer("Что ты заметил во время выполнения?")
            # post_done_reflection этап убран, сразу переходим к следующему этапу
            u["stage"] = "waiting_next_day"
            await save_user(u, DB_PATH)
            await m.answer("Завтра будет чуть легче, чем сегодня.")
            return

        if text == "↩️ Вернулся(лась)" or "вернулся" in low:
            await log_event(u["user_id"], "training", "return", {"day": day}, DB_PATH, SHEETS_WEBHOOK_URL)
            u["return_count"] += 1
            gamify_apply(u, 1, "return")
            await save_user(u, DB_PATH)
            await m.answer(trainer_say(u.get("trainer_key") or "marsha", "Возврат засчитан. Это ключевой навык."))
            try:
                await m.answer(trainer_say(u.get("trainer_key") or "marsha", PRAISE.get(u.get("trainer_key") or "marsha", "")))
            except Exception:
                pass
            if day == 7:
                await send_weekly_summary(m, u, DB_PATH)
            if not TEST_MODE and day == 3 and u.get("trial_phase") == "trial3":
                await m.answer("Ты уже видел(а):\nэто не мотивация.\nЭто тренировка.\n\n💳 Сейчас — цена со скидкой.", reply_markup=kb_pay_choice)
                u["stage"] = "offer"
                await save_user(u, DB_PATH)
                return
            if not TEST_MODE and day >= 7 and u.get("trial_phase") in ("trial3", "trial7", None):
                await m.answer("Выбирай вариант оплаты:", reply_markup=kb_pay_choice)
                u["stage"] = "offer"
                await save_user(u, DB_PATH)
                return
            await start_day(m, u, day + 1, DB_PATH, SHEETS_WEBHOOK_URL)
            return

        if text == "❓ Сомневаюсь, работает ли" or "сомневаюсь" in low:
            trainer_key = u.get("trainer_key") or "marsha"
            await log_event(u["user_id"], "training", "doubt_pressed", {"trainer": trainer_key}, DB_PATH, SHEETS_WEBHOOK_URL)
            if trainer_key == "skinny":
                doubt_text = "Поможет/не поможет — узнаем только выполнением 60 секунд.\n\nФакт есть или факта нет.\nТретьего не дано.\n\nДелай сегодня — увидишь завтра."
            elif trainer_key == "beck":
                doubt_text = "Это не вопрос веры, это вопрос эффекта.\n\nТренинг навыков работает через повторение.\nМы измеряем микро-метриками: done/return.\n\nЧерез 2 недели будет график нейро-адаптации."
            else:
                doubt_text = "Сомнение нормально.\n\nМы проверяем не верой, а маленькими фактами.\nКаждый день — один факт за 60 секунд.\n\nЕсли не подходит — меняем инструмент."
            await m.answer(trainer_say(trainer_key, doubt_text), reply_markup=kb_doubt_response)
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
            await m.answer(skill_msg, reply_markup=kb_training_main)
            return

        await m.answer("Выбери кнопку 👇", reply_markup=kb_training_main)
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
            await m.answer("Ок. Возвращаемся в тренировку.", reply_markup=kb_training_main)
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
        await m.answer("Выбери кнопкой 👇", reply_markup=kb_crisis_mode)
        return

    if u.get("stage") == "crisis_text":
        if text and text.lower().strip() in {"⬅️ назад", "назад"}:
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            await m.answer("Ок. Возвращаемся в тренировку.", reply_markup=kb_training_main)
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
            await m.answer("Ок. Возвращаемся в тренировку.", reply_markup=kb_training_main)
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
            await m.answer("Возвращаемся в тренировку.", reply_markup=kb_training_main)
            return
        if text == "❌ Нет" or "нет" in low:
            u["pending_plan_change"] = None
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            await log_event(u["user_id"], u.get("stage", ""), "plan_change_reject", {}, DB_PATH, SHEETS_WEBHOOK_URL)
            await m.answer("Ок. План не меняю. Возвращаемся.", reply_markup=kb_training_main)
            return
        await m.answer("Выбери: ✅ Да / ❌ Нет", reply_markup=kb_yes_no)
        return

    # OFFER stage
    if u.get("stage") == "offer":
        if TEST_MODE:
            u["stage"] = "training"
            u["trial_phase"] = "paid"
            await save_user(u, DB_PATH)
            await m.answer("Тестовый режим: продолжаем без оплаты.", reply_markup=kb_training_main)
            return
        low = text.lower().strip()
        if text == "💳 Оплатить со скидкой" or "со скидкой" in low:
            await m.answer("Ок. Скидка по ссылке 👇")
            await m.answer(" ", reply_markup=payment_inline_discount(PAYMENT_URL_DISCOUNT))
            return
        if text == "💳 Оплатить без скидки" or "без скидки" in low:
            await m.answer("Ок. Полная цена по ссылке 👇")
            await m.answer(" ", reply_markup=payment_inline_full(PAYMENT_URL_FULL))
            return
        if text == "➕ Ещё 4 дня без оплаты" or "ещё" in low or "дня" in low:
            await m.answer("Ок. Продолжаем тренировку! 💪")
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            await m.answer("Выбери действие:", reply_markup=kb_training_main)
            return
        if text == "❌ Не готов(а)" or "не готов" in low:
            await m.answer("Ок. Если захочешь продолжить — просто напиши /start")
            u["stage"] = "idle"
            await save_user(u, DB_PATH)
            return
        if "ещ" in low:
            u["trial_days"] = 7
            u["trial_phase"] = "trial7"
            await save_user(u, DB_PATH)
            await m.answer("Ок. Ещё 4 дня в пробе. Продолжаем.", reply_markup=kb_training_main)
            u["stage"] = "training"
            await save_user(u, DB_PATH)
            return
        await m.answer("Выбирай кнопкой 👇", reply_markup=kb_pay_choice)
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
    u["analysis_json"] = json.dumps(comp, ensure_ascii=False)
    u["bucket"] = comp.get("bucket", bucket)
    plan_ids = build_28_day_plan(u["bucket"])
    u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
    u["day"] = 1
    u["stage"] = "confirm_analysis"
    await save_user(u, DB_PATH)
    await log_event(u["user_id"], "analysis", "analysis_shown", {"bucket": u.get("bucket")}, DB_PATH, SHEETS_WEBHOOK_URL)
    msg = f"{comp.get('short_summary', 'Похоже на тебя?')}\n\nЭто похоже на тебя?"
    await m.answer(msg, reply_markup=kb_analysis_confirm)

# ============================================================
# WHISPER TRANSCRIBE
# ============================================================

async def whisper_transcribe(m: Message) -> Optional[str]:
    if not (AI_ANALYSIS_ENABLED and client):
        return None
    if not m.voice:
        return None
    try:
        file = await m.bot.get_file(m.voice.file_id)
        fp = await m.bot.download_file(file.file_path)
        data = fp.read()
        import io
        bio = io.BytesIO(data)
        bio.name = "voice.ogg"
        tr = client.audio.transcriptions.create(model=OPENAI_WHISPER_MODEL, file=bio)
        text = getattr(tr, "text", None)
        if not text:
            try:
                text = tr["text"]
            except Exception:
                text = None
        return (text or "").strip() or None
    except Exception as e:
        log.exception("whisper error: %s", e)
        return None

# ============================================================
# BACKGROUND TASKS
# ============================================================

async def background_ping(bot):
    while True:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT * FROM users")
            rows = await cur.fetchall()
        for row in rows:
            u = dict(zip(USER_FIELDS, row))
            if should_ping(u, 24) and u.get("stage") in {"training", "await_training_target"}:
                try:
                    await bot.send_message(u["chat_id"], inactivity_ping(u.get("trainer_key")))
                except Exception as e:
                    log.warning(f"Не удалось отправить сообщение {u['chat_id']}: {e}")
        await asyncio.sleep(3600)

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
        asyncio.create_task(background_ping(bot))
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
