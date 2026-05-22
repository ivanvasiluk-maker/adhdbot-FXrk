# ============================================================
# TEXTS.PY — Все текстовые константы и клавиатуры
# ============================================================

import json
import math
import random
from typing import Dict, Any, Optional, List
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from skills_texts import SKILLS_TEXTS

# ============================================================
# TRAINERS (стили)
# ============================================================

TRAINERS = {
    "skinny": {
        "name": "Скинни",
        "tone": "жёсткий, прямой",
        "emoji": "🐈‍⬛",
        "short": "Без воды. Сделал — молодец. Не сделал — вернись.",
    },
    "marsha": {
        "name": "Марша",
        "tone": "мягкий, поддерживающий",
        "emoji": "🐈",
        "short": "Мягко возвращаемся. Без наказания. Навык важнее эмоций.",
    },
    "beck": {
        "name": "Бек",
        "tone": "аналитичный, структурный",
        "emoji": "🐈‍🦁",
        "short": "Тренируем конкретную функцию. Измеряем фактами.",
    },
}

# ============================================================
# 🐈 TRAINER PRESENTATION BLOCK
# ============================================================

TRAINER_INTRO_TEXT = {
    "marsha": {
        "who": "🤍 Марша — мягкий тренер",
        "for_whom": (
            "Подходит, если:\n"
            "• много самокритики\n"
            "• тревога мешает начинать\n"
            "• давление только усиливает срыв\n"
        ),
        "intro": (
            "Привет. Я Марша.\n\n"
            "Я не давлю.\n"
            "Я помогаю возвращаться без стыда.\n\n"
            "Мы будем усиливать устойчивость мягко,\n"
            "но системно.\n\n"
            "Даже если ты сорвёшься — я не исчезну."
        )
    },
    "skinny": {
        "who": "🐈‍⬛ Скинни — жёсткий тренер",
        "for_whom": (
            "Подходит, если:\n"
            "• нужен толчок\n"
            "• устал от разговоров\n"
            "• хочешь структуру и результат\n"
        ),
        "intro": (
            "Привет. Я Скинни.\n\n"
            "Я не обсуждаю бесконечно.\n"
            "Мы тренируем навык через действие.\n\n"
            "Минимум слов. Максимум выполнения.\n\n"
            "Сорвёшься — поднимем и продолжим.\n"
            "Но с дистанции не уйдёшь."
        )
    },
    "beck": {
        "who": "🧠 Бек — аналитический тренер",
        "for_whom": (
            "Подходит, если:\n"
            "• важно понимать, почему это работает\n"
            "• нужна логика и структура\n"
            "• хочешь видеть систему\n"
        ),
        "intro": (
            "Привет. Я Бек.\n\n"
            "Мы будем работать через модель.\n"
            "Я объясню, что происходит с вниманием,\n"
            "и какие функции мы тренируем.\n\n"
            "Если метод не подойдёт — адаптируем.\n"
            "Решение существует."
        )
    }
}

async def send_trainer_introduction(m, u):
    trainer_key = u.get("trainer_key")
    if trainer_key not in TRAINER_INTRO_TEXT:
        return
    data = TRAINER_INTRO_TEXT[trainer_key]
    text = (
        f"{data['who']}\n\n"
        f"{data['for_whom']}\n"
        f"{data['intro']}\n\n"
        "Если стиль откликается — идём дальше."
    )
    await m.answer(text)

def trainer_say(trainer_key: str, text: str) -> str:
    t = TRAINERS.get(trainer_key, TRAINERS["marsha"])
    return f"{t['emoji']} *{t['name']}*: {text}"


def keyboard_button_count(reply_markup) -> int:
    """Count buttons in reply/inline keyboards for overload guardrails."""
    if not reply_markup:
        return 0
    rows = getattr(reply_markup, "keyboard", None) or getattr(reply_markup, "inline_keyboard", None) or []
    return sum(len(row) for row in rows)

# Crisis limit for non-paid users
CRISIS_LIMIT = 3

# Praise phrases per trainer
PRAISE = {
    "skinny": "Сделал. Факт есть. Это тренировка.",
    "marsha": "Это важно. Ты не бросил(а).",
    "beck": "Есть действие → есть обучение."
}

# ============================================================
# TRAINER PRESENTATION & SELECTION
# ============================================================

TRAINER_INTRO_SCREEN = (
    "Перед тем как мы начнём,\n"
    "ты выберешь тренера.\n\n"
    "Это не просто стиль текста.\n"
    "Это то, КАК с тобой будут работать,\n"
    "поддерживать и вести дальше.\n\n"
    "Можно выбрать любого —\n"
    "если не подойдёт, мы сможем сменить."
)

# 🤍 Марша — поддержка и безопасность
TRAINER_MARSHA_DESC = (
    "🤍 Марша — мягкая и поддерживающая.\n\n"
    "Подойдёт, если ты часто винишь себя,\n"
    "быстро выгораешь или боишься не справиться.\n\n"
    "Она помогает возвращаться без стыда\n"
    "и не бросать после срывов."
)

# 🧱 Скинни — структура и давление на результат
TRAINER_SKINNY_DESC = (
    "🧱 Скинни — прямой и требовательный.\n\n"
    "Подойдёт, если нужен чёткий маршрут,\n"
    "жёсткие рамки и меньше разговоров.\n\n"
    "Он не давит на самооценку,\n"
    "он давит на выполнение."
)

# 🧠 Бек — объяснение и логика
TRAINER_BECK_DESC = (
    "🧠 Бек — аналитичный и спокойный.\n\n"
    "Подойдёт, если тебе важно понимать,\n"
    "что с тобой происходит и почему это работает.\n\n"
    "Он объясняет модель и даёт структуру,\n"
    "на которую можно опереться."
)

# Экран выбора тренера (с кнопками)
TRAINER_CHOICE_TEXT = (
    "Выбери тренера.\n\n"
    "Ты будешь работать с ним каждый день.\n"
    "Это можно изменить позже."
)

# Кнопки для выбора тренера
TRAINER_BUTTONS = {
    "marsha": "🤍 Марша — поддержка",
    "skinny": "🧱 Скинни — жёстко",
    "beck": "🧠 Бек — объясняю",
}

# ============================================================
# 🐈 TRAINER PRESENTATION BLOCK
# ============================================================

TRAINER_INTRO_TEXT = {
    "marsha": {
        "who": "🤍 Марша — мягкий тренер",
        "for_whom": (
            "Подходит, если:\n"
            "• много самокритики\n"
            "• тревога мешает начинать\n"
            "• давление только усиливает срыв\n"
        ),
        "intro": (
            "Привет. Я Марша.\n\n"
            "Я не давлю.\n"
            "Я помогаю возвращаться без стыда.\n\n"
            "Мы будем усиливать устойчивость мягко,\n"
            "но системно.\n\n"
            "Даже если ты сорвёшься — я не исчезну."
        )
    },
    "skinny": {
        "who": "🐈‍⬛ Скинни — жёсткий тренер",
        "for_whom": (
            "Подходит, если:\n"
            "• нужен толчок\n"
            "• устал от разговоров\n"
            "• хочешь структуру и результат\n"
        ),
        "intro": (
            "Привет. Я Скинни.\n\n"
            "Я не обсуждаю бесконечно.\n"
            "Мы тренируем навык через действие.\n\n"
            "Минимум слов. Максимум выполнения.\n\n"
            "Сорвёшься — поднимем и продолжим.\n"
            "Но с дистанции не уйдёшь."
        )
    },
    "beck": {
        "who": "🧠 Бек — аналитический тренер",
        "for_whom": (
            "Подходит, если:\n"
            "• важно понимать, почему это работает\n"
            "• нужна логика и структура\n"
            "• хочешь видеть систему\n"
        ),
        "intro": (
            "Привет. Я Бек.\n\n"
            "Мы будем работать через модель.\n"
            "Я объясню, что происходит с вниманием,\n"
            "и какие функции мы тренируем.\n\n"
            "Если метод не подойдёт — адаптируем.\n"
            "Решение существует."
        )
    }
}

def trainer_confirm_text(trainer_key: str) -> str:
    """Мини-подтверждение после выбора тренера"""
    if trainer_key == "marsha":
        return (
            "🤍 Хороший выбор.\n"
            "Мы будем двигаться мягко,\n"
            "без давления и самокритики."
        )
    if trainer_key == "skinny":
        return (
            "🧱 Ок.\n"
            "Будем работать чётко и по плану.\n"
            "Без лишних разговоров."
        )
    return (
        "🧠 Отлично.\n"
        "Я буду объяснять,\n"
        "что происходит и зачем мы это делаем."
    )

# ============================================================
# TEST QUESTIONS (для быстрого узнавания bucket)
# ============================================================

TEST_QUESTIONS = [
    {
        "id": 1,
        "text": "Когда ты откладываешь задачу, что чаще всего происходит?",
        "options": {
            "anxiety": "Начинаю переживать, прокручивать мысли",
            "low_energy": "Нет сил даже начать",
            "distractibility": "Отвлекаюсь почти сразу",
            "mixed": "Всего понемногу"
        }
    },
    {
        "id": 2,
        "text": "Что сложнее всего?",
        "options": {
            "low_energy": "Начать",
            "distractibility": "Удержаться",
            "anxiety": "Перестать думать",
            "mixed": "Всё сразу"
        }
    },
    {
        "id": 3,
        "text": "Когда не получилось, что ты думаешь о себе?",
        "options": {
            "anxiety": "Со мной что-то не так",
            "low_energy": "Я слишком вымотан",
            "distractibility": "Я не собранный",
            "mixed": "Я снова всё испортил"
        }
    },
    {
        "id": 4,
        "text": "Как ты обычно реагируешь на план?",
        "options": {
            "anxiety": "Начинаю переживать",
            "low_energy": "Откладываю",
            "distractibility": "Сбиваюсь",
            "mixed": "Недолго держусь"
        }
    },
    {
        "id": 5,
        "text": "Что ты хочешь больше всего?",
        "options": {
            "low_energy": "Просто начать",
            "distractibility": "Доделывать",
            "anxiety": "Меньше напряжения",
            "mixed": "Стабильность"
        }
    }
]

def resolve_bucket_from_test(answers: list) -> str:
    """Определить bucket по ответам на тест"""
    from collections import Counter
    c = Counter(answers)
    if c:
        return c.most_common(1)[0][0]
    return "mixed"

def create_test_question_keyboard(question_id: int) -> InlineKeyboardMarkup:
    """Создать клавиатуру для вопроса теста"""
    q = next((x for x in TEST_QUESTIONS if x["id"] == question_id), None)
    if not q:
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Ошибка", callback_data="noop")]])
    
    buttons = []
    for bucket_key, option_text in q["options"].items():
        buttons.append([InlineKeyboardButton(text=option_text, callback_data=f"test_q{question_id}_{bucket_key}")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def skill_explain(trainer_key: str, skill: dict) -> str:
    steps = skill.get("steps") or ([] if not skill.get("how") else [skill.get("how")])
    if trainer_key == "skinny":
        first = steps[0] if steps else skill.get("how", "")
        return f"Делай так:\n{first}\nХватит."
    if trainer_key == "beck":
        return (
            f"Почему работает:\n{skill.get('why', skill.get('goal',''))}\n\n"
            "Шаги:\n" + "\n".join(steps)
        )
    return (
        "Спокойно. Без давления.\n\n" +
        "\n".join(steps) +
        f"\n\nДаже {skill.get('micro', skill.get('minimum',''))} — считается."
    )



def _skill_steps(skill: dict) -> List[str]:
    """Return compact action steps, splitting arrow-separated strings when needed."""
    raw_steps = skill.get("steps") or skill.get("simple")
    if isinstance(raw_steps, str):
        candidates = raw_steps.split("→") if "→" in raw_steps else raw_steps.split("\n")
    elif isinstance(raw_steps, list):
        candidates = raw_steps
    else:
        how = skill.get("how") or ""
        candidates = how.split("→") if "→" in how else [how]

    steps = []
    for item in candidates:
        step = str(item or "").strip()
        if step:
            steps.append(step)
    return steps or ["Открой место, где лежит задача."]


def format_skill_card(user: dict, skill: dict, today_target: str) -> str:
    """Clean skill card with trainer-specific wording over live skill fields."""
    trainer_key = (user or {}).get("trainer_key") or "marsha"
    trainer = TRAINERS.get(trainer_key, TRAINERS["marsha"])
    steps = _skill_steps(skill)
    steps_text = "\n".join(f"{idx}. {step}" for idx, step in enumerate(steps, start=1))
    minimum_action = skill.get("minimum_action") or skill.get("minimum") or skill.get("micro") or "Открыть задачу на 30 секунд."
    why_short = skill.get("why_short") or skill.get("explain") or "Сейчас тренируем вход, а не результат."
    skill_name = skill.get("name", "Микро-шаг")
    trainer_variants = skill.get("trainer_variants") or {}
    trainer_line = trainer_variants.get(trainer_key) or trainer_variants.get("marsha")
    if not trainer_line:
        trainer_line = {
            "beck": "Логика такая: уменьшаем вход, чтобы мозгу было легче начать.",
            "skinny": "Без переговоров. Делаешь только маленький шаг.",
            "marsha": "Давай бережно: только маленький вход, без давления на результат.",
        }.get(trainer_key, "Давай бережно: только маленький вход, без давления на результат.")

    if trainer_key == "beck":
        return (
            f"{trainer['emoji']} {trainer['name']}\n\n"
            f"📌 Дело: {today_target}\n\n"
            f"🧩 Навык: {skill_name}\n\n"
            f"{trainer_line}\n\n"
            "Почему это работает:\n"
            f"{why_short}\n\n"
            "Сделай:\n"
            f"{steps_text}\n\n"
            "Минимум:\n"
            f"{minimum_action}"
        )

    if trainer_key == "skinny":
        return (
            f"{trainer['emoji']} {trainer['name']}\n\n"
            f"📌 Дело: {today_target}\n\n"
            f"🧩 {skill_name}\n\n"
            f"{trainer_line}\n\n"
            "Делаешь только это:\n\n"
            f"{steps_text}\n\n"
            "Минимум:\n"
            f"{minimum_action}\n\n"
            "Сделал — вернулся сюда."
        )

    return (
        f"{trainer['emoji']} {trainer['name']}\n\n"
        f"📌 Дело: {today_target}\n\n"
        f"🧩 Навык: {skill_name}\n\n"
        f"{trainer_line}\n\n"
        "Попробуй:\n"
        f"{steps_text}\n\n"
        "Минимум:\n"
        f"{minimum_action}\n\n"
        "Если не получится — это не провал, мы просто уменьшим шаг."
    )


def trainer_done_response(trainer_key: str) -> str:
    """Trainer-styled response for a completed action."""
    return {
        "beck": "Факт есть. Ты не просто сделал задачу — ты обошёл входной блок. Это и есть тренировка.",
        "skinny": "Есть. Не обсуждаем — фиксируем. Один подход засчитан.",
        "marsha": "Получилось. Даже маленький шаг считается. Сейчас важно не идеально, а вернуться к действию.",
    }.get(trainer_key or "marsha", "Получилось. Даже маленький шаг считается. Сейчас важно не идеально, а вернуться к действию.")


def trainer_failed_response(trainer_key: str) -> str:
    """Trainer-styled response for a failed/too-hard action."""
    return {
        "beck": "Ок. Это данные. Значит, текущий шаг слишком большой. Уменьшаем.",
        "skinny": "Не сделал — значит шаг большой. Режем задачу.",
        "marsha": "Ок. Это не провал. Похоже, шаг был тяжёлым. Давай сделаем его меньше.",
    }.get(trainer_key or "marsha", "Ок. Это не провал. Похоже, шаг был тяжёлым. Давай сделаем его меньше.")

# Раскрытая подача навыка для кнопки «ℹ️ Подробнее»
TRACK_RATIONALE = {
    "anxiety": "Останавливает тревожный цикл и возвращает в действие через микрошаг.",
    "low_energy": "Снижает порог входа: начинаем без мотивации и не выгораем.",
    "distractibility": "Сужает поток стимулов и тренирует быстрый возврат внимания.",
    "mixed": "Базовые навыки для возврата, ясности и мягкого старта при прокрастинации.",
}

def _steps_from_skill(skill: dict) -> List[str]:
    raw_steps = skill.get("steps") or skill.get("simple")
    if isinstance(raw_steps, str):
        candidates = raw_steps.split("→") if "→" in raw_steps else raw_steps.split("\n")
    elif isinstance(raw_steps, list):
        candidates = raw_steps
    else:
        how = skill.get("how") or ""
        candidates = how.split("→") if "→" in how else [how]
    return [str(item).strip() for item in candidates if str(item or "").strip()]


def skill_detail_text(skill: dict) -> str:
    """ℹ️ Details branch: render live skill fields for launch-week skills."""
    name = skill.get("name", "Навык")
    goal = skill.get("goal", "Помочь войти в действие без перегруза.")
    when_to_use = skill.get("when_to_use") or "когда трудно начать, тянет отложить или непонятно, с какого шага войти."
    steps = _steps_from_skill(skill)
    steps_text = "\n".join(f"{idx}. {step}" for idx, step in enumerate(steps, start=1))
    minimum = skill.get("minimum_action") or skill.get("minimum") or skill.get("micro") or "Открыть задачу на 30 секунд."
    example = skill.get("real_life_example") or skill.get("example") or skill.get("how_more")
    if not example and steps:
        example = " → ".join(steps[:3])
    if not example:
        example = "Открой файл, не работай, назови следующий физический шаг."
    why_long = skill.get("why_long") or skill.get("why_short") or skill.get("explain") or "Маленький шаг снижает сопротивление и помогает войти в действие."

    return (
        f"ℹ️ Подробнее о навыке: {name}\n\n"
        f"Что это:\n{goal}\n\n"
        f"Когда использовать:\n{when_to_use}\n\n"
        f"Пример:\n{example}\n\n"
        f"Шаги:\n{steps_text}\n\n"
        f"Минимум:\n{minimum}\n\n"
        f"Почему работает:\n{why_long}"
    )


def simple_explain_text() -> str:
    """🤔 I don't understand branch: simple explanation without analysis."""
    return (
        "Простыми словами:\n"
        "мы не пытаемся заставить тебя работать.\n"
        "Мы учим мозг входить в задачу без войны.\n"
        "Поэтому шаг маленький."
    )


def skeptic_text() -> str:
    """❓ Skeptic branch: explain metric-based check."""
    return (
        "Это не вопрос веры.\n"
        "Мы проверяем эффект по микро-метрикам:\n"
        "1. начал ли ты\n"
        "2. стало ли легче войти\n"
        "3. смог ли вернуться\n\n"
        "Если не работает — уменьшаем шаг или меняем навык."
    )


def day3_offer_text() -> str:
    """Offer shown after day 3 completion/summary."""
    return (
        "За 3 дня уже видно:\n\n"
        "— где ты застреваешь\n"
        "— что помогает начать\n"
        "— где ты сливаешься\n"
        "— какой шаг нужно уменьшать\n\n"
        "Обычно люди после первого улучшения снова исчезают.\n\n"
        "Чтобы этого не было, нужна система:\n"
        "7 дней сопровождения\n"
        "или месяц тренировки за €14.98."
    )


def payment_20_stub_text() -> str:
    return (
        "Оплата почти готова.\n"
        "Пока тестируем MVP: напиши «хочу 7 дней», и я включу доступ вручную."
    )


def payment_month_1498_stub_text() -> str:
    return (
        "Месячный режим включает:\n"
        "— ежедневное сопровождение\n"
        "— память паттернов\n"
        "— адаптацию навыков\n"
        "— вечерние итоги\n\n"
        "Пока оплата подключается.\n"
        "Я записал твой выбор."
    )


def payment_declined_soft_text() -> str:
    return (
        "Ок.\n"
        "Продолжим в коротком режиме.\n"
        "Один навык в день.\n"
        "Если захочешь полное сопровождение — вернёшься."
    )


def payment_includes_text() -> str:
    return (
        "Что входит:\n"
        "— ежедневное сопровождение\n"
        "— память паттернов\n"
        "— адаптацию навыков\n"
        "— вечерние итоги\n"
        "— уменьшение шага, если действие ломается"
    )



def morning_checkin_text(name: str) -> str:
    return (
        f"Доброе утро, {name}.\n\n"
        "Коротко отметь состояние — и я подберу шаг на сегодня."
    )


def evening_checkin_text() -> str:
    return (
        "Как прошёл день?\n\n"
        "Важно не идеально.\n"
        "Важно: был ли хоть один возврат к действию."
    )


def reactivation_text(count: int) -> str:
    lines = {
        1: "Ты не провалился. Просто выпал из цикла. Вернёмся с 30 секунд?",
        2: "Не надо догонять. Не надо начинать заново. Один маленький шаг — и ты снова внутри.",
        3: "Я больше не буду дёргать. Маршрут сохранён. Вернёшься — продолжим с маленького шага.",
    }
    return lines.get(max(1, min(int(count or 1), 3)), lines[3])

# ============================================================
# 3) KEYBOARDS
# ============================================================


kb_morning_checkin = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="😐 норм"), KeyboardButton(text="😣 тяжело")],
        [KeyboardButton(text="🔋 нет сил"), KeyboardButton(text="📱 отвлекаюсь")],
        [KeyboardButton(text="🚪 не хочу начинать")],
    ],
    resize_keyboard=True
)

kb_evening_checkin = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ сделал"), KeyboardButton(text="😐 частично")],
        [KeyboardButton(text="❌ не сделал"), KeyboardButton(text="↩️ срывался, но возвращался")],
    ],
    resize_keyboard=True
)


kb_notifications_consent = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Ок, можно писать")],
        [KeyboardButton(text="🔕 Без напоминаний")],
    ],
    resize_keyboard=True,
)


def notifications_consent_text() -> str:
    return (
        "Я могу писать утром и вечером:\n"
        "— утром подобрать шаг\n"
        "— вечером закрыть день\n"
        "— если ты пропадёшь, мягко вернуть\n\n"
        "Можно отключить в любой момент."
    )


def user_help_text() -> str:
    return (
        "Что умеет бот:\n"
        "— помогает выбрать маленький шаг на день\n"
        "— уменьшает шаг, если сложно начать\n"
        "— вечером закрывает день без стыда\n"
        "— показывает прогресс и маршрут\n"
        "— включает кризисный режим по /crisis\n\n"
        "Команды: /progress, /settings, /stop, /start_over, /crisis."
    )


def settings_text(notifications_enabled: int, timezone: str) -> str:
    status = "включены" if int(notifications_enabled or 0) == 1 else "выключены"
    return (
        "Настройки:\n"
        f"— напоминания: {status}\n"
        f"— часовой пояс: {timezone or 'Europe/Vilnius'}\n\n"
        "Чтобы отключить напоминания: /stop\n"
        "Чтобы начать заново: /start_over"
    )


def start_over_confirm_text() -> str:
    return "Ок. Начинаем заново, но без удаления глобальной аналитики. Как к тебе обращаться? (1 слово)"

kb_input_mode = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🧠 Диагностика текстом")],
        [KeyboardButton(text="🎙 Диагностика голосом")],
        [KeyboardButton(text="❓ Быстрый тест (5 вопросов)")],
    ],
    resize_keyboard=True,
)

kb_trainers = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🐈‍⬛ Скинни (жёстко)")],
        [KeyboardButton(text="🐈 Марша (мягко)")],
        [KeyboardButton(text="🐈‍🦁 Бек (аналитично)")],
    ],
    resize_keyboard=True,
)

# ============================================================
# EXTRA KEYBOARDS — Training / Crisis / Payment options
# ============================================================

kb_training_main = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💪 Давай действие")],
        [KeyboardButton(text="📚 Подробнее"), KeyboardButton(text="Ещё")],
        [KeyboardButton(text="🆘 Кризис")],
    ],
    resize_keyboard=True
)

kb_more_actions = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Прогресс"), KeyboardButton(text="🗺 Показать маршрут")],
        [KeyboardButton(text="🔁 Заменить навык"), KeyboardButton(text="❓ Сомневаюсь")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True
)



kb_skill_card = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Сделал"), KeyboardButton(text="❌ Не сделал")],
        [KeyboardButton(text="😣 Слишком сложно"), KeyboardButton(text="🤔 Не понял")],
        [KeyboardButton(text="🆘 Кризис")],
    ],
    resize_keyboard=True
)

kb_done = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔁 Ещё круг")],
        [KeyboardButton(text="🌙 На сегодня хватит")],
    ],
    resize_keyboard=True
)

kb_failed = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="😣 Слишком сложно"), KeyboardButton(text="😵 Нет сил")],
        [KeyboardButton(text="📱 Залип"), KeyboardButton(text="🤔 Не понял")],
    ],
    resize_keyboard=True
)

kb_action_clarify = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Не та причина"), KeyboardButton(text="Не тот навык")],
        [KeyboardButton(text="Слишком сложно"), KeyboardButton(text="Я не понимаю")],
    ],
    resize_keyboard=True
)

kb_downscale = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Сделал")],
        [KeyboardButton(text="😣 Даже это сложно"), KeyboardButton(text="🤔 Зачем так мало?")],
        [KeyboardButton(text="🆘 Кризис")],
    ],
    resize_keyboard=True
)

kb_downscale_name_task = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Написал")],
        [KeyboardButton(text="🆘 Кризис")],
    ],
    resize_keyboard=True
)

kb_microstep = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="💪 Давай действие")]],
    resize_keyboard=True
)

kb_skeptic = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💪 Давай действие")],
        [KeyboardButton(text="😣 Слишком сложно"), KeyboardButton(text="🔁 Заменить навык")],
    ],
    resize_keyboard=True
)

kb_crisis_mode = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎙 Кризис голосом")],
        [KeyboardButton(text="✍️ Кризис текстом")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True
)

# Keyboard shown after user requests more details — allows asking for clarification
kb_more_clarify = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💪 Давай действие")],
        [KeyboardButton(text="🤔 Я не понимаю")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True
)

kb_doubt_response = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👍 Понял(а), продолжаем")],
        [KeyboardButton(text="📚 Подробнее почему это работает")],
    ],
    resize_keyboard=True
)

kb_yes_no = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="✅ Да"), KeyboardButton(text="❌ Нет")]],
    resize_keyboard=True
)

# ============================================================
# Анализ подтверждение + уточнение
# ============================================================

kb_analysis_confirm = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Да, в точку"), KeyboardButton(text="📚 Подробнее")],
        [KeyboardButton(text="🤔 Не совсем"), KeyboardButton(text="💪 Давай действие")],
    ],
    resize_keyboard=True
)

kb_pay_choice = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="7 дней — €20"), KeyboardButton(text="Месяц — €14.98")],
        [KeyboardButton(text="Подумаю"), KeyboardButton(text="Что входит?")],
    ],
    resize_keyboard=True
)

def payment_inline_discount(payment_url_discount: str) -> InlineKeyboardMarkup:
    if not payment_url_discount:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Скидка: ссылка не настроена", callback_data="noop")]]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить со скидкой", url=payment_url_discount)]]
    )

def payment_inline_full(payment_url_full: str) -> InlineKeyboardMarkup:
    if not payment_url_full:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Полная: ссылка не настроена", callback_data="noop")]]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить без скидки", url=payment_url_full)]]
    )


def payment_inline_20(payment_url_discount: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="7 дней — €20", url=payment_url_discount)]]
    )


def payment_inline_month_1498(payment_url_full: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Месяц — €14.98", url=payment_url_full)]]
    )

kb_yes_no_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="yes")],
        [InlineKeyboardButton(text="❌ Нет", callback_data="no")],
    ]
)

def payment_inline(payment_url: str) -> InlineKeyboardMarkup:
    if not payment_url:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Ссылка на оплату не настроена", callback_data="noop")]]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=payment_url)]]
    )

ONBOARDING_SCREENS = [
    '😮\u200d💨 Ты знаешь, ЧТО делать, но это не становится действием.\n\nПроблема не в силе воли.\nМы тренируем:\n— запуск\n— внимание\n— возврат после срыва\n\nМинимум — 60–120 секунд.\nСрыв — часть процесса.\n\n⚠️ Это не терапия и не диагноз.\nВ кризис — жми «🆘 Кризис».',
    'Сейчас выберешь тренера:\nМарша — мягко\nСкинни — чётко\nБек — с объяснениями\n\nПотом короткая диагностика — и первый навык.',
]

# ============================================================
# 7) SALES & ONBOARDING TEXTS (карта, гарантия, таймеры)
# ============================================================

def month_map_short(bucket: str) -> str:
    """Короткая версия месячной карты (показываем сразу)"""
    return (
        "🗺 Твоя карта на ближайший месяц:\n\n"
        "Неделя 1 — стабилизация\n"
        "Научимся возвращаться без самокритики и запускать действия.\n\n"
        "Неделя 2 — контроль\n"
        "Начнём удерживать внимание и снижать сопротивление.\n\n"
        "Неделя 3 — устойчивость\n"
        "Меньше срывов, больше предсказуемости.\n\n"
        "Неделя 4 — закрепление\n"
        "Навыки начинают работать автоматически.\n\n"
        "Это не марафон. Это тренировка системы."
    )

def month_map_full(bucket: str) -> str:
    """Подробная версия месячной карты (по кнопке «Подробнее»)"""
    return (
        "🗺 Подробная карта тренировки:\n\n"
        "🔹 Неделя 1 — Стабилизация\n"
        "• возвращаться к задаче без самонаказания\n"
        "• запускать действие даже без мотивации\n\n"
        "🔹 Неделя 2 — Контроль\n"
        "• удерживать внимание дольше\n"
        "• не срываться при дискомфорте\n\n"
        "🔹 Неделя 3 — Устойчивость\n"
        "• меньше откатов\n"
        "• меньше внутреннего давления\n\n"
        "🔹 Неделя 4 — Закрепление\n"
        "• навыки работают без постоянного контроля\n"
        "• появляется ощущение управляемости\n\n"
        "Мы будем адаптировать маршрут, если что-то не подойдёт."
    )

# 🔒 ГАРАНТИЯ (универсальная, не страшная)

GUARANTEE_TEXT = (
    "🔒 Наша гарантия:\n\n"
    "Мы не обещаем «исцеление» или быстрые чудеса.\n\n"
    "Мы гарантируем:\n"
    "• понятный план\n"
    "• сопровождение\n"
    "• адаптацию навыков под тебя\n\n"
    "Если навык не работает — мы его меняем.\n"
    "Если формат не заходит — упрощаем.\n\n"
    "Ты не останешься один(одна) с задачей."
)

GUARANTEE_SHORT = (
    "Если что-то не работает — мы это меняем.\n"
    "Ты не застрянешь один(одна)."
)

# 💰 ТАЙМЕРЫ И ДЕДЛАЙНЫ (продающие, но человеческие)

def offer_day_3(name: str) -> str:
    """Текст дня 3 — основной дожим со скидкой"""
    return (
        f"{name}, ты уже не в теории.\n\n"
        "Ты потренировал(а) навык.\n"
        "И заметил(а), что:\n"
        "• стало чуть легче возвращаться\n"
        "• меньше внутреннего давления\n\n"
        "Чтобы это стало устойчивым,\n"
        "обычно нужно несколько недель.\n\n"
        "💰 Сейчас ты можешь продолжить со скидкой.\n"
        "Она доступна ещё 4 дня."
    )

def offer_day_7(name: str) -> str:
    """Текст дня 7 — последний дедлайн по скидке"""
    return (
        f"{name}, напоминаю.\n\n"
        "Скидка заканчивается сегодня.\n\n"
        "Если хочешь продолжить системно —\n"
        "это последний день по сниженной цене.\n\n"
        "Если не готов(а) — всё ок.\n"
        "Ты уже знаешь, как это работает."
    )

REMINDER_AFTER_DECLINE = (
    "Просто напомню.\n\n"
    "Ты начал(а) тренировать навык.\n"
    "И это уже шаг.\n\n"
    "Если захочешь вернуться — я здесь."
)

# ============================================================
# 7.5) АНАЛИЗ → СЛЕДУЮЩЕЕ ДЕЙСТВИЕ
# ============================================================

def analysis_next_step_short(name: str, trainer_key: str, bucket: str) -> str:
    """Короткое объяснение после анализа без курса перед первым действием."""
    base = {
        "anxiety": "ты часто не начинаешь из-за напряжения и ожидания угрозы",
        "low_energy": "тебе сложно начинать из-за истощения и перегруза",
        "distractibility": "ты начинаешь, но внимание быстро уносит",
        "mixed": "у тебя смешанный профиль — и старт, и удержание даются тяжело",
    }.get(bucket, "есть сложности с саморегуляцией")

    trainer_tone = {
        "marsha": (
            f"{name}, я вижу, как это выматывает.\n"
            "Это не слабость и не лень — это перегруженная система.\n\n"
            "Хорошая новость: это тренируется. И я буду рядом."
        ),
        "skinny": (
            f"{name}, проблема не в характере.\n"
            "Не хватает натренированных функций.\n\n"
            "Мы это исправим через действия. Без лишних слов."
        ),
        "beck": (
            f"{name}, то, что с тобой происходит — распространённый паттерн.\n"
            "Он хорошо описан и поддаётся тренировке.\n\n"
            "Дальше будет структура."
        ),
    }[trainer_key]

    return (
        f"🧠 Что я вижу:\n"
        f"Похоже, что {base}.\n\n"
        f"{trainer_tone}\n\n"
        "Это решаемо.\n"
        "Дальше — не карта на месяц, а первый маленький шаг."
    )


def analysis_next_step_long(name: str, trainer_key: str, bucket: str) -> str:
    """Подробное объяснение после анализа без карты курса."""
    trainer_finish = {
        "marsha": "Даже если что-то не пойдёт — мы подстроим путь. Ты не останешься один.",
        "skinny": "Если метод не сработает — заменим. Но ты дойдёшь.",
        "beck": "Программа адаптируется под обратную связь. Это часть протокола.",
    }[trainer_key]

    return (
        f"🔍 {name}, разложу по шагам:\n\n"
        "1️⃣ Что происходит\n"
        "Навыки саморегуляции сейчас не выдерживают нагрузку.\n\n"
        "2️⃣ Почему так\n"
        "Не из-за лени и не из-за воли — функции просто не натренированы.\n\n"
        "3️⃣ Почему это решаемо\n"
        "Эти навыки тренируются так же, как мышцы.\n\n"
        "4️⃣ Как мы будем работать\n"
        "Не мотивацией, а регулярными микро-тренировками.\n\n"
        "5️⃣ Что сейчас\n"
        "Не грузим тебя программой на месяц.\n"
        "Сначала проверяем первый вход в задачу и адаптируем шаг по факту.\n\n"
        f"{trainer_finish}\n\n"
        "Мы будем идти шаг за шагом."
    )


def month_map_text(bucket: str) -> str:
    """Предварительный маршрут тренировки: показываем только по запросу или после действия."""
    return (
        "🗺 Твой маршрут пока предварительный.\n\n"
        "Мы не будем грузить тебя программой на месяц.\n"
        "Сначала смотрим, где ломается действие.\n\n"
        "Дальше система будет адаптироваться по твоим данным:\n"
        "— где ты зависаешь\n"
        "— какие навыки сработали\n"
        "— где нужен меньший шаг\n"
        "— как ты возвращаешься после срыва\n\n"
        "Первые блоки:\n"
        "1️⃣ Вход в задачу\n"
        "2️⃣ Удержание внимания\n"
        "3️⃣ Возврат после срыва\n"
        "4️⃣ Работа с самокритикой\n\n"
        "Выводы будут точнее после первых попыток."
    )

def guarantee_block(trainer_key: str) -> str:
    """Гарантийный блок"""
    if trainer_key == "skinny":
        return (
            "🐈‍⬛ Я не брошу тебя, даже если сорвёшься.\n"
            "Срыв — часть тренировки.\n"
            "Метод не пойдёт — заменим.\n"
            "Результат будет."
        )

    if trainer_key == "beck":
        return (
            "🧠 Если метод не даст эффекта,\n"
            "мы адаптируем программу.\n"
            "Система гибкая.\n"
            "Решение существует."
        )

    return (
        "🌿 Даже если будет откат — это нормально.\n"
        "Я буду рядом.\n"
        "Мы подстроим план.\n"
        "Ты не останешься один."
    )

MONTH_CONTRACT_TEXT = (
    "📄 Контракт на работу:\n\n"
    "• срок: 1 месяц\n"
    "• цель: натренировать навыки саморегуляции\n"
    "• формат: ежедневные микро-действия\n"
    "• срывы: допустимы\n"
    "• адаптация: гарантирована\n\n"
    "Ты не обязан быть мотивирован.\n"
    "Ты обязан возвращаться.\n\n"
    "Готов начать путь?"
)

def offer_day_3_text(u: dict) -> str:
    """Усиленный дожим после 3 дней (на основе фактов)"""
    done = u.get("done_count", 0)
    ret = u.get("return_count", 0)

    return (
        "💳 Ты уже сделал(а) реальные шаги:\n\n"
        f"✅ попытки: {done}\n"
        f"↩️ возвраты: {ret}\n\n"
        "Это не «старался(ась)».\n"
        "Это и есть сдвиг.\n\n"
        "Если продолжить — эффект закрепится.\n"
        "Со скидкой — прямо сейчас."
    )

def inactivity_ping(trainer_key: str) -> str:
    """Авто-пинг через 24 часа неактивности"""
    return {
        "marsha": "Я рядом. Даже маленький возврат сегодня — уже достаточно.",
        "skinny": "Ты пропал. Возвращаемся. 60 секунд.",
        "beck": "Перерыв зафиксирован. Возврат сейчас снизит откат.",
    }.get(trainer_key, "Пора вернуться.")

DAILY_LIVE_LINES = {
    "marsha": [
        "Ты стараешься больше, чем тебе кажется.",
        "Завтра будет чуть легче, чем сегодня."
    ],
    "skinny": [
        "Факт есть. Продолжаем.",
        "Ты делаешь — система работает."
    ],
    "beck": [
        "Процесс запущен. Это важно.",
        "Повторение формирует устойчивость."
    ],
}

def day_task_text(name: str, trainer_key: str, day: int, skill: dict) -> str:
    intro = {
        "skinny": "Минимум слов. Максимум выполнения.",
        "marsha": "Мягко. Без давления. Главное — вернуться.",
        "beck": "Сегодня тренируем конкретную функцию."
    }.get(trainer_key, "")
    how_text = skill_explain(trainer_key, skill)

    return (
        f"🌅 {name}, День {day}\n\n"
        f"{intro}\n\n"
        f"🧩 Навык: {skill['name']}\n"
        f"🎯 Цель: {skill['goal']}\n"
        f"✅ Как: {how_text}\n\n"
        "Важно:\n"
        "60–120 секунд — считается.\n"
        "Не результат, а попытка."
    )

def midday_ping(name: str, trainer_key: str) -> str:
    if trainer_key == "skinny":
        return f"⏱ {name}, 60 секунд. Потом свободен."
    if trainer_key == "beck":
        return f"⏱ {name}, это тренировка процесса, не результата."
    return f"⏱ {name}, если не сделал — просто вернись. Этого достаточно."

def gamify_status_line(u: dict) -> str:
    pts = int(u.get("points") or 0)
    lvl = int(u.get("level") or 1)
    streak = int(u.get("streak") or 0)
    return f"🏅 Очки: {pts} | Уровень: {lvl} | Стрик: {streak}"

# ============================================================
# AI SYSTEM PROMPTS
# ============================================================

AI_ANALYSIS_SYSTEM_PROMPT = """
Ты — AI-ассистент тренинга навыков саморегуляции.
Это НЕ психотерапия и НЕ диагноз.

Твоя задача:
— объяснить человеку, что с ним происходит
— показать, что проблема решаема
— продать путь тренировки на 1–2 месяца
— дать ощущение сопровождения и адаптации

Ограничения:
— нельзя ставить диагнозы
— нельзя использовать клинические термины
— нельзя обещать лечение
— нельзя говорить, что человек «сломан»

ВАЖНО:
Существует ТОЛЬКО 4 пути:
1) anxiety — тревожное избегание
2) low_energy — трудно начинать (апатия / истощение)
3) distractibility — высокая отвлекаемость
4) mixed — сочетание нескольких факторов

Также учитывай:
— почти всегда есть самокритика и самообвинение
— сначала тренируются НАВЫКИ
— работа с мыслями подключается ПОТОМ, мягко, не как терапия

Ты ОБЯЗАН внушать:
— «ты справишься»
— «это тренируется»
— «если метод не подойдёт — его заменят»

Верни СТРОГО JSON без комментариев.
"""

def build_ai_system_prompt() -> str:
    return (
        "Ты — ассистент тренинга навыков саморегуляции. Это НЕ терапия, НЕ диагноз.\n"
        "Твоя задача: по короткому описанию определить рабочий bucket и дать краткий разбор.\n"
        "Выход строго JSON без текста вокруг.\n"
        "Формат:\n"
        "{\n"
        '  "bucket": "anxiety|low_energy|distractibility|mixed",\n'
        '  "summary": "1-2 предложения о паттерне",\n'
        '  "confidence": 0.0-1.0,\n'
        '  "top_signals": ["...","..."],\n'
        '  "first_action": "1 конкретный шаг на сегодня"\n'
        "}\n"
        "Не придумывай диагнозов. Не говори про лечение. Без морали.\n"
    )
