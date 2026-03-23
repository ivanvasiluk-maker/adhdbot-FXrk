# ============================================================
# FLOWS.PY — Основные логические потоки
# ============================================================

import json
import time
import asyncio
import random
import logging
import os
from typing import Dict, Any, Optional, List
import aiosqlite
from aiogram.types import Message, InputFile, KeyboardButton, ReplyKeyboardMarkup
from aiogram import Bot

from texts import (
    trainer_say, skill_explain, PRAISE, DAILY_LIVE_LINES,
    day_task_text, midday_ping, TRAINER_INTRO_TEXT,
    kb_yes_no, kb_training_main, kb_crisis_mode,
    CRISIS_LIMIT,
)
from skills import SKILLS_DB, get_current_plan, build_28_day_plan, build_plan
from db import get_user, save_user, log_event, USER_FIELDS, is_paid

# Logging
log = logging.getLogger("bot")

# ============================================================
# UTILS
# ============================================================

def clamp_str(s: str, n: int = 1400) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."

# ============================================================
# TRAINER PHOTO SENDING
# ============================================================

async def send_trainer_photo_if_any(chat_id: int, trainer_key: str, bot_token: str):
    """Send trainer photo if a matching file exists in ./images."""
    import os
    import logging
    from aiogram import Bot
    from aiogram.types import InputFile
    base = os.path.join(os.path.dirname(__file__), "images", trainer_key)
    for ext in ("jpg", "jpeg", "png", "webp"):  # ищем любой формат
        for fname in os.listdir(base):
            if fname.lower().endswith(ext):
                path = os.path.join(base, fname)
                try:
                    b = Bot(token=bot_token)
                    await b.send_photo(chat_id, InputFile(path))
                    await b.session.close()
                    logging.info(f"[PHOTO] Sent trainer photo: {path} to chat {chat_id}")
                    return
                except Exception as e:
                    logging.error(f"[PHOTO] Failed to send {path} to chat {chat_id}: {e}")
                    continue
    logging.warning(f"[PHOTO] No photo found for trainer {trainer_key} in {base}")
    return

# Заглушка для send_trainer_introduction
async def send_trainer_introduction(chat_id: int, trainer_key: str, bot_token: str):
    """Send trainer introduction message (stub)."""
    pass

# ============================================================
# DAY SCRIPTS
# ============================================================

async def start_day(m: Message, u: dict, day: int, db_path: str, sheets_webhook: str = ""):
    """Начать день тренировки"""
    plan = get_current_plan(u)
    # Согласовать план: оставить только существующие навыки
    plan = [sid for sid in plan if sid in SKILLS_DB]
    # Если после всех попыток нет ни одного навыка — вывести ошибку и список навыков
    if not plan or len(SKILLS_DB) == 0:
        skills_list = [f"• {v['name']} (код: {k})" for k, v in SKILLS_DB.items()]
        if not skills_list:
            skills_text = "❌ Нет доступных навыков. Обратитесь к администратору."
        else:
            skills_text = "❌ Не удалось найти ни одного навыка для вашего трека. Вот доступные навыки:\n\n" + "\n".join(skills_list) + "\n\nНапишите код навыка, чтобы начать с него."
        await m.answer(skills_text)
        return
    if day < 1:
        day = 1
    if day > len(plan):
        day = len(plan)

    sid = plan[day - 1] if plan else None
    # Если навык не найден — взять первый навык из трека
    if not sid or sid not in SKILLS_DB:
        from skills import build_4_week_plan
        bucket = u.get("bucket") or "mixed"
        plan = build_4_week_plan(bucket)
        plan = [sid for sid in plan if sid in SKILLS_DB]
        if not plan:
            skills_list = [f"• {v['name']} (код: {k})" for k, v in SKILLS_DB.items()]
            if not skills_list:
                skills_text = "❌ Нет доступных навыков. Обратитесь к администратору."
            else:
                skills_text = "❌ Не удалось найти ни одного навыка для вашего трека. Вот доступные навыки:\n\n" + "\n".join(skills_list) + "\n\nНапишите код навыка, чтобы начать с него."
            await m.answer(skills_text)
            return
        sid = plan[0]
        u["plan_json"] = json.dumps(plan, ensure_ascii=False)
        await save_user(u, db_path)
    skill = SKILLS_DB[sid]

    u["day"] = day
    u["stage"] = "await_training_target"
    u["pending_skill_id"] = sid
    u["pending_skill_day"] = day
    await save_user(u, db_path)

    # Утренний быстрый чек — только начиная со 2-го дня
    if day > 1:
        sleep = u.get("last_sleep") or "?"
        anxiety = u.get("last_anxiety") or "?"
        energy = u.get("last_energy") or "?"
        await m.answer(f"🕒 Быстрый чек\nСон: {sleep}\nТревога: {anxiety}\nЭнергия: {energy}")

    # Вопрос перед выдачей навыка
    question = (
        "Перед стартом: что ты прокрастинируешь сегодня?\n"
        "Одна задача/дело, на котором потренируемся.\n"
        "Напиши коротко или нажми 'Пропустить'."
    )
    skip_kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Пропустить")]],
        resize_keyboard=True
    )
    await m.answer(question, reply_markup=skip_kb)

    # 2️⃣ +1 балл прогресса
    u["points"] = int(u.get("points") or 0) + 1
    u["streak"] = int(u.get("streak") or 0) + 1
    # Уровень растет каждые 7 дней
    u["level"] = int(u.get("level") or 1)
    if u["streak"] % 7 == 0:
        u["level"] += 1

    # Кризисный режим: если не заходил 2 дня
    last_active = float(u.get("last_active") or 0)
    now = time.time()
    if last_active and now - last_active > 2*24*3600:
        await m.answer("Ты не сорвался. Ты выпал. Разница есть. Возвращаемся на 3 минуты. Это критично для ADHD.")
        u["streak"] = 0

    u["last_active"] = now
    await save_user(u, db_path)

async def start_day1(m: Message, u: Dict[str, Any], db_path: str):
    """День 1 - специальный скрипт"""
    name = u.get("name") or "друг"
    trainer_key = u.get("trainer_key") or "marsha"

    plan_ids = json.loads(u.get("plan_json") or "[]")
    if not plan_ids:
        plan_ids = build_plan(u.get("bucket") or "mixed")
        u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
        await save_user(u, db_path)

    sid = plan_ids[0]
    skill = SKILLS_DB.get(sid) or list(SKILLS_DB.values())[0]

    msg = (
        f"🌅 {name}, День 1\n\n"
        "Мы не лечим. Мы тренируем навыки.\n"
        "Считается попытка на 60–120 секунд.\n\n"
        f"🧩 Навык: {skill['name']}\n"
        f"🎯 Цель: {skill['goal']}\n"
        f"✅ Как: {skill_explain(trainer_key, skill)}\n\n"
        "Вечером спросим: сделал(а)? вернулся(лась)?"
    )
    await m.answer(trainer_say(trainer_key, msg), reply_markup=kb_training_main)

async def start_day_simple(m: Message, u: Dict[str, Any], day: int, db_path: str):
    """Универсальный скрипт для любого дня"""
    name = u.get("name") or "друг"
    trainer_key = u.get("trainer_key") or "marsha"

    plan_ids = json.loads(u.get("plan_json") or "[]")
    if not plan_ids:
        plan_ids = build_plan(u.get("bucket") or "mixed")
        u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
        await save_user(u, db_path)

    day = max(1, min(day, len(plan_ids)))
    sid = plan_ids[day - 1]
    skill = SKILLS_DB.get(sid) or list(SKILLS_DB.values())[0]

    msg = (
        f"🌅 {name}, День {day}\n\n"
        f"🧩 Навык: {skill['name']}\n"
        f"🎯 Цель: {skill['goal']}\n"
        f"✅ Как: {skill_explain(trainer_key, skill)}\n\n"
        "Считается попытка 60–120 сек."
    )
    await m.answer(trainer_say(trainer_key, msg), reply_markup=kb_training_main)

    u["day"] = day
    u["stage"] = "await_training_target"
    u["pending_skill_id"] = sid
    u["pending_skill_day"] = day
    await save_user(u, db_path)

    await m.answer(
        "Перед стартом: что ты прокрастинируешь сегодня?\n"
        "Одна задача/дело, на котором потренируемся.\n"
        "Напиши коротко или нажми 'Пропустить'.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Пропустить")]],
            resize_keyboard=True
        )
    )

async def advance_day(m: Message, u: Dict[str, Any], next_day: int, db_path: str):
    """Перейти на следующий день"""
    u["day"] = next_day
    await save_user(u, db_path)
    await start_day_simple(m, u, next_day, db_path)

# ============================================================
# CRISIS HANDLER
# ============================================================

async def handle_crisis(m: Message, u: dict, user_text: str, db_path: str, sheets_webhook: str, client=None, model: str = "gpt-4o-mini"):
    """Обработка кризиса пользователя"""
    from db import gamify_apply
    fallback_sid = "return_no_punish"  # безопасный навык по умолчанию
    
    name = u.get("name") or "друг"
    trainer_key = u.get("trainer_key") or "marsha"
    bucket = u.get("bucket") or "mixed"

    # increment first; limit free crisis uses
    u["crisis_count"] = int(u.get("crisis_count") or 0) + 1
    await save_user(u, db_path)

    if not is_paid(u) and int(u.get("crisis_count") or 0) > CRISIS_LIMIT:
        await m.answer("🆘 Кризис — доступен без ограничений в полной версии.")
        return

    await log_event(u["user_id"], u.get("stage",""), "crisis_message", {"len": len(user_text)}, db_path, sheets_webhook)
    gamify_apply(u, 1, "crisis_used")

    await m.answer(trainer_say(trainer_key, "Ок. Сейчас быстро стабилизируем и вернём контроль."))
    
    # AI crisis help (если доступно)
    try:
        r = await ai_crisis_help(trainer_key, bucket, user_text, client, model)
    except Exception as e:
        log.error(f"ai_crisis_help failed: {e}")
        # жёсткий фолбэк, чтобы не было тишины
        r = {
            "support": "Ок. Берём один шаг, чтобы вернуть контроль.",
            "skill_id": "return_no_punish",
            "why_this": "Возврат без самонаказания снимает ступор и даёт действие.",
            "micro_step": "Скажи «Возвращаюсь» и сделай 60–120 сек самого первого шага.",
            "plan_change": None,
        }

    sid = r.get("skill_id") or fallback_sid
    if sid not in SKILLS_DB:
        log.warning(f"[CRISIS] skill_id {sid} not in SKILLS_DB, fallback to {fallback_sid}")
        sid = fallback_sid
    from skills import format_skill, suggest_alternative_skill
    trainer_key = u.get("trainer_key") or "marsha"
    # Use format_skill for modern skill presentation
    skill_msg = format_skill(sid, trainer_key)
    micro_step = r.get("micro_step") or SKILLS_DB[sid].get("minimum", SKILLS_DB[sid].get("how"))
    msg = (
        f"🆘 {name}, коротко:\n"
        f"{r['support']}\n\n"
        f"{skill_msg}\n"
        f"✅ Микро-шаг: {micro_step}\n\n"
        f"Почему это сейчас: {r['why_this']}"
    )
    await m.answer(msg)

    pc = r.get("plan_change")
    if pc:
        day_num = int(u.get("day") or 1) + int(pc.get("day_offset") or 1)
        new_sid = pc.get("replace_with")
        if new_sid in SKILLS_DB:
            u["pending_plan_change"] = json.dumps({"day_num": day_num, "skill_id": new_sid}, ensure_ascii=False)
            u["stage"] = "crisis_plan_confirm"
            await save_user(u, db_path)
            await m.answer(
                f"Хочешь, я на завтра (день {day_num}) заменю навык на:\n"
                f"➡️ {SKILLS_DB[new_sid]['name']} ?",
                reply_markup=kb_yes_no
            )
            return

    u["stage"] = "training"
    await save_user(u, db_path)
    await m.answer("Возвращаемся в тренировку 👇", reply_markup=kb_training_main)

# ============================================================
# AI CRISIS HELP
# ============================================================

async def ai_crisis_help(trainer_key: str, bucket: str, user_text: str, client=None, model: str = "gpt-4o-mini") -> dict:
    """AI помощь в кризисе"""
    fallback_sid = "return_no_punish"
    if not (client and model):
        # fallback
        return {
            "support": "Ок. Сейчас не обсуждаем жизнь целиком. Берём один шаг, который можно сделать прямо сейчас.",
            "skill_id": fallback_sid,
            "why_this": "Ключ — вернуть контроль через возврат без самонаказания.",
            "micro_step": "Скажи «Я возвращаюсь — это и есть навык» и сделай один шаг ≤ 2 минут.",
            "plan_change": None
        }

    allowed_ids = list(SKILLS_DB.keys())
    skill_catalog = [f"{sid}: {SKILLS_DB[sid].get('name','')}" for sid in allowed_ids]
    system = (
        "Ты — CBT/DBT психолог в формате кризисного ответа.\n"
        "Контекст: клиент в кризисе из-за прокрастинации. Нужна помощь 'здесь и сейчас'.\n"
        "Твоя задача: кратко поддержать, дать понятный шаг и выбрать навык из базы навыков.\n"
        "Это НЕ терапия и НЕ диагноз. Нельзя обещать лечение. Без клинических терминов.\n"
        "Всегда выбирай skill_id ТОЛЬКО из allowed_ids.\n"
        "Каталог навыков: " + " | ".join(skill_catalog) + "\n"
        "Формат ответа СТРОГО JSON без комментариев и текста вокруг:\n"
        "{\n"
        "  'support': '1-2 предложения поддержки/валидизации',\n"
        "  'skill_id': '<один id из allowed_ids>',\n"
        "  'why_this': 'почему этот навык сейчас',\n"
        "  'micro_step': 'один шаг ≤50 слов, сделать прямо сейчас',\n"
        "  'plan_change': null\n"
        "}\n"
        "Тон выбирай по trainer_style: skinny=жёстко, marsha=мягко, beck=логично."
    )
    user = json.dumps({
        "trainer_style": trainer_key,
        "bucket": bucket,
        "user_text": user_text,
        "allowed_ids": allowed_ids
    }, ensure_ascii=False)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role":"system","content":system},{"role":"user","content":user}],
            temperature=0.25,
        )
        data = _extract_json(resp.choices[0].message.content or "") or {}
    except Exception:
        data = {}
    
    sid = data.get("skill_id")
    if sid not in SKILLS_DB:
        sid = fallback_sid
    pc = data.get("plan_change")
    if pc:
        rid = pc.get("replace_with")
        if rid not in SKILLS_DB:
            pc = None
    return {
        "support": (data.get("support") or "Ок. Сейчас берём один шаг, чтобы вернуть контроль.").strip(),
        "skill_id": sid,
        "why_this": (data.get("why_this") or "Возврат без самонаказания убирает ступор и даёт быстрое действие.").strip(),
        "micro_step": (data.get("micro_step") or "Скажи «Возвращаюсь» и сделай 60–120 сек задачи или самый первый шаг.").strip(),
        "plan_change": pc
    }

# ============================================================
# ANALYSIS
# ============================================================

def _extract_json(text: str) -> Optional[dict]:
    """Извлечь JSON из текста"""
    import re
    if not text:
        return None
    text = text.strip()
    # try direct
    try:
        return json.loads(text)
    except Exception:
        pass
    # try find {...}
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

async def ai_analyze(user_text: str, client=None, model: str = "gpt-4o-mini") -> dict:
    """Быстрый AI анализ"""
    if not (client and model):
        return {
            "bucket": "mixed",
            "summary": "Похоже на смешанный профиль: немного тревоги + избегание + низкий ресурс.",
            "confidence": 0.55,
            "top_signals": ["избегание", "тревога", "низкая энергия"],
            "first_action": "Сделай один микро-старт ≤ 2 минут."
        }

    from texts import build_ai_system_prompt
    system = build_ai_system_prompt()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": clamp_str(user_text, 1500)},
        ],
        temperature=0.3,
    )
    data = _extract_json(resp.choices[0].message.content or "")
    if not data:
        return {
            "bucket": "mixed",
            "summary": "Похоже на смешанный профиль: тревога/энергия/внимание пересекаются.",
            "confidence": 0.45,
            "top_signals": ["смешанные сигналы"],
            "first_action": "Сделай один микро-старт ≤ 2 минут."
        }

    bucket = data.get("bucket") or "mixed"
    if bucket not in ("anxiety", "low_energy", "distractibility", "mixed"):
        bucket = "mixed"

    return {
        "bucket": bucket,
        "summary": clamp_str(data.get("summary") or "", 400),
        "confidence": float(data.get("confidence") or 0.5),
        "top_signals": data.get("top_signals") or [],
        "first_action": clamp_str(data.get("first_action") or "", 300),
    }

    # Мини-ИИ-рефлексия после выполнения
    async def ai_micro_reflect(user_text: str, trainer_key: str) -> str:
        """
        Короткий рефлексивный ответ на опыт выполнения.
        1–2 предложения максимум.
        """
        prompt = f"""
    Пользователь описал опыт выполнения навыка:
    \n"{user_text}"\n
    Ответь:
    - 1–2 короткими предложениями
    - Без лекций
    - Поддерживающе
    - В стиле тренера: {trainer_key}

    Если есть положительный момент — усили его.
    Если сомнение — нормализуй.
    Без длинных объяснений.
    """
        try:
            import openai
            client = openai.OpenAI()
            r = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.6,
                max_tokens=120
            )
            return r.choices[0].message.content.strip()
        except Exception:
            return "Это важный шаг. Продолжай."

async def ai_analyze_comprehensive(user_text: str, trainer_key: str = "marsha", client=None, model: str = "gpt-4o-mini") -> dict:
    """Подробный AI анализ"""
    from texts import AI_ANALYSIS_SYSTEM_PROMPT
    
    if not (client and model):
        # fallback comprehensive response
        return {
            "bucket": "mixed",
            "short_summary": "Похоже, ты сталкиваешься с несколькими вызовами сразу: тревога мешает начать, внимание сложно удержать.",
            "what_is_happening": "Тебе сложно начать важное дело и удержать на нём внимание. Сначала переживаешь или откладываешь, потом отвлекаешься.",
            "why_it_happens": "Мозг ищет более лёгкую стимуляцию и избегает дискомфорта начала. Это не лень — это автоматический защитный паттерн.",
            "not_your_fault_or_control_zone": "Это не твоя вина и не слабость. Это навык, который пока не натренирован. Ты можешь это изменить.",
            "why_change_is_possible": "Навыки саморегуляции, начала и удержания внимания поддаются тренировке. В течение 4–8 недель увидишь реальные сдвиги.",
            "training_path": "Мы будем двигаться маленькими шагами, без перегруза. Сначала натренируем одно, потом подключим другое.",
            "skills_focus": ["начало без давления", "удержание внимания", "возврат без самокритики"],
            "timeline": "Первые сдвиги - 2–3 недели. Устойчивость - 4–8 недель.",
            "support_guarantee": "Если метод не подойдёт - мы его заменим. Ты не один(а).",
            "closing_reassurance": "Это работает. И ты справишься."
        }

    system = AI_ANALYSIS_SYSTEM_PROMPT
    
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": f"""Проанализируй следующее описание и верни JSON с этой структурой:
{{
  "bucket": "anxiety|low_energy|distractibility|mixed",
  "short_summary": "краткое резюме",
  "what_is_happening": "что происходит с человеком",
  "why_it_happens": "почему это происходит",
  "not_your_fault_or_control_zone": "это не твоя вина",
  "why_change_is_possible": "почему это можно изменить",
  "training_path": "путь тренировки",
  "skills_focus": ["навык1", "навык2", "навык3"],
  "timeline": "сроки улучшений",
  "support_guarantee": "гарантия поддержки",
  "closing_reassurance": "финальное перехватывание"
}}

Описание человека:
{clamp_str(user_text, 1500)}"""},
        ],
        temperature=0.3,
    )
    data = _extract_json(resp.choices[0].message.content or "")
    if not data:
        return {
            "bucket": "mixed",
            "short_summary": "Похоже на смешанный профиль: несколько вызовов одновременно.",
            "what_is_happening": "Тебе сложно с несколькими аспектами одновременно.",
            "why_it_happens": "Разные функции мозга перегружены одновременно.",
            "not_your_fault_or_control_zone": "Это не твоя вина. Это паттерн, который тренируется.",
            "why_change_is_possible": "С правильной тренировкой все эти навыки развиваются.",
            "training_path": "Шаг за шагом, с мягкой поддержкой.",
            "skills_focus": ["начало", "удержание", "возврат"],
            "timeline": "Первые сдвиги - 2–3 недели.",
            "support_guarantee": "Мы найдём подходящий метод для тебя.",
            "closing_reassurance": "Ты справишься."
        }

    # Ensure all required fields exist
    result = {
        "bucket": data.get("bucket") or "mixed",
        "short_summary": clamp_str(data.get("short_summary") or "", 200),
        "what_is_happening": clamp_str(data.get("what_is_happening") or "", 400),
        "why_it_happens": clamp_str(data.get("why_it_happens") or "", 400),
        "not_your_fault_or_control_zone": clamp_str(data.get("not_your_fault_or_control_zone") or "", 400),
        "why_change_is_possible": clamp_str(data.get("why_change_is_possible") or "", 400),
        "training_path": clamp_str(data.get("training_path") or "", 400),
        "skills_focus": data.get("skills_focus") or ["внимание", "начало", "поддержание"],
        "timeline": clamp_str(data.get("timeline") or "", 200),
        "support_guarantee": clamp_str(data.get("support_guarantee") or "", 200),
        "closing_reassurance": clamp_str(data.get("closing_reassurance") or "", 200),
    }

    bucket = result.get("bucket") or "mixed"
    if bucket not in ("anxiety", "low_energy", "distractibility", "mixed"):
        result["bucket"] = "mixed"

    return result

async def run_analysis(m: Message, u: Dict[str, Any], user_text: str, db_path: str, sheets_webhook: str = "", client=None, model: str = "gpt-4o-mini"):
    """Запустить анализ"""
    from texts import kb_analysis_confirm
    
    # Try quick analysis first (keeps fallback behavior)
    r = await ai_analyze(user_text, client, model)

    # Attempt to get a comprehensive analysis (may fallback internally)
    comp = await ai_analyze_comprehensive(user_text, u.get("trainer_key", "marsha"), client, model)

    # Prefer comprehensive bucket if present
    bucket = comp.get("bucket") or r.get("bucket") or "mixed"
    u["bucket"] = bucket

    # Save full analysis (include user_text for reference)
    comp_to_store = dict(comp)
    comp_to_store["user_text"] = clamp_str(user_text, 1000)
    u["analysis_json"] = json.dumps(comp_to_store, ensure_ascii=False)

    # build plan (28 days)
    plan_ids = build_28_day_plan(bucket)
    u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
    u["day"] = 1

    # set stage to confirm comprehensive analysis and persist
    u["stage"] = "confirm_analysis"
    await save_user(u, db_path)

    # Log that analysis was shown
    await log_event(u["user_id"], "analysis", "analysis_shown", {"bucket": u.get("bucket")}, db_path, sheets_webhook)

    # Short selling text + buttons (matches comprehensive flow)
    short_text = comp.get("short_summary") or r.get("summary") or "Похоже на тебя?"
    msg = f"{short_text}\n\nЭто похоже на тебя?"

    await m.answer(msg, reply_markup=kb_analysis_confirm)

# ============================================================
# PROGRESS & REPORTS
# ============================================================

async def send_weekly_summary(m: Message, u: dict, db_path: str):
    """Отправить еженедельный отчет"""
    uid = u["user_id"]
    since = time.time() - 7 * 24 * 3600

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT event, COUNT(*) FROM events WHERE user_id=? AND ts>=? GROUP BY event",
            (uid, since)
        )
        rows = await cur.fetchall()

    stats = {e: c for e, c in rows}

    msg = (
        f"📊 {u.get('name') or 'друг'}, итоги недели:\n\n"
        f"✅ попытки: {stats.get('done',0)}\n"
        f"↩️ возвраты: {stats.get('return',0)}\n"
        f"🆘 кризисы: {stats.get('crisis_message',0)}\n\n"
        "Главное:\n"
        "ты не бросил(а).\n"
        "Значит, система работает."
    )

    await m.answer(msg)

async def send_progress_report(m: Message, u: dict, db_path: str):
    """Отправить отчет о прогрессе"""
    from texts import gamify_status_line
    
    uid = u["user_id"]
    since = time.time() - 7 * 24 * 3600
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT event, COUNT(*) FROM events WHERE user_id=? AND ts>=? GROUP BY event",
            (uid, since)
        )
        rows = await cur.fetchall()

    counts = {e: c for e, c in rows}
    done = counts.get("done", 0)
    ret = counts.get("return", 0)
    crisis = counts.get("crisis_message", 0)

    plan = get_current_plan(u)
    day = int(u.get("day") or 1)
    next_skill = SKILLS_DB[plan[min(day, len(plan)-1)]]["name"] if plan else "—"

    msg = (
        "📊 Твой прогресс за 7 дней:\n"
        f"✅ выполнено: {done}\n"
        f"↩️ возвратов: {ret}\n"
        f"🆘 кризис-обращений: {crisis}\n\n"
        f"{gamify_status_line(u)}\n\n"
        f"➡️ Следующий навык по плану: {next_skill}"
    )
    await m.answer(msg)
    await log_event(uid, u.get("stage",""), "progress_view", {"done": done, "return": ret, "crisis": crisis}, db_path)
