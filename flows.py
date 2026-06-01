# ============================================================
# FLOWS.PY — Основные логические потоки
# ============================================================

import json
import time
import asyncio
import random
import logging
import os
import re
from typing import Dict, Any, Optional, List
import aiosqlite
from aiogram.types import Message, FSInputFile, KeyboardButton, ReplyKeyboardMarkup
from aiogram import Bot

from texts import (
    trainer_say, skill_explain, PRAISE, DAILY_LIVE_LINES,
    day_task_text, midday_ping, TRAINER_INTRO_TEXT,
    kb_yes_no, kb_training_main, kb_crisis_mode, keyboard_button_count,
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
    base = os.path.join(os.path.dirname(__file__), "images", trainer_key)
    if not os.path.isdir(base):
        logging.warning(f"[PHOTO] Directory not found for trainer {trainer_key}: {base}")
        return

    for ext in ("jpg", "jpeg", "png", "webp"):  # ищем любой формат
        for fname in os.listdir(base):
            if fname.lower().endswith(ext):
                path = os.path.join(base, fname)
                b = Bot(token=bot_token)
                try:
                    await b.send_photo(chat_id, FSInputFile(path))
                    logging.info(f"[PHOTO] Sent trainer photo: {path} to chat {chat_id}")
                    return
                except Exception as e:
                    logging.error(f"[PHOTO] Failed to send {path} to chat {chat_id}: {e}")
                    continue
                finally:
                    await b.session.close()
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
    button_count = keyboard_button_count(kb_training_main)
    await log_event(u["user_id"], "training", "keyboard_shown" if button_count <= 5 else "keyboard_warning", {"keyboard": "training_main", "button_count": button_count}, db_path)
    await m.answer(trainer_say(trainer_key, msg), reply_markup=kb_training_main if button_count <= 5 else None)

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
    button_count = keyboard_button_count(kb_training_main)
    await log_event(u["user_id"], "training", "keyboard_shown" if button_count <= 5 else "keyboard_warning", {"keyboard": "training_main", "button_count": button_count}, db_path)
    await m.answer(trainer_say(trainer_key, msg), reply_markup=kb_training_main if button_count <= 5 else None)

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
    """Short crisis flow: stabilize first, do not explain at length."""
    from db import gamify_apply
    from texts import kb_crisis_stabilize, crisis_stabilize_text

    u["crisis_count"] = int(u.get("crisis_count") or 0) + 1
    await save_user(u, db_path)

    if not is_paid(u) and int(u.get("crisis_count") or 0) > CRISIS_LIMIT:
        await m.answer("🆘 Кризис — доступен без ограничений в полной версии.")
        return

    await log_event(u["user_id"], u.get("stage", ""), "crisis_message", {"len": len(user_text or "")}, db_path, sheets_webhook)
    gamify_apply(u, 1, "crisis_used")
    u["stage"] = "crisis_stabilize"
    await save_user(u, db_path)
    await log_event(u["user_id"], "crisis_stabilize", "crisis_stabilize_shown", {}, db_path, sheets_webhook)
    await m.answer(crisis_stabilize_text(), reply_markup=kb_crisis_stabilize)

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

ANALYSIS_FORBIDDEN_REPLACEMENTS = {
    "управление временем": "вход в задачу",
    "концентрация": "удержание внимания",
    "постановка целей": "прояснение первого шага",
    "навыки саморегуляции не выдерживают нагрузку": "вход в задачу становится слишком дорогим",
    "тренируется как мышцы": "закрепляется короткими повторами",
    "многие сталкиваются, это нормально": "это рабочий паттерн, его можно разобрать по шагам",
}


def _clean_analysis_phrase(value: Any, fallback: str, limit: int = 120) -> str:
    text = clamp_str(str(value or "").strip(), limit)
    if not text:
        text = fallback
    lowered = text.lower()
    for forbidden, replacement in ANALYSIS_FORBIDDEN_REPLACEMENTS.items():
        if forbidden in lowered:
            text = text.replace(forbidden, replacement).replace(forbidden.capitalize(), replacement.capitalize())
            lowered = text.lower()
    return text


def analysis_needs_more_input(user_text: str) -> bool:
    text = (user_text or "").strip().lower()
    if len(text) < 24:
        return True
    words = re.findall(r"[\wёа-яА-Я-]+", text, flags=re.IGNORECASE)
    if len(words) < 5:
        return True
    vague = {"прокрастинация", "прокрастинирую", "лень", "не могу", "сложно", "тяжело", "ничего", "всё", "все"}
    return len(words) <= 7 and any(item in text for item in vague)


def analysis_need_more_text() -> str:
    return (
        "Пока сигналов мало.\n"
        "Чтобы собрать режим точнее, мне нужно еще 1-2 ответа.\n\n"
        "Что чаще ломает вход?\n"
        "😵 Перегруз\n"
        "😬 Страх ошибки\n"
        "📱 Отвлечения\n"
        "🌀 Слишком много вариантов\n"
        "😶 Не вижу смысла"
    )


def _infer_analysis_fields(user_text: str, bucket: str = "mixed") -> Dict[str, Any]:
    text = (user_text or "").lower()
    if any(x in text for x in ("идеал", "идеаль", "ошиб", "плохо", "стыд", "оцен")):
        pattern = "риск сделать неидеально делает старт слишком дорогим"
        behavior = "подготовку, перепроверку или откладывание первого шага"
        skills = ["плохой первый шаг", "вход без идеального плана", "возврат после выпадения"]
    elif any(x in text for x in ("устал", "нет сил", "выгор", "сон", "апат")) or bucket == "low_energy":
        pattern = "перегруз появляется раньше первого действия"
        behavior = "сон, зависание или самый лёгкий короткий стимул"
        skills = ["минимальный вход", "снижение шага", "возврат без самонаказания"]
    elif any(x in text for x in ("телефон", "соц", "ютуб", "скрол", "отвлек", "увод")) or bucket == "distractibility":
        pattern = "первый шаг теряется среди быстрых стимулов"
        behavior = "телефон, вкладки и мелкие срочные дела"
        skills = ["одна вкладка", "короткое удержание внимания", "возврат после выпадения"]
    elif any(x in text for x in ("непонят", "не ясно", "не знаю", "вариант", "слишком много", "больш")):
        pattern = "задача слишком расплывчатая для первого шага"
        behavior = "планирование, выбор вариантов и подготовку вместо старта"
        skills = ["назвать задачу", "первый физический шаг", "вход без полного плана"]
    elif any(x in text for x in ("код", "it", "айти", "проект", "задач", "документ", "тикет")):
        pattern = "задачи без чёткого конца и быстрого завершения"
        behavior = "организацию, мелкие дела и поиск идеального состояния"
        skills = ["плохой первый шаг", "вход без идеального плана", "короткое удержание внимания"]
    else:
        pattern = "первый шаг ощущается слишком большим и мутным"
        behavior = "откладывание, подготовку или быстрые отвлечения"
        skills = ["микро-старт", "прояснение первого шага", "возврат после выпадения"]

    if any(x in text for x in ("рядом", "вместе", "друг", "созвон", "коворк")):
        signal = "легче начинать рядом с другим человеком"
    elif any(x in text for x in ("дедлайн", "срок", "горит")):
        signal = "срок включает действие, но слишком поздно"
    elif any(x in text for x in ("помог", "получ", "легче", "лучше")):
        signal = "уже есть условия, где вход становится легче"
    else:
        signal = "ты уже замечаешь момент ухода от старта"

    return {
        "specific_pattern": pattern,
        "avoidance_behavior": behavior,
        "useful_signal": signal,
        "skills_focus": skills,
    }


def safe_analysis_memory(user_text: str, comp: Optional[Dict[str, Any]] = None, *, needs_more: bool = False) -> Dict[str, Any]:
    """Return non-verbatim analysis memory for persistence.

    Do not store the user's full text/transcript/confession. Keep only short
    categories and inferred behavioral signals that are safe for profile rebuilds.
    """
    comp = comp or {}
    bucket = str(comp.get("bucket") or "mixed")
    inferred = _infer_analysis_fields(user_text or "", bucket)
    memory = {
        "input_len": len(user_text or ""),
        "input_signal_summary": {
            "specific_pattern": clamp_str(comp.get("specific_pattern") or inferred.get("specific_pattern"), 120),
            "avoidance_behavior": clamp_str(comp.get("avoidance_behavior") or inferred.get("avoidance_behavior"), 120),
            "useful_signal": clamp_str(comp.get("useful_signal") or inferred.get("useful_signal"), 120),
            "skills_focus": (comp.get("skills_focus") or inferred.get("skills_focus") or [])[:4],
        },
    }
    if needs_more:
        memory["needs_more"] = True
    return memory


def normalize_analysis(comp: Dict[str, Any], user_text: str, quick: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    quick = quick or {}
    bucket = comp.get("bucket") or quick.get("bucket") or "mixed"
    inferred = _infer_analysis_fields(user_text, bucket)
    skills = comp.get("skills_focus") or inferred["skills_focus"]
    if not isinstance(skills, list):
        skills = inferred["skills_focus"]
    normalized = dict(comp)
    normalized["bucket"] = bucket if bucket in ("anxiety", "low_energy", "distractibility", "mixed") else "mixed"
    normalized["specific_pattern"] = _clean_analysis_phrase(comp.get("specific_pattern"), inferred["specific_pattern"], 140)
    normalized["avoidance_behavior"] = _clean_analysis_phrase(comp.get("avoidance_behavior"), inferred["avoidance_behavior"], 140)
    normalized["useful_signal"] = _clean_analysis_phrase(comp.get("useful_signal"), inferred["useful_signal"], 140)
    normalized["skills_focus"] = [_clean_analysis_phrase(skill, fallback, 80) for skill, fallback in zip((skills + inferred["skills_focus"])[:3], inferred["skills_focus"])]
    if len(normalized["skills_focus"]) < 3:
        normalized["skills_focus"] = (normalized["skills_focus"] + inferred["skills_focus"])[:3]
    return normalized

async def ai_analyze(user_text: str, client=None, model: str = "gpt-4o-mini") -> dict:
    """Быстрый AI анализ"""
    fallback = {
        "bucket": "mixed",
        "summary": "Похоже на смешанный профиль: немного тревоги + избегание + низкий ресурс.",
        "confidence": 0.55,
        "top_signals": ["избегание", "тревога", "низкая энергия"],
        "first_action": "Сделай один микро-старт ≤ 2 минут.",
        "analysis_fallback": True,
    }
    if not (client and model):
        log.warning("[AI] Quick analysis fallback: OpenAI client or model is not configured")
        return fallback

    from texts import build_ai_system_prompt
    system = build_ai_system_prompt()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": clamp_str(user_text, 1500)},
            ],
            temperature=0.3,
        )
        data = _extract_json(resp.choices[0].message.content or "")
    except Exception as e:
        log.exception("[AI] Quick analysis failed, using fallback: %s", e)
        return fallback
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
    
    fallback = normalize_analysis({
        "bucket": "mixed",
        "analysis_fallback": True,
        "selected_skill": "open_only",
    }, user_text)
    if not (client and model):
        log.warning("[AI] Comprehensive analysis fallback: OpenAI client or model is not configured")
        return fallback

    system = AI_ANALYSIS_SYSTEM_PROMPT
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"""Проанализируй следующее описание и верни JSON с этой структурой:
{{
  "bucket": "anxiety|low_energy|distractibility|mixed",
  "specific_pattern": "главный конкретный стопор",
  "avoidance_behavior": "куда мозг уходит вместо действия",
  "useful_signal": "полезный сигнал из описания",
  "skills_focus": ["навык1", "навык2", "навык3"],
  "selected_skill": "skill_id если очевидно или open_only"
}}

Описание человека:
{clamp_str(user_text, 1500)}"""},
            ],
            temperature=0.3,
        )
        data = _extract_json(resp.choices[0].message.content or "")
    except Exception as e:
        log.exception("[AI] Comprehensive analysis failed, using fallback: %s", e)
        return fallback
    if not data:
        return normalize_analysis({
            "bucket": "mixed",
            "analysis_fallback": True,
            "selected_skill": "open_only",
        }, user_text)

    result = normalize_analysis({
        "bucket": data.get("bucket") or "mixed",
        "specific_pattern": data.get("specific_pattern"),
        "avoidance_behavior": data.get("avoidance_behavior"),
        "useful_signal": data.get("useful_signal"),
        "skills_focus": data.get("skills_focus"),
        "selected_skill": data.get("selected_skill") or "open_only",
    }, user_text)

    return result


def format_comprehensive_analysis(comp: Dict[str, Any], quick: Optional[Dict[str, Any]] = None) -> str:
    """Собрать короткий точный разбор без generic GPT-фраз."""
    quick = quick or {}
    normalized = normalize_analysis(comp, comp.get("user_text") or quick.get("user_text") or "", quick)
    skills = normalized.get("skills_focus") or []
    skills_text = "\n".join(f"— {_clean_analysis_phrase(skill, 'микро-старт', 80)}" for skill in skills[:3])
    if not skills_text:
        skills_text = "— микро-старт\n— первый физический шаг\n— возврат после выпадения"

    return (
        "Коротко, что вижу:\n\n"
        "1. Не лень и не дисциплина.\n"
        f"2. Главный стопор: {normalized['specific_pattern']}.\n"
        f"3. Мозг уходит в {normalized['avoidance_behavior']}.\n"
        f"4. Уже виден сигнал: {normalized['useful_signal']}.\n\n"
        "Тренировать будем:\n"
        f"{skills_text}"
    )

async def run_analysis(m: Message, u: Dict[str, Any], user_text: str, db_path: str, sheets_webhook: str = "", client=None, model: str = "gpt-4o-mini"):
    """Запустить анализ"""
    from texts import kb_analysis_confirm, kb_analysis_need_more, preliminary_hypothesis_note

    if analysis_needs_more_input(user_text):
        u["analysis_json"] = json.dumps(safe_analysis_memory(user_text, {"bucket": u.get("bucket") or "mixed"}, needs_more=True), ensure_ascii=False)
        u["stage"] = "analysis_need_more"
        await save_user(u, db_path)
        await log_event(u["user_id"], "analysis", "analysis_needs_more_input", {"len": len(user_text or "")}, db_path, sheets_webhook)
        button_count = keyboard_button_count(kb_analysis_need_more)
        await log_event(
            u["user_id"],
            "analysis",
            "keyboard_shown" if button_count <= 5 else "keyboard_warning",
            {"keyboard": "analysis_need_more", "button_count": button_count},
            db_path,
            sheets_webhook,
        )
        await m.answer(analysis_need_more_text(), reply_markup=kb_analysis_need_more if button_count <= 5 else None)
        return

    # Try quick analysis first (keeps fallback behavior)
    r = await ai_analyze(user_text, client, model)

    # Attempt to get a comprehensive analysis (may fallback internally)
    comp = await ai_analyze_comprehensive(user_text, u.get("trainer_key", "marsha"), client, model)

    # Prefer comprehensive bucket if present
    bucket = comp.get("bucket") or r.get("bucket") or "mixed"
    u["bucket"] = bucket
    if comp.get("analysis_fallback") or r.get("analysis_fallback"):
        await log_event(u["user_id"], "analysis", "openai_error", {"error_type": "analysis_fallback", "error_source": "run_analysis"}, db_path, sheets_webhook)

    # Save normalized analysis without storing raw user text/transcripts.
    comp = normalize_analysis(comp, user_text, r)
    comp_to_store = dict(comp)
    comp_to_store.pop("user_text", None)
    comp_to_store.update(safe_analysis_memory(user_text, comp_to_store))
    u["analysis_json"] = json.dumps(comp_to_store, ensure_ascii=False)

    # build plan (28 days)
    plan_ids = build_28_day_plan(bucket)
    if (comp.get("analysis_fallback") or r.get("analysis_fallback")) and "open_only" in SKILLS_DB:
        plan_ids[0] = "open_only"
    u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
    u["day"] = 1

    # set stage to confirm comprehensive analysis and persist
    u["stage"] = "confirm_analysis"
    await save_user(u, db_path)

    # Log that analysis was shown
    await log_event(u["user_id"], "analysis", "diagnosis_completed", {"bucket": u.get("bucket")}, db_path, sheets_webhook)
    await log_event(u["user_id"], "analysis", "analysis_shown", {"bucket": u.get("bucket")}, db_path, sheets_webhook)

    # Show the actual precise analysis before asking for confirmation.
    msg = f"{format_comprehensive_analysis(comp_to_store, r)}\n\n{preliminary_hypothesis_note()}\n\nЭто похоже на тебя?"

    button_count = keyboard_button_count(kb_analysis_confirm)
    await log_event(
        u["user_id"],
        "analysis",
        "keyboard_shown" if button_count <= 5 else "keyboard_warning",
        {"keyboard": "analysis", "button_count": button_count},
        db_path,
        sheets_webhook,
    )
    await m.answer(msg, reply_markup=kb_analysis_confirm if button_count <= 5 else None)

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
