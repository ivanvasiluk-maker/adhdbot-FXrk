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
        "short": "Не характер. Не лень. Чиним вход через действия.",
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
        "short": "Снижаем стоимость входа и проверяем эффект по действиям.",
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
        "beck": "Логика такая: мозг блокирует не задачу, а слишком дорогой вход. Снижаем стоимость входа и проверяем эффект по действиям.",
        "skinny": "Не характер. Не лень. Навык входа разваливается. Чиним через действия. Ты не ленивый. Вход слишком дорогой. Уменьшаем.",
        "marsha": "Похоже, шаг был слишком тяжелым. Это не провал. Давай сделаем вход мягче и безопаснее.",
    }.get(trainer_key or "marsha", "Похоже, шаг был слишком тяжелым. Это не провал. Давай сделаем вход мягче и безопаснее.")

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


def day3_offer_text(main_pattern: str = "сложно войти в действие", best_skill: str = "маленький вход в задачу", weak_point: str = "старт при перегрузе") -> str:
    """Offer shown after day 3 with a primary action map."""
    return (
        "🧭 Твоя первичная карта\n\n"
        "За эти дни видно: дело не в «лени».\n\n"
        f"Похоже, главный паттерн:\n{main_pattern}\n\n"
        f"Что уже помогает:\n{best_skill}\n\n"
        f"Где ещё ломается:\n{weak_point}\n\n"
        "Это не диагноз.\n"
        "Это рабочая карта, которую мы строим по твоим действиям.\n\n"
        "Дальше можно собрать полную карту на 30 дней:\n"
        "— какие задачи тебя блокируют\n"
        "— какие эмоции запускают избегание\n"
        "— какие навыки работают именно у тебя\n"
        "— какой режим и среда тебе подходят\n\n"
        "Продолжить на месяц?"
    )



def preliminary_hypothesis_note() -> str:
    return (
        "Пока это предварительная гипотеза.\n\n"
        "Мы ещё не знаем тебя достаточно хорошо.\n\n"
        "Первые дни система будет смотреть:\n"
        "— где ломается вход\n"
        "— какие навыки помогают\n"
        "— где нужен меньший шаг\n"
        "— как ты реагируешь на срывы\n"
        "— что облегчает старт именно тебе\n\n"
        "Поэтому более точная карта появится через несколько дней практики."
    )


def day3_primary_map_text(
    start_pattern: str,
    avoidance_trigger: str,
    best_skills: str,
    downscale_pattern: str,
    preferred_activation: str,
    return_pattern: str,
) -> str:
    return (
        "🧭 Первичная карта\n\n"
        "За эти дни уже видно,\n"
        "что проблема не сводится к “лени”.\n\n"
        "Что система заметила:\n\n"
        f"— тебе особенно трудно начинать:\n{start_pattern}\n\n"
        f"— чаще всего вход ломается из-за:\n{avoidance_trigger}\n\n"
        f"— лучше всего сработали:\n{best_skills}\n\n"
        f"— когда шаг становится слишком большим:\n{downscale_pattern}\n\n"
        f"— похоже, тебе легче действовать:\n{preferred_activation}\n\n"
        f"— после срывов ты чаще:\n{return_pattern}\n\n"
        "Это ещё не полная картина.\n"
        "Но система уже начала подстраивать тренировки под тебя.\n\n"
        "Следующие недели нужны не для “мотивации”,\n"
        "а чтобы собрать устойчивую модель:\n"
        "— как тебе легче входить в задачи\n"
        "— как удерживать внимание\n"
        "— как возвращаться без самокритики\n"
        "— какие навыки реально работают именно у тебя\n\n"
        "Сейчас у нас уже есть первые сигналы.\n"
        "Но устойчивые паттерны появляются только через повторения.\n\n"
        "Следующий этап —\n"
        "не просто упражнения,\n"
        "а сбор устойчивой модели:\n"
        "что помогает именно тебе,\n"
        "где ломается внимание,\n"
        "и как выстроить систему,\n"
        "в которую мозгу легче возвращаться."
    )


def profile_map_details_text() -> str:
    return (
        "Сейчас выводы ещё предварительные.\n\n"
        "Точная модель строится не по словам,\n"
        "а по повторяющимся действиям:\n\n"
        "— какие навыки ты избегаешь\n"
        "— где возвращаешься\n"
        "— какие форматы помогают\n"
        "— как реагируешь на перегруз\n"
        "— какие шаги оказываются слишком большими\n\n"
        "Поэтому система становится точнее\n"
        "не после длинных анкет,\n"
        "а после реальных попыток."
    )


def profile_signals_text(
    return_count: int,
    downscale_count: int,
    done_count: int,
    avoidance_trigger: str,
    best_skills: str,
    preferred_activation: str,
) -> str:
    return (
        "📊 Что уже видно:\n\n"
        f"Возвраты после срыва: {return_count}\n"
        f"Downscale: {downscale_count}\n"
        f"Успешные подходы: {done_count}\n\n"
        "Чаще всего мешает:\n"
        f"😬 {avoidance_trigger}\n\n"
        "Лучше всего помогают:\n"
        f"{best_skills}\n\n"
        "Похоже, тебе легче начинать:\n"
        f"{preferred_activation}\n\n"
        "Это пока предварительная карта."
    )

def payment_20_stub_text() -> str:
    return (
        "Оплата почти готова.\n"
        "Пока тестируем MVP: напиши «хочу 7 дней», и я включу доступ вручную."
    )


def payment_month_1498_stub_text() -> str:
    return (
        "Оплата почти готова.\n\n"
        "Пока тестируем запуск:\n"
        "напиши сюда «хочу месяц»,\n"
        "и я включу доступ вручную."
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
        "Сейчас у нас уже есть первые сигналы.\n"
        "Но устойчивые паттерны появляются только через повторения.\n\n"
        "Следующий этап —\n"
        "не просто упражнения,\n"
        "а сбор устойчивой модели:\n"
        "что помогает именно тебе,\n"
        "где ломается внимание,\n"
        "и как выстроить систему,\n"
        "в которую мозгу легче возвращаться.\n\n"
        "В месячном режиме система смотрит:\n"
        "— где ломается вход\n"
        "— какие навыки реально помогают\n"
        "— где нужен меньший шаг\n"
        "— как ты возвращаешься после срыва\n"
        "— какой формат запуска подходит тебе"
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
        [KeyboardButton(text="📊 Прогресс"), KeyboardButton(text="🧭 Моя карта")],
        [KeyboardButton(text="🔁 Заменить навык"), KeyboardButton(text="😑 Ты меня не понял")],
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
        [KeyboardButton(text="🌙 Хватит на сегодня")],
        [KeyboardButton(text="📌 Что изменилось?")],
    ],
    resize_keyboard=True
)

kb_day_core_stop = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌙 На сегодня хватит")],
        [KeyboardButton(text="🧭 Моя карта")],
    ],
    resize_keyboard=True
)

kb_day_core_stop = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌙 На сегодня хватит")],
        [KeyboardButton(text="🧭 Моя карта")],
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


kb_crisis_stabilize = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Сделал")],
        [KeyboardButton(text="↩️ Вернуться в тренировку")],
        [KeyboardButton(text="🆘 Мне всё ещё плохо")],
        [KeyboardButton(text="✍️ Написать, что происходит")],
    ],
    resize_keyboard=True
)


def crisis_stabilize_text() -> str:
    return (
        "Стоп.\n\n"
        "Сейчас не решаем жизнь.\n\n"
        "1. Ноги на пол.\n"
        "2. Один длинный выдох.\n"
        "3. Назови следующую микроточку.\n\n"
        "Только это."
    )


def crisis_still_bad_text() -> str:
    return (
        "Ок. Тогда задача не продуктивность.\n"
        "Задача — стабилизация.\n\n"
        "Сделай одну вещь:\n"
        "вода / сесть / открыть окно / написать живому человеку.\n\n"
        "Если есть риск причинить вред себе или кому-то — обратись за срочной живой помощью."
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
        [KeyboardButton(text="😑 Ты меня не понял"), KeyboardButton(text="💪 Давай действие")],
        [KeyboardButton(text="🤔 Не совсем")],
    ],
    resize_keyboard=True
)

kb_misunderstood_reasons = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="1. Не та проблема"), KeyboardButton(text="2. Слишком общий ответ")],
        [KeyboardButton(text="3. Не тот навык"), KeyboardButton(text="4. Это не про лень")],
        [KeyboardButton(text="5. Хочу объяснить иначе")],
    ],
    resize_keyboard=True
)

kb_analysis_need_more = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="😵 Перегруз"), KeyboardButton(text="😬 Страх ошибки")],
        [KeyboardButton(text="📱 Отвлечения"), KeyboardButton(text="🌀 Слишком много вариантов")],
        [KeyboardButton(text="😶 Не вижу смысла")],
    ],
    resize_keyboard=True
)



LONG_TERM_MICRO_HABITS = [
    {"id":"external_memory","title":"🌱 Микро-навык дня","text":"Прокрастинаторы часто пытаются держать задачи только в голове.\n\nПопробуй сегодня: один раз вынести задачу из головы в заметки.\n\nНе список на жизнь. Одну задачу.","why":"Когда задача лежит только в голове, мозг продолжает крутить её в фоне как незакрытый цикл.","trainer_variants":{"beck":"Когда задача лежит только в голове, мозг продолжает держать её активной. Внешняя память снижает перегруз.","skinny":"Не держи всё в голове. Записал — выгрузил.","marsha":"Тебе не нужно помнить всё самому. Можно чуть-чуть разгрузить голову."}},
    {"id":"one_visible_step","title":"🌱 Микро-навык дня","text":"Попробуй сегодня оставить следующий шаг на виду: открыть вкладку заранее, оставить документ открытым или положить вещь на видное место.","why":"Мозгу легче вернуться туда, где вход уже подготовлен.","trainer_variants":{"beck":"Подготовленный вход снижает стоимость возврата в задачу.","skinny":"Подготовь вход заранее. Вернуться будет проще.","marsha":"Сделай вход чуть легче заранее — это забота о себе."}},
    {"id":"bad_draft","title":"🌱 Микро-навык дня","text":"Сегодня попробуй сделать один плохой черновик. Не хороший. Не финальный. Не красивый. Просто существующий.","why":"Перфекционизм часто блокирует не работу, а разрешение начать плохо.","trainer_variants":{"beck":"Черновик снимает блок идеального старта.","skinny":"Плохой черновик лучше нуля.","marsha":"Можно начать неидеально — это уже движение."}},

    {"id":"rest_before_crash","title":"🌱 Микро-навык дня","text":"Не ждать полного истощения для отдыха. Попробуй 2–5 минут паузы до момента, когда мозг уже вырубился.","why":"Перегруженный мозг хуже замечает усталость заранее — короткая пауза заранее снижает срыв.","trainer_variants":{"beck":"Ранний короткий отдых снижает цену когнитивного истощения.","skinny":"Пауза до падения — это стратегия, не слабость.","marsha":"Небольшой отдых заранее помогает бережно сохранить ресурс."}},
    {"id":"one_tab","title":"🌱 Микро-навык дня","text":"Одна вкладка — меньше распада внимания. Попробуй хотя бы 3 минуты оставить только одно окно по задаче.","why":"Ограничение контекста снижает переключения и упрощает вход.","trainer_variants":{"beck":"Один контекст уменьшает стоимость возврата внимания.","skinny":"Одна вкладка. Три минуты. Без хаоса.","marsha":"Сделай пространство чуть тише: одно окно на короткое время."}},
    {"id":"body_doubling_notice","title":"🌱 Микро-навык дня","text":"Сегодня просто заметь: рядом с человеком, на созвоне или в коворкинге тебе легче начать или нет. Ничего доказывать не надо.","why":"Body doubling — это внешний контур запуска. Важно заметить, помогает ли он именно тебе.","trainer_variants":{"beck":"Мы проверяем формат запуска: одному или рядом с человеком.","skinny":"Заметь факт: рядом легче или нет. Всё.","marsha":"Можно начинать не в одиночку. Сегодня просто понаблюдай, помогает ли это."}},
    {"id":"return_without_punishment","title":"🌱 Микро-навык дня","text":"План возврата на сегодня: если выпадешь — не догоняй идеально. Вернись с одного маленького шага без наказания себя.","why":"Заранее готовый возврат снижает шанс, что один срыв превратится в полный откат.","trainer_variants":{"beck":"План возврата заранее снижает цену срыва.","skinny":"Выпал — один маленький шаг назад. Без драмы.","marsha":"Ты можешь заранее разрешить себе мягкий возврат после срыва."}},
    {"id":"pause_before_scroll","title":"🌱 Микро-навык дня","text":"Не запрещай себе скролл.\nПросто один раз поймай момент,\nкогда рука сама тянется открыть ленту.\n\nЭто не запрет. Это тренировка паузы перед автопилотом.","why":"Пауза перед скроллом не борется с привычкой силой, а возвращает момент выбора до автопилота.","trainer_variants":{"beck":"Наблюдение перед действием переводит автопилот в осознанный выбор.","skinny":"Не запрещай. Поймай момент перед лентой.","marsha":"Без запрета. Просто мягко заметь секунду перед скроллом."}},
]

SYSTEM_PHRASES = [
"Мы сейчас тренируем не мотивацию, а возврат.",
"Система адаптируется по твоим действиям.",
"Срыв — это тоже данные.",
"Навык строится повторениями, а не идеальными днями.",
"Мозгу легче возвращаться туда, где вход маленький.",
"Мы не ломаем сопротивление силой. Мы уменьшаем трение.",
]

kb_micro_habit = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="👍 Попробую")],[KeyboardButton(text="🤔 Зачем это?")],[KeyboardButton(text="➡️ Дальше")]],
    resize_keyboard=True
)

kb_pay_choice = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💳 Продолжить за €14.98")],
        [KeyboardButton(text="📚 Подробнее о карте")],
        [KeyboardButton(text="🧭 Показать мои сигналы")],
        [KeyboardButton(text="🤔 Подумаю")],
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
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить €14.98", url=payment_url_full)]]
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
    'Сейчас выберешь тренера:\nМарша — мягко\nСкинни — чётко\nБек — с объяснениями\n\nПотом короткая рабочая карта — и первый навык.',
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
        "marsha": "Если шаг не подойдёт — мы уменьшим или заменим его. Ты не остаёшься с этим один.",
        "skinny": "Не пошло — режем шаг или меняем навык. Без лекций.",
        "beck": "Дальше смотрим на данные: где вход сорвался, какой шаг сработал, что нужно уменьшить.",
    }[trainer_key]

    return (
        f"🔍 {name}, коротко по механике:\n\n"
        "1️⃣ Что происходит\n"
        "Важная задача ощущается слишком дорогой на входе.\n\n"
        "2️⃣ Почему так\n"
        "Не из-за лени и не из-за воли. Мозг выбирает быстрый способ снять напряжение: отложить, переключиться, подготовиться вместо старта.\n\n"
        "3️⃣ Что проверяем первым\n"
        "Какой самый маленький вход реально проходит сегодня: открыть, назвать, сделать плохой первый шаг.\n\n"
        "4️⃣ Как будем работать\n"
        "Короткий подход → факт → адаптация шага. Без больших обещаний.\n\n"
        f"{trainer_finish}"
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
Ты — AI-ассистент тренинга ADHD-навыков. Это НЕ психотерапия и НЕ диагноз.

Задача: дать короткий точный анализ поведения, без generic GPT-фраз и без мотивационной лекции.

Запрещённые формулировки:
— управление временем
— концентрация
— постановка целей
— навыки саморегуляции не выдерживают нагрузку
— тренируется как мышцы
— многие сталкиваются, это нормально

Ищи конкретику:
— какие задачи стопорят: без конца, с риском ошибки, скучные, длинные, неопределённые, без быстрого результата
— куда человек уходит: телефон, мелкие срочные дела, подготовка, планирование, уборка, поиск идеального состояния, сон, заморозка
— какой полезный сигнал уже виден: легче рядом с другим, включают дедлайны, помогает внешний старт, замечает момент ухода, проще с маленьким шагом

Верни СТРОГО JSON без комментариев:
{
  "bucket": "anxiety|low_energy|distractibility|mixed",
  "specific_pattern": "главный конкретный стопор, 5-12 слов",
  "avoidance_behavior": "куда мозг уходит вместо действия, 5-14 слов",
  "useful_signal": "один полезный сигнал из текста, 5-16 слов",
  "skills_focus": ["навык1", "навык2", "навык3"],
  "selected_skill": "skill_id если очевидно или open_only"
}

Пиши по-русски, коротко, конкретно. Если данных мало — не выдумывай, оставь поля конкретными, но осторожными.
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
