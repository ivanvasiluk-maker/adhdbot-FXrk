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
    kb_yes_no, kb_training_main, kb_crisis_mode, keyboard_button_count, MAX_KEYBOARD_BUTTONS,
    CRISIS_LIMIT, progress_achievements_text, growth_history_text,
)
from skills import SKILLS_DB, get_current_plan, build_28_day_plan, build_plan
from db import (
    get_user, save_user, log_event, USER_FIELDS, is_paid, update_user_profile,
    diagnosis_user_profile_patch, get_user_profile, render_development_avatar,
    render_development_mirror_report, daily_profile_explanation, determine_development_focus,
    get_action_metrics,
)

# Logging
log = logging.getLogger("bot")

# ============================================================
# UTILS
# ============================================================

def set_skill_explanation_context(u: Dict[str, Any], skill: Dict[str, Any], sid: str) -> None:
    title = skill.get("name") or sid or "навык"
    reason = skill.get("why_short") or skill.get("why") or skill.get("goal") or "Этот навык снижает цену входа и помогает проверить рабочую гипотезу действием."
    if "чернов" in title.lower():
        evidence = [
            "ты боишься оценки",
            "задача стала слишком дорогой",
            "хороший текст сейчас требует слишком много",
            "плохой черновик снижает цену входа",
        ]
        next_step = "Мы не пишем финальный материал. Мы ломаем заморозку."
    else:
        evidence = [
            skill.get("goal") or "цель — сделать вход проще",
            skill.get("explain") or skill.get("why_long") or "короткий шаг уменьшает сопротивление",
            "мы проверяем навык на практике, а не требуем идеального результата",
        ]
        next_step = "Мы не делаем финальный результат. Мы ломаем заморозку маленьким входом."
    u["last_explanation_context"] = json.dumps({
        "type": "skill",
        "title": title,
        "reason": reason,
        "evidence": [str(x) for x in evidence if x],
        "next_step": next_step,
    }, ensure_ascii=False)


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
    profile = await get_user_profile(u["user_id"], db_path)
    focus = determine_development_focus(profile)
    await update_user_profile(
        u["user_id"],
        {
            "current_development_focus": focus["code"],
            "development_focus_reason": focus["reason"],
            "today_recommended_skill": sid,
        },
        db_path,
        source="daily_focus",
    )

    u["day"] = day
    u["stage"] = "await_training_target"
    u["pending_skill_id"] = sid
    u["pending_skill_day"] = day
    set_skill_explanation_context(u, skill, sid)
    await save_user(u, db_path)

    # Утренний быстрый чек — только начиная со 2-го дня
    if day > 1:
        sleep = u.get("last_sleep") or "?"
        anxiety = u.get("last_anxiety") or "?"
        energy = u.get("last_energy") or "?"
        await m.answer(f"🕒 Быстрый чек\nСон: {sleep}\nТревога: {anxiety}\nЭнергия: {energy}")

    await m.answer(daily_profile_explanation(profile, sid, day))

    # Вопрос перед выдачей навыка
    question = (
        "Перед стартом: что ты прокрастинируешь сегодня?\n"
        "Одна задача/дело, на котором потренируемся.\n"
        "Напиши коротко, пришли голосовое или нажми 'Пропустить'."
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
        await m.answer(
            "Пауза = информация, не наказание. "
            "Сейчас видно, что нужен мягкий возврат: начнём с 3 минут и уточним модель."
        )
        u["return_count"] = int(u.get("return_count") or 0) + 1

    u["last_active"] = now
    await save_user(u, db_path)

async def start_day1(m: Message, u: Dict[str, Any], db_path: str):
    """День 1 - специальный скрипт"""
    name = u.get("name") or "друг"
    trainer_key = u.get("trainer_key") or "marsha"

    plan_ids = get_current_plan(u)
    if not plan_ids:
        plan_ids = build_plan(u.get("bucket") or "mixed")
        u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
        await save_user(u, db_path)

    sid = plan_ids[0]
    skill = SKILLS_DB.get(sid) or list(SKILLS_DB.values())[0]
    profile = await get_user_profile(u["user_id"], db_path)
    focus = determine_development_focus(profile)
    await update_user_profile(
        u["user_id"],
        {
            "current_development_focus": focus["code"],
            "development_focus_reason": focus["reason"],
            "today_recommended_skill": sid,
        },
        db_path,
        source="daily_focus",
    )
    set_skill_explanation_context(u, skill, sid)
    u["day"] = 1
    u["stage"] = "await_training_target"
    u["pending_skill_id"] = sid
    u["pending_skill_day"] = 1
    await save_user(u, db_path)

    msg = (
        f"🌅 {name}, День 1\n\n"
        "Коротко: я помогу выбрать один сегодняшний вход, дам навык на 60–120 секунд "
        "и после действия соберу первые данные в карту.\n\n"
        "Сейчас не грузим баллами, длинной картой и теорией. Сначала действие."
    )
    await m.answer(trainer_say(trainer_key, msg))
    await m.answer(
        "Что мешает сегодня?\n\n"
        "Напиши одну задачу или ситуацию, на которой потренируемся. "
        "Если хочешь без деталей — нажми «Пропустить».",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Пропустить")]],
            resize_keyboard=True,
        ),
    )

async def start_day_simple(m: Message, u: Dict[str, Any], day: int, db_path: str):
    """Универсальный скрипт для любого дня"""
    name = u.get("name") or "друг"
    trainer_key = u.get("trainer_key") or "marsha"

    plan_ids = get_current_plan(u)
    if not plan_ids:
        plan_ids = build_plan(u.get("bucket") or "mixed")
        u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
        await save_user(u, db_path)

    day = max(1, min(day, len(plan_ids)))
    sid = plan_ids[day - 1]
    skill = SKILLS_DB.get(sid) or list(SKILLS_DB.values())[0]
    profile = await get_user_profile(u["user_id"], db_path)
    focus = determine_development_focus(profile)
    await update_user_profile(
        u["user_id"],
        {
            "current_development_focus": focus["code"],
            "development_focus_reason": focus["reason"],
            "today_recommended_skill": sid,
        },
        db_path,
        source="daily_focus",
    )
    personal_context = daily_profile_explanation(profile, sid, day)
    set_skill_explanation_context(u, skill, sid)

    msg = (
        f"🌅 {name}, День {day}\n\n"
        f"🧩 Навык: {skill['name']}\n"
        f"🎯 Цель: {skill['goal']}\n"
        f"✅ Как: {skill_explain(trainer_key, skill)}\n\n"
        f"{personal_context}\n\n"
        "Считается попытка 60–120 сек."
    )
    button_count = keyboard_button_count(kb_training_main)
    await log_event(u["user_id"], "training", "keyboard_shown" if button_count <= MAX_KEYBOARD_BUTTONS else "keyboard_warning", {"keyboard": "training_main", "button_count": button_count}, db_path)
    await m.answer(trainer_say(trainer_key, msg), reply_markup=kb_training_main if button_count <= MAX_KEYBOARD_BUTTONS else None)

    u["day"] = day
    u["stage"] = "await_training_target"
    u["pending_skill_id"] = sid
    u["pending_skill_day"] = day
    await save_user(u, db_path)

    await m.answer(
        "Перед стартом: что ты прокрастинируешь сегодня?\n"
        "Одна задача/дело, на котором потренируемся.\n"
        "Напиши коротко, пришли голосовое или нажми 'Пропустить'.",
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

    # Stabilization should never be blocked by monetization.
    # Limits may apply only to repeated skill matching, not to safety guidance.

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
        "Ты — тренер самопомощи с опорой на CBT/DBT-навыки в формате короткого кризисного ответа.\n"
        "Контекст: клиент в остром перегрузе из-за прокрастинации. Нужна помощь 'здесь и сейчас'.\n"
        "Твоя задача: кратко поддержать, дать понятный шаг и выбрать навык из базы навыков.\n"
        "Это НЕ терапия, НЕ медицинское заключение и НЕ замена живой помощи. Нельзя обещать лечение. Без клинических терминов.\n"
        "Если в тексте есть риск вреда себе/другим, насилия или потери контроля — сначала советуй срочную живую помощь/экстренный номер, затем только безопасное заземление.\n"
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


def analysis_need_more_text(user_text: str = "") -> str:
    text = clamp_str(str(user_text or "").strip().replace("\n", " "), 160)
    intro = (
        f"Я беру в разбор то, что ты написал: “{text}”.\n\n"
        if text
        else "Я уже беру это в разбор.\n\n"
    )
    return (
        intro +
        "Хочу уточнить один узел, чтобы не гадать и не давать общий совет.\n\n"
        "Что чаще ломает вход?\n"
        "😵 Перегруз\n"
        "😬 Страх ошибки\n"
        "📱 Отвлечения\n"
        "🌀 Слишком много вариантов\n"
        "😶 Не вижу смысла\n\n"
        "После ответа я соберу короткий разбор: что происходит, почему это может ломаться и какой первый навык проверяем."
    )


def _infer_analysis_fields(user_text: str, bucket: str = "mixed") -> Dict[str, Any]:
    text = (user_text or "").lower()
    anxiety_mechanism = detect_anxiety_case_mechanism(user_text)
    if anxiety_mechanism == "anxiety":
        pattern = "тревога стала главным механизмом: мысли всё время возвращаются к рискам и последствиям"
        behavior = "проверку новостей, документов, денег или сценариев вместо ближайшего контролируемого шага"
        skills = ["сначала сузить тревогу", "проверка фактов", "один контролируемый шаг"]
    elif anxiety_mechanism == "fear_error":
        pattern = "страх ошибки делает выбор или действие слишком рискованным"
        behavior = "перепроверку, откладывание решения или поиск гарантии перед стартом"
        skills = ["проверка фактов", "плохой первый шаг", "вход без идеального плана"]
    elif anxiety_mechanism == "overload":
        pattern = "перегруз делает ситуацию не одним шагом, а большим комом"
        behavior = "попытку удержать всё в голове, перескакивание между срочным и заморозку"
        skills = ["один следующий шаг", "минимальный вход", "снижение шага"]
    elif anxiety_mechanism == "choice_freeze":
        pattern = "старт зависает на невозможности выбрать один безопасный вариант"
        behavior = "сравнение вариантов, подготовку и откладывание выбора"
        skills = ["уменьшение выбора", "один следующий шаг", "вход без полного плана"]
    elif anxiety_mechanism == "self_criticism":
        pattern = "самокритика усиливает тревогу и делает возврат дороже"
        behavior = "самообвинение, стыд и откладывание следующего шага"
        skills = ["факт вместо приговора", "возврат без самонаказания", "минимальный вход"]
    elif anxiety_mechanism == "quick_rewards":
        pattern = "тревога уводит в быстрые награды и проверку ленты"
        behavior = "новости, телефон, сообщения или вкладки, которые быстро снижают напряжение"
        skills = ["барьер перед быстрым стимулом", "одна вкладка", "короткое удержание внимания"]
    elif anxiety_mechanism == "meaning_loss":
        pattern = "потеря смысла делает действие внутренне спорным"
        behavior = "раздумья зачем это делать, сопротивление и откладывание"
        skills = ["маленькая цель", "связь с ближайшим результатом", "минимальный вход"]
    elif any(x in text for x in ("идеал", "идеаль", "ошиб", "плохо", "стыд", "оцен")):
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


ANXIETY_CASE_KEYWORDS = (
    "тревог", "страш", "страх послед", "последств", "новост", "документ",
    "деньг", "оплат", "счёт", "счет", "неопредел", "не могу выбрать",
    "невозможно выбрать", "выбрать", "а если", "вдруг",
)


def detect_anxiety_case_mechanism(user_text: str) -> str:
    """Classify anxious cases before choosing a skill.

    The goal is to avoid reducing money/documents/news/uncertainty cases to
    a generic "start the task" response before identifying the dominant
    mechanism.
    """
    text = (user_text or "").lower()
    if not any(marker in text for marker in ANXIETY_CASE_KEYWORDS):
        return ""
    if any(x in text for x in ("самокрит", "ругаю себя", "ненавижу себя", "тупой", "тупая", "опять всё", "опять все", "стыдно за себя")):
        return "self_criticism"
    if any(x in text for x in ("новост", "лента", "телеграм", "telegram", "сообщени", "скрол", "телефон", "проверяю")):
        return "quick_rewards"
    if any(x in text for x in ("зачем", "смысл", "бессмыс", "не вижу смысла", "потерял смысл", "потеряла смысл")):
        return "meaning_loss"
    if any(x in text for x in ("не могу выбрать", "невозможно выбрать", "много вариантов", "вариант", "решить что", "что выбрать")):
        return "choice_freeze"
    if any(x in text for x in ("ошиб", "неправильно", "последств", "штраф", "потеряю", "боюсь сделать", "страшно сделать")):
        return "fear_error"
    if any(x in text for x in ("перегруз", "всё сразу", "все сразу", "слишком много", "не вывожу", "хаос")):
        return "overload"
    if any(x in text for x in ("тревог", "паник", "а если", "вдруг", "страш")):
        return "anxiety"
    return ""




def detect_live_analysis_pattern(user_text: str) -> str:
    text = (user_text or "").lower()
    if any(x in text for x in ("ленив", "безволь", "нормальные люди", "со мной что-то не так")):
        return "shame_self_attack"
    if any(x in text for x in ("идеаль", "красиво", "позор", "плохо получится", "опубликую", "оценят", "оцен", "осуждени", "редактор", "слабый автор", "кажется тупым", "тупым", "выглядеть глупо", "глупо", "стыдно", "критика")) or ("боюсь" in text and any(x in text for x in ("плохо", "ошиб", "глуп", "оцен", "осуж", "письм", "стать", "текст", "результ"))):
        return "perfectionism_visibility_fear"
    anxiety_mechanism = detect_anxiety_case_mechanism(user_text)
    if anxiety_mechanism:
        return f"anxious_{anxiety_mechanism}"
    if any(x in text for x in ("залип", "ютуб", "youtube", "сообщения", "почта", "на минуту", "лента", "скрол")):
        return "attention_escape"
    if any(x in text for x in ("нет сил", "устал", "выгор", "не в форме", "не могу думать")):
        return "low_energy_overload"
    if any(x in text for x in ("рядом кто", "рядом с", "коворкинг", "созвон", "с коллегой легче", "кто-то рядом")):
        return "body_doubling_helpful"
    return "default_start_block"


def live_analysis_profile_patch(pattern: str) -> Dict[str, Any]:
    if pattern.startswith("anxious_"):
        return {"main_pattern": pattern, "avoidance_trigger": "anxious_case", "emotional_trigger": "anxiety"}
    return {
        "shame_self_attack": {"main_pattern": "shame_self_attack", "shame_signal": "self_attack_after_slip"},
        "perfectionism_visibility_fear": {"main_pattern": "perfectionism_visibility_fear", "avoidance_trigger": "страх оценки или неидеального результата"},
        "attention_escape": {"main_pattern": "attention_escape", "attention_pattern": "scroll_autopilot"},
        "low_energy_overload": {"main_pattern": "low_energy_overload", "energy_pattern": "low_start_energy"},
        "body_doubling_helpful": {"main_pattern": "body_doubling_helpful", "preferred_activation": "body_doubling", "body_doubling_signal": "body_doubling"},
    }.get(pattern, {"main_pattern": "start_avoidance"})


def extract_analysis_signals(user_text: str) -> Dict[str, Any]:
    """Extract compact user-specific signals for analysis copy.

    Store short labels only: enough for "меня поняли", not the full text.
    """
    text = (user_text or "").lower()
    signals: Dict[str, Any] = {"facts": []}

    def add_fact(label: str):
        if label and label not in signals["facts"] and len(signals["facts"]) < 8:
            signals["facts"].append(label)

    if any(x in text for x in ("письмо", "письма", "письм")):
        signals["task"] = "письмо"
    if any(x in text for x in ("статья", "статью", "текст", "материал")):
        signals["task"] = "статья / текст"
        add_fact("статью нужно сдать сегодня" if "сегодня" in text else "важный текст ждёт первого черновика")
    if any(x in text for x in ("завис", "застрял", "открываю документ", "закрываю")):
        add_fact("документ открывается, но текст не начинается")
    if any(x in text for x in ("паника", "панич", "страшно")):
        add_fact("перед стартом поднимается паника")
    if any(x in text for x in ("слабый автор", "боюсь осуждения", "осуждени", "редактор", "зачем мы вообще")):
        add_fact("страх оценки делает каждое предложение опасным")
    if "третий день" in text or "3 день" in text or "три дня" in text:
        signals["delay"] = "письмо стоит третий день"
        add_fact("письмо стоит третий день")
    elif "второй день" in text or "два дня" in text:
        signals["delay"] = "задача стоит не первый день"
        add_fact("задача стоит не первый день")
    if any(x in text for x in ("открываю ноутбук", "открыл ноутбук", "открываю документ", "открываю файл")):
        signals["starts_environment"] = "ноутбук / место задачи открывается"
        add_fact("ноутбук открывается")

    escapes: List[str] = []
    if "telegram" in text or "телеграм" in text:
        escapes.append("Telegram")
    if "почт" in text:
        escapes.append("почта")
    if "новост" in text:
        escapes.append("новости")
    if "youtube" in text or "ютуб" in text:
        escapes.append("YouTube")
    if escapes:
        signals["escapes"] = escapes
        add_fact("после задачи появляются " + " / ".join(escapes[:3]))

    if "злюсь на себя" in text or "злится на себя" in text or "ругаю себя" in text:
        signals["self_anger"] = "злость на себя"
        add_fact("появляется злость на себя")
    if "не такое уж слож" in text or "не очень слож" in text or "не слож" in text:
        signals["not_hard"] = "письмо не кажется очень сложным"
        add_fact("письмо не кажется очень сложным")
    if "боюсь написать плохо" in text or ("боюсь" in text and "плохо" in text):
        signals["fear_bad"] = "страх написать плохо"
        add_fact("страх написать плохо")
    if "выглядеть глупо" in text or "глупо" in text:
        signals["fear_visible"] = "страх выглядеть глупо"
        add_fact("страх выглядеть глупо")
    if "собраться с мыслями" in text or "сначала надо собраться" in text or "собраться" in text:
        signals["preparation"] = "сначала надо собраться с мыслями"
        add_fact("появляется идея сначала «собраться с мыслями»")
    if "страх ошибки" in text or "цена ошибки" in text:
        signals["fear_error"] = "страх ошибки"
        add_fact("вход ломает страх ошибки")
    if "написать плохо" in text and "страх написать плохо" not in signals:
        signals["fear_bad"] = "страх написать плохо"
        add_fact("страх написать плохо")
    if "перегруз" in text or "слишком большой" in text or "старт превращается в гору" in text:
        signals["overload"] = "перегруз перед стартом"
        add_fact("вход ломает перегруз")
        add_fact("задача ощущается слишком большой")
        add_fact("нужен первый физический шаг")
    if "быстрые награды" in text or "телефон" in text or "сообщения" in text or "лента" in text or "вкладки" in text:
        signals["attention_escape"] = "уход в быстрые награды"
        add_fact("внимание уходит в быстрые награды")
        add_fact("нужен барьер перед уходом")
        add_fact("нужно вернуть контроль внимания")
    if "много вариантов" in text or "уменьшение выбора" in text:
        signals["too_many_options"] = "слишком много вариантов"
        add_fact("вход ломает слишком много вариантов")
        add_fact("старт зависает на выборе")
        add_fact("нужно уменьшить выбор до одного шага")
    if "отсутствие смысла" in text or "не вижу смысла" in text:
        signals["meaning_gap"] = "не вижу смысла"
        add_fact("вход ломает отсутствие смысла")
        add_fact("нужна связь с ближайшим полезным результатом")
    if "сбой появляется до действия" in text:
        add_fact("сбой появляется до действия")
    if "маленький безопасный вход" in text or "маленький вход" in text:
        add_fact("нужно проверить маленький безопасный вход")
    return signals


def _facts_text(signals: Dict[str, Any], limit: int = 6, bullet: str = "—") -> str:
    facts = [str(x) for x in (signals or {}).get("facts", []) if x]
    return "\n".join(f"{bullet} {x}" for x in facts[:limit])


def _has_signal(signals: Dict[str, Any], key: str) -> bool:
    return bool((signals or {}).get(key))


def _escape_names(signals: Dict[str, Any]) -> str:
    escapes = signals.get("escapes") if isinstance(signals, dict) else []
    if isinstance(escapes, list) and escapes:
        return " / ".join(str(x) for x in escapes[:3])
    return "Telegram / почта / новости"


def _analysis_result_core_hypothesis(pattern: str) -> str:
    if pattern == "anxious_anxiety":
        return "главный механизм — тревога, а не отсутствие дисциплины"
    if pattern == "anxious_fear_error":
        return "главный механизм — страх ошибки или последствий"
    if pattern == "anxious_overload":
        return "главный механизм — перегруз"
    if pattern == "anxious_choice_freeze":
        return "главный механизм — невозможность выбрать"
    if pattern == "anxious_self_criticism":
        return "главный механизм — самокритика"
    if pattern == "anxious_quick_rewards":
        return "главный механизм — уход в быстрые награды"
    if pattern == "anxious_meaning_loss":
        return "главный механизм — потеря смысла"
    if pattern == "perfectionism_visibility_fear":
        return "страх ошибки или оценки"
    if pattern == "attention_escape":
        return "уход внимания в быстрые награды"
    if pattern == "shame_self_attack":
        return "самокритика после срыва"
    if pattern == "low_energy_overload":
        return "низкий ресурс и перегруз"
    if pattern == "body_doubling_helpful":
        return "внешний контакт снижает порог старта"
    return "вход в задачу становится слишком большим"


def _recommended_skill_for_pattern(pattern: str) -> Dict[str, str]:
    if pattern in {"anxious_anxiety", "anxious_overload"}:
        return {"recommended_core_skill": "anxiety_first", "recommended_variant": "body_before_task"}
    if pattern == "anxious_fear_error":
        return {"recommended_core_skill": "fact_check", "recommended_variant": "check_the_facts_light"}
    if pattern == "anxious_choice_freeze":
        return {"recommended_core_skill": "choice_reduction", "recommended_variant": "visible_next_step"}
    if pattern == "anxious_self_criticism":
        return {"recommended_core_skill": "shame_to_action", "recommended_variant": "check_the_facts_light"}
    if pattern == "anxious_quick_rewards":
        return {"recommended_core_skill": "attention_container", "recommended_variant": "phone_far_3min"}
    if pattern == "anxious_meaning_loss":
        return {"recommended_core_skill": "meaning_bridge", "recommended_variant": "minimum_viable_day"}
    if pattern == "perfectionism_visibility_fear":
        return {"recommended_core_skill": "bad_draft_entry", "recommended_variant": "bad_first_step"}
    if pattern == "attention_escape":
        return {"recommended_core_skill": "attention_container", "recommended_variant": "phone_far_3min"}
    if pattern == "shame_self_attack":
        return {"recommended_core_skill": "shame_to_action", "recommended_variant": "check_the_facts_light"}
    if pattern == "low_energy_overload":
        return {"recommended_core_skill": "energy_first", "recommended_variant": "minimum_viable_day"}
    return {"recommended_core_skill": "entry_small_step", "recommended_variant": "open_only"}


def _hypothesis_label(core_hypothesis: str) -> str:
    return f"Пока есть гипотеза, что {core_hypothesis}"


def _analysis_facts(evidence: List[str], limit: int = 6) -> str:
    clean = [str(x).strip() for x in evidence if str(x).strip()]
    return "\n".join(f"— {x}" for x in clean[:limit]) or "— данных пока мало"


def _skill_display_name(skill_id: str) -> str:
    skill = SKILLS_DB.get(str(skill_id or ""), {})
    return str(skill.get("name") or skill_id or "маленький вход")


def _skill_reason(skill_id: str, pattern: str = "") -> str:
    skill = SKILLS_DB.get(str(skill_id or ""), {})
    if skill.get("why_short"):
        return str(skill.get("why_short"))
    if pattern == "perfectionism_visibility_fear":
        return "он снижает цену ошибки и помогает начать без идеального результата"
    if pattern == "attention_escape":
        return "он создаёт короткую паузу перед быстрым отвлечением"
    if pattern == "low_energy_overload":
        return "он уменьшает требование к себе до уровня текущего ресурса"
    if pattern == "shame_self_attack":
        return "он переводит самокритику в проверяемый следующий шаг"
    return "он проверяет вход в задачу без давления на результат"


def _analysis_line_or_unknown(items: List[str], fallback: str = "данных пока нет") -> str:
    clean = [str(x).strip() for x in items if str(x).strip()]
    return "; ".join(clean[:2]) if clean else fallback


def _detailed_section_lines(pattern: str, signals: Dict[str, Any], evidence: List[str], core_hypothesis: str, recommended_variant: str, skills_focus: List[str]) -> List[str]:
    facts_text = _analysis_line_or_unknown(evidence, "данных пока мало — не добавляю факты от себя")
    trigger = "момент перед стартом задачи"
    if signals.get("fear_bad") or signals.get("fear_visible") or pattern == "perfectionism_visibility_fear":
        trigger = "риск сделать плохо или быть оценённым"
    elif signals.get("attention_escape") or pattern == "attention_escape":
        trigger = "быстрый доступ к отвлечениям"
    elif signals.get("overload") or pattern == "low_energy_overload":
        trigger = "перегруз до первого действия"
    elif signals.get("too_many_options"):
        trigger = "слишком много вариантов перед стартом"

    thoughts = []
    if signals.get("fear_bad"):
        thoughts.append(str(signals.get("fear_bad")))
    if signals.get("fear_visible"):
        thoughts.append(str(signals.get("fear_visible")))
    if signals.get("meaning_gap"):
        thoughts.append(str(signals.get("meaning_gap")))
    if not thoughts and pattern == "perfectionism_visibility_fear":
        thoughts.append("пока похоже: результат может быть оценён как плохой")

    emotions = []
    evidence_joined = " ".join(evidence).lower()
    if "паник" in evidence_joined:
        emotions.append("паника")
    if signals.get("self_anger"):
        emotions.append(str(signals.get("self_anger")))
    if signals.get("fear_bad") or signals.get("fear_visible") or pattern == "perfectionism_visibility_fear":
        emotions.append("страх оценки")

    body = "данных пока нет"
    if "паник" in evidence_joined:
        body = "паника может ощущаться телесно, но конкретные телесные признаки ты пока не описал(а)"
    elif signals.get("overload"):
        body = "пока видно только ощущение перегруза; телесные признаки ещё надо уточнить"

    avoidance = str(signals.get("escapes") and " / ".join(signals.get("escapes")[:3]) or "")
    if not avoidance:
        if signals.get("preparation"):
            avoidance = str(signals.get("preparation"))
        elif pattern == "perfectionism_visibility_fear":
            avoidance = "откладывание первого чернового следа"
        else:
            avoidance = "откладывание или подготовка вместо первого действия"

    immediate_gain = "сразу становится меньше напряжения или риска ошибиться"
    later_cost = "задача остаётся стоять, а давление растёт"
    if signals.get("escapes"):
        immediate_gain = "быстрые стимулы дают облегчение прямо сейчас"
        later_cost = "время уходит, а возвращаться к задаче становится тяжелее"

    cycle = f"триггер → {core_hypothesis} → {avoidance} → короткое облегчение → больше давления перед следующим стартом"
    resources = []
    if signals.get("starts_environment"):
        resources.append(str(signals.get("starts_environment")))
    if signals.get("not_hard"):
        resources.append(str(signals.get("not_hard")))
    resources.append("ты уже можешь описать момент, где ломается старт")

    already_helps = []
    useful = signals.get("useful_signal")
    if useful:
        already_helps.append(str(useful))

    next_skills = [x for x in skills_focus[1:4] if x]
    if not next_skills:
        next_skills = ["уменьшение шага", "возврат после выпадения", "проверка внешней опоры"]

    skill_name = _skill_display_name(recommended_variant)
    reason = _skill_reason(recommended_variant, pattern)
    return [
        f"1. Что произошло.\n— {facts_text}",
        f"2. Что стало триггером.\n— пока похоже: {trigger}",
        f"3. Какие мысли или оценки включаются.\n— {_analysis_line_or_unknown(thoughts)}",
        f"4. Какие эмоции возникают.\n— {_analysis_line_or_unknown(emotions)}",
        f"5. Что происходит в теле.\n— {body}",
        f"6. Как человек начинает избегать.\n— {avoidance}",
        f"7. Что даёт избегание прямо сейчас.\n— {immediate_gain}",
        f"8. Какую цену оно создаёт потом.\n— {later_cost}",
        f"9. Повторяющийся цикл прокрастинации.\n— {cycle}",
        f"10. Ресурсы человека.\n— {_analysis_line_or_unknown(resources)}",
        f"11. Что уже помогает.\n— {_analysis_line_or_unknown(already_helps, 'данных пока мало — проверим по действиям')}",
        f"12. Какие гипотезы ещё надо проверить.\n— помогает ли выбранный вход; что сильнее: страх оценки, перегруз, отвлечение или низкий ресурс",
        f"13. Почему выбран текущий навык.\n— {skill_name}: {reason}",
        f"14. Какие навыки могут быть следующими.\n— {_analysis_line_or_unknown(next_skills)}",
    ]


def _primary_analysis_scripts(pattern: str, evidence: List[str], core_hypothesis: str) -> Dict[str, str]:
    """Render analysis without inventing unreported life details.

    The copy may use only extracted user facts in ``evidence``. Anything beyond
    those facts is framed explicitly as a hypothesis/test, never as a fact.
    """
    facts = _analysis_facts(evidence, 5)
    hypothesis = _hypothesis_label(core_hypothesis)
    base = {
        "skinny": (
            f"Что вижу по твоим словам.\n\n{facts}\n\n"
            f"{hypothesis}.\n\n"
            "Проверим это маленьким действием. Если не подойдёт — не считаем это провалом, а меняем гипотезу."
        ),
        "beck": (
            f"Коротко, на что опираюсь.\n\n{facts}\n\n"
            f"Моя рабочая гипотеза: {core_hypothesis}.\n\n"
            "Это не вывод о тебе. Сегодня проверяем, помогает ли сузить вход до одного безопасного действия."
        ),
        "marsha": (
            f"Я беру только то, что ты уже описал(а).\n\n{facts}\n\n"
            f"Пока осторожная гипотеза: {core_hypothesis}.\n\n"
            "Дальше проверим мягко: один маленький шаг и честная обратная связь, стало ли легче."
        ),
    }
    return base


def _detailed_analysis_scripts(pattern: str, evidence: List[str], core_hypothesis: str, signals: Optional[Dict[str, Any]] = None, recommended_variant: str = "", skills_focus: Optional[List[str]] = None) -> Dict[str, str]:
    """Detailed product-grade analysis grounded in extracted user facts."""
    signals = signals or {}
    skills_focus = skills_focus or []
    facts = [str(x).strip() for x in evidence if str(x).strip()]
    facts_block = "\n".join(f"— {x}" for x in facts[:7]) or "— пока данных мало: я не буду делать вид, что всё понял"

    if signals.get("fear_bad") or signals.get("fear_visible") or pattern in {"perfectionism_visibility_fear", "anxious_fear_error"}:
        start_thought = "«надо сделать правильно / нельзя ошибиться / это могут оценить»"
        avoid = "черновик откладывается или вход заменяется подготовкой"
        why = "Я пока рассматриваю страх ошибки или оценки, потому что в данных есть сигнал, что цена плохого результата ощущается высокой."
        unknown = "Но пока неясно, что сильнее: страх сделать плохо, перегруз от масштаба задачи или тревога из-за неопределённости."
    elif signals.get("attention_escape") or pattern in {"attention_escape", "anxious_quick_rewards"}:
        start_thought = "«нужно быстро сбросить напряжение / проверить что-то ещё»"
        avoid = f"внимание уходит в {_escape_names(signals)}"
        why = "Я пока рассматриваю уход в быстрые стимулы, потому что внимание описано как уходящее туда, где легче получить короткое облегчение."
        unknown = "Но пока неясно, телефон здесь главный источник облегчения или только самый доступный способ уйти от перегруза."
    elif signals.get("overload") or signals.get("too_many_options") or pattern in {"low_energy_overload", "anxious_overload", "anxious_choice_freeze"}:
        start_thought = "«слишком много всего / непонятно, с чего начать»"
        avoid = "выбор, планирование или зависание заменяют первый физический шаг"
        why = "Я пока рассматриваю перегруз или слишком большой вход, потому что задача выглядит не как один шаг, а как большой ком."
        unknown = "Но пока неясно, что сильнее: масштаб задачи, срочность, усталость или страх выбрать неправильно."
    else:
        start_thought = "«надо начать, но вход выглядит дорогим»"
        avoid = "появляется пауза, подготовка или откладывание"
        why = "Я пока рассматриваю слишком дорогой вход в задачу, потому что данных хватает только на осторожную рабочую модель."
        unknown = "Пока неясно, что является главной причиной: страх ошибки, перегруз, тревога, низкий ресурс или быстрые отвлечения."

    skill_name = _skill_display_name(recommended_variant)
    known_lines = [
        "проблема, похоже, возникает не только в самой работе, а в моменте входа",
        "маленький шаг может быть полезнее требования «соберись»",
    ]
    if signals.get("escapes") or signals.get("attention_escape"):
        known_lines.insert(1, "отвлечение может давать краткое облегчение")
    if len(facts) < 3:
        known_lines.insert(0, "пока данных мало: я не могу уверенно сказать, что именно является главной причиной")
    known_block = "\n".join(f"— {x}" for x in known_lines[:4])

    base = (
        "## 🧭 Почему я сейчас думаю именно так\n\n"
        "### Что ты прямо описал\n\n"
        f"{facts_block}\n\n"
        "### Как может выглядеть цикл сейчас\n\n"
        "Важная задача\n"
        f"→ мысль: {start_thought}\n"
        "→ тревога, напряжение или перегруз\n"
        f"→ {avoid}\n"
        "→ короткое облегчение\n"
        "→ задача остаётся\n"
        "→ вина, злость на себя или ещё большее напряжение\n"
        "→ вход в задачу становится тяжелее.\n\n"
        "Это пока не диагноз и не окончательный вывод. Это рабочий цикл, который мы проверяем по твоим действиям.\n\n"
        "### Почему выбрана эта гипотеза\n\n"
        f"{why}\n\n{unknown}\n\n"
        "### Что уже известно\n\n"
        f"Уже видно:\n{known_block}\n\n"
        "### Что проверим первым\n\n"
        "Первый тест: уменьшить стоимость входа.\n\n"
        "Мы не проверяем, умеешь ли ты работать.\n"
        "Мы проверяем, станет ли легче начать, если от тебя требуется только один физический шаг.\n\n"
        f"Предварительный навык для проверки: {skill_name}.\n\n"
        "Чтобы не гадать, я могу задать 3 коротких вопроса и точнее подобрать первый навык."
    )
    return {
        "marsha": "Похоже, твоя психика сейчас не ленится, а пытается быстро убрать напряжение. Давай без давления поймём, что именно делает вход таким тяжёлым.\n\n" + base,
        "skinny": "Пока данных мало. Не гадаем. За три вопроса выясним, где именно ломается вход, и выберем шаг.\n\n" + base,
        "beck": "Сейчас у нас есть несколько конкурирующих гипотез: страх оценки, перегруз и поиск быстрого снижения тревоги. Ответы помогут различить их.\n\n" + base,
    }

def _working_map_scripts(pattern: str, core_hypothesis: str) -> Dict[str, str]:
    checks = [
        "какой минимальный шаг помогает начать",
        "снижает ли напряжение маленький шаг",
        "как быстро получается вернуться после срыва",
        "какие факты повторяются в следующие дни",
    ]
    if pattern == "perfectionism_visibility_fear":
        checks.insert(0, "помогает ли снизить цену ошибки")
    elif pattern == "attention_escape":
        checks.insert(0, "помогает ли короткий барьер перед отвлечением")
    elif pattern == "low_energy_overload":
        checks.insert(0, "помогает ли шаг под текущий уровень энергии")
    checks_text = "\n".join(f"— {item}" for item in checks[:5])
    base = (
        "🗺 Рабочая карта\n\n"
        "Главная гипотеза:\n\n"
        f"🔹 {core_hypothesis}\n\n"
        "Пока это не вывод и не факт о тебе. Несколько дней проверяем действиями:\n\n"
        f"{checks_text}\n\n"
        "На 3 день карта станет точнее по тому, что реально сработало."
    )
    return {"skinny": base, "beck": base, "marsha": base}


def build_analysis_result(comp: Dict[str, Any], user_text: str = "") -> Dict[str, Any]:
    data = dict(comp or {})
    signals = data.get("analysis_signals") if isinstance(data.get("analysis_signals"), dict) else extract_analysis_signals(user_text)
    evidence = [str(x) for x in signals.get("facts", []) if x]
    detected_pattern = detect_live_analysis_pattern(user_text)
    stored_pattern = str(data.get("live_pattern") or "")
    pattern = stored_pattern if stored_pattern and stored_pattern != "default_start_block" else (detected_pattern or "default_start_block")
    core_hypothesis = _analysis_result_core_hypothesis(pattern)
    secondary = [
        "помогает ли плохой черновик" if pattern == "perfectionism_visibility_fear" else "какой минимальный шаг помогает начать",
        "снижает ли напряжение маленький шаг",
        "как быстро получается вернуться после срыва",
        "усиливает ли самокритика откладывание",
        "помогает ли внешний контакт / body doubling",
    ]
    rec = _recommended_skill_for_pattern(pattern)
    recommended_variant = rec.get("recommended_variant", "open_only")
    skill_name = _skill_display_name(recommended_variant)
    skill_reason = _skill_reason(recommended_variant, pattern)
    skills_focus = data.get("skills_focus") if isinstance(data.get("skills_focus"), list) else []
    if data.get("useful_signal") and isinstance(signals, dict):
        signals = {**signals, "useful_signal": data.get("useful_signal")}
    return {
        "pattern": pattern,
        "evidence_signals": evidence,
        "core_hypothesis": core_hypothesis,
        "secondary_hypotheses": secondary,
        **rec,
        "recommended_skill_name": skill_name,
        "recommended_skill_reason": skill_reason,
        "first_check": skill_name,
        "primary_analysis_by_trainer": _primary_analysis_scripts(pattern, evidence, core_hypothesis),
        "detailed_analysis_by_trainer": _detailed_analysis_scripts(pattern, evidence, core_hypothesis, signals, recommended_variant, skills_focus),
        "working_map_by_trainer": _working_map_scripts(pattern, core_hypothesis),
    }


def render_analysis_details_by_trainer(comp: Dict[str, Any], trainer_key: str = "marsha") -> str:
    """Expand only the current analysis with facts and hypotheses separated."""
    analysis_result = comp.get("analysis_result") if isinstance(comp.get("analysis_result"), dict) else {}
    details_by_trainer = analysis_result.get("detailed_analysis_by_trainer") if isinstance(analysis_result.get("detailed_analysis_by_trainer"), dict) else {}
    scripted = details_by_trainer.get(trainer_key) or details_by_trainer.get("marsha")
    if scripted:
        return str(scripted)

    pattern = str(comp.get("live_pattern") or "default_start_block")
    core_hypothesis = _analysis_result_core_hypothesis(pattern)
    signals = comp.get("analysis_signals") if isinstance(comp.get("analysis_signals"), dict) else {}
    evidence = [str(x) for x in signals.get("facts", []) if x]
    if not evidence:
        return (
            "## 🧭 Почему я сейчас думаю именно так\n\n"
            "Пока у меня мало данных, поэтому не буду делать вид, что всё понял. "
            "Давай уточним 3 короткими вопросами — и я соберу первую рабочую модель.\n\n"
            "Это пока не диагноз и не окончательный вывод. Это рабочий цикл, который мы проверяем по твоим действиям."
        )
    recommended_variant = str((comp.get("analysis_result") or {}).get("recommended_variant") or _recommended_skill_for_pattern(pattern).get("recommended_variant") or "open_only")
    skills_focus = comp.get("skills_focus") if isinstance(comp.get("skills_focus"), list) else []
    details = _detailed_analysis_scripts(pattern, evidence, core_hypothesis, signals, recommended_variant, skills_focus)
    return details.get(trainer_key) or details["marsha"]


def render_analysis_by_trainer(pattern: str, trainer_key: str, data: Optional[Dict[str, Any]] = None) -> str:
    trainer_key = trainer_key if trainer_key in {"skinny", "beck", "marsha"} else "marsha"
    data = data or {}
    signals = data.get("analysis_signals") if isinstance(data.get("analysis_signals"), dict) else {}
    evidence = [str(x) for x in signals.get("facts", []) if x]
    if not evidence:
        return (
            "Фактов пока мало.\n\n"
            "Я не буду достраивать историю за тебя. Нужен один конкретный эпизод: что было перед стартом, куда ушло внимание или чего стало страшно."
        )
    core_hypothesis = _analysis_result_core_hypothesis(pattern)
    return _primary_analysis_scripts(pattern, evidence, core_hypothesis).get(trainer_key) or _primary_analysis_scripts(pattern, evidence, core_hypothesis)["marsha"]


def safe_analysis_memory(user_text: str, comp: Optional[Dict[str, Any]] = None, *, needs_more: bool = False) -> Dict[str, Any]:
    """Return non-verbatim analysis memory for persistence.

    Do not store the user's full text/transcript/confession. Keep only short
    categories and inferred behavioral signals that are safe for profile rebuilds.
    """
    comp = comp or {}
    bucket = str(comp.get("bucket") or "mixed")
    inferred = _infer_analysis_fields(user_text or "", bucket)
    live_pattern = detect_live_analysis_pattern(user_text or "")
    memory = {
        "input_len": len(user_text or ""),
        "live_pattern": live_pattern,
        "analysis_signals": extract_analysis_signals(user_text or ""),
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
    normalized["live_pattern"] = comp.get("live_pattern") or detect_live_analysis_pattern(user_text)
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
                {"role": "user", "content": f"""Проанализируй следующее описание и верни JSON с этой структурой.

Важно для тревожных кейсов: если человек пишет про постоянную тревогу, страх последствий,
проверку новостей, документы, деньги, неопределённость или невозможность выбрать,
сначала определи главный механизм: тревога, страх ошибки, перегруз, невозможность выбора,
самокритика, уход в быстрые награды или потеря смысла. Только после этого выбирай selected_skill.
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


def format_comprehensive_analysis(comp: Dict[str, Any], quick: Optional[Dict[str, Any]] = None, trainer_key: Optional[str] = None) -> str:
    """Собрать короткий живой разбор без generic GPT-фраз."""
    quick = quick or {}
    key = trainer_key or comp.get("trainer_key") or quick.get("trainer_key") or "marsha"
    analysis_result = comp.get("analysis_result") if isinstance(comp.get("analysis_result"), dict) else {}
    primary_by_trainer = analysis_result.get("primary_analysis_by_trainer") if isinstance(analysis_result.get("primary_analysis_by_trainer"), dict) else {}
    scripted = primary_by_trainer.get(str(key)) or primary_by_trainer.get("marsha")
    if scripted:
        return str(scripted)
    raw_hint = comp.get("user_text") or quick.get("user_text") or ""
    normalized = normalize_analysis(comp, raw_hint, quick)
    pattern = normalized.get("live_pattern") or comp.get("live_pattern") or detect_live_analysis_pattern(raw_hint)
    return render_analysis_by_trainer(str(pattern), str(key), normalized)

async def run_analysis(m: Message, u: Dict[str, Any], user_text: str, db_path: str, sheets_webhook: str = "", client=None, model: str = "gpt-4o-mini"):
    """Запустить анализ"""
    from texts import kb_analysis_confirm, kb_analysis_need_more, preliminary_hypothesis_note, preliminary_diagnosis_conclusion_text

    if analysis_needs_more_input(user_text):
        u["analysis_json"] = json.dumps(safe_analysis_memory(user_text, {"bucket": u.get("bucket") or "mixed"}, needs_more=True), ensure_ascii=False)
        u["stage"] = "analysis_need_more"
        await save_user(u, db_path)
        await log_event(u["user_id"], "analysis", "analysis_needs_more_input", {"len": len(user_text or "")}, db_path, sheets_webhook)
        button_count = keyboard_button_count(kb_analysis_need_more)
        await log_event(
            u["user_id"],
            "analysis",
            "keyboard_shown" if button_count <= MAX_KEYBOARD_BUTTONS else "keyboard_warning",
            {"keyboard": "analysis_need_more", "button_count": button_count},
            db_path,
            sheets_webhook,
        )
        await m.answer(analysis_need_more_text(user_text), reply_markup=kb_analysis_need_more if button_count <= MAX_KEYBOARD_BUTTONS else None)
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
    comp["trainer_key"] = u.get("trainer_key", "marsha")
    comp_to_store = dict(comp)
    comp_to_store.pop("user_text", None)
    comp_to_store.update(safe_analysis_memory(user_text, comp_to_store))
    analysis_result = build_analysis_result(comp_to_store, user_text)
    if len(analysis_result.get("evidence_signals") or []) < 3:
        u["analysis_json"] = json.dumps(comp_to_store, ensure_ascii=False)
        u["stage"] = "analysis_need_more"
        await save_user(u, db_path)
        await log_event(u["user_id"], "analysis", "analysis_evidence_too_low", {"signals": len(analysis_result.get("evidence_signals") or [])}, db_path, sheets_webhook)
        await m.answer(analysis_need_more_text(user_text), reply_markup=kb_analysis_need_more)
        return
    comp_to_store["analysis_result"] = analysis_result
    u["analysis_json"] = json.dumps(comp_to_store, ensure_ascii=False)

    # build plan (28 days)
    plan_ids = build_28_day_plan(bucket)
    recommended_variant = analysis_result.get("recommended_variant")
    if recommended_variant in SKILLS_DB:
        plan_ids[0] = recommended_variant
    if (comp.get("analysis_fallback") or r.get("analysis_fallback")) and "open_only" in SKILLS_DB and recommended_variant not in SKILLS_DB:
        plan_ids[0] = "open_only"
    u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
    u["day"] = 1

    # set stage to confirm comprehensive analysis and persist
    u["stage"] = "confirm_analysis"
    await save_user(u, db_path)

    # Log that analysis was shown
    await log_event(u["user_id"], "analysis", "diagnosis_completed", {"bucket": u.get("bucket")}, db_path, sheets_webhook)
    diagnosis_patch = diagnosis_user_profile_patch(comp_to_store)
    live_patch = live_analysis_profile_patch(str(comp_to_store.get("live_pattern") or ""))
    profile_patch = {**diagnosis_patch, **live_patch}
    if profile_patch:
        updated_profile = await update_user_profile(u["user_id"], profile_patch, db_path, source="initial_diagnosis")
        u["profile_json"] = updated_profile
        await log_event(u["user_id"], "analysis", "profile_signal_detected", {"source": "initial_diagnosis", **profile_patch}, db_path, sheets_webhook)
        await log_event(u["user_id"], "analysis", "profile_map_updated", {"source": "initial_diagnosis", **profile_patch}, db_path, sheets_webhook)
    await log_event(u["user_id"], "analysis", "analysis_shown", {"bucket": u.get("bucket")}, db_path, sheets_webhook)

    # Show only the short conclusion; the full mechanism is behind «📚 Подробнее».
    preliminary_conclusion = preliminary_diagnosis_conclusion_text(
        comp_to_store.get("specific_pattern") or comp_to_store.get("live_pattern") or "",
        comp_to_store.get("useful_signal") or "",
        comp_to_store.get("skills_focus") if isinstance(comp_to_store.get("skills_focus"), list) else [],
        (analysis_result.get("first_check") or analysis_result.get("recommended_skill_name") or ""),
        (analysis_result.get("recommended_skill_reason") or ""),
    )
    msg = f"{preliminary_conclusion}\n\nЭто похоже на тебя?"

    button_count = keyboard_button_count(kb_analysis_confirm)
    await log_event(
        u["user_id"],
        "analysis",
        "keyboard_shown" if button_count <= MAX_KEYBOARD_BUTTONS else "keyboard_warning",
        {"keyboard": "analysis", "button_count": button_count},
        db_path,
        sheets_webhook,
    )
    await m.answer(msg, reply_markup=kb_analysis_confirm if button_count <= MAX_KEYBOARD_BUTTONS else None)

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

    profile = await get_user_profile(uid, db_path)
    msg = (
        f"📊 {u.get('name') or 'друг'}, итоги недели:\n\n"
        f"✅ попытки: {stats.get('done',0)}\n"
        f"↩️ возвраты: {stats.get('return',0)}\n"
        f"🆘 кризисы: {stats.get('crisis_message',0)}\n\n"
        "🏆 Достижения развития:\n"
        f"{progress_achievements_text(u, profile, stats)}\n\n"
        f"{growth_history_text(u, profile, stats)}\n\n"
        "Главное:\n"
        "ты видишь, как меняешься.\n"
        "Это не игра — это история роста."
    )

    await m.answer(msg)

async def send_progress_report(m: Message, u: dict, db_path: str):
    """Отправить отчет о прогрессе"""
    from texts import gamify_status_line, progress_achievements_text, growth_history_text
    
    uid = u["user_id"]
    since = time.time() - 7 * 24 * 3600
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT event, COUNT(*) FROM events WHERE user_id=? AND ts>=? GROUP BY event",
            (uid, since)
        )
        rows = await cur.fetchall()

    counts = {e: c for e, c in rows}
    metrics = await get_action_metrics(uid, db_path, day_id=str(u.get("current_day_id") or ""))
    period = metrics.get("period", {})
    done = int(period.get("micro_approaches") or 0)
    slips = int(period.get("slips") or 0)
    ret = int(period.get("returns_after_slip") or 0)
    downscales = int(period.get("step_reductions") or 0)
    crisis = counts.get("crisis_message", 0)

    plan = get_current_plan(u)
    day = int(u.get("day") or 1)
    next_skill = SKILLS_DB[plan[min(day, len(plan)-1)]]["name"] if plan else "—"

    profile = await get_user_profile(uid, db_path)
    avatar_text = render_development_avatar(profile)

    achievements_text = progress_achievements_text(u, profile, counts)
    history_text = growth_history_text(u, profile, counts)
    mirror_text = render_development_mirror_report(profile, period_days=7)

    msg = (
        "📊 Твои отметки за период:\n"
        f"— микро-подходов: {done}\n"
        f"— отмеченных залипаний: {slips}\n"
        f"— возвратов после залипания: {ret}\n"
        f"— упрощений шага: {downscales}\n"
        f"🆘 кризис-обращений: {crisis}\n\n"
        "Это не оценка твоей продуктивности. Это отметки о том, как ты пробовал(а) входить в задачу.\n\n"
        f"{avatar_text}\n\n"
        "🏆 Достижения развития:\n"
        f"{achievements_text}\n\n"
        f"{gamify_status_line(u)}\n\n"
        f"{history_text}\n\n"
        f"{mirror_text}\n\n"
        "Главное: ты видишь, как меняешься — это не игра и не очки.\n\n"
        f"➡️ Следующий навык по плану: {next_skill}"
    )
    await m.answer(msg)
    await log_event(uid, u.get("stage",""), "progress_view", {"done": done, "return": ret, "crisis": crisis}, db_path)
