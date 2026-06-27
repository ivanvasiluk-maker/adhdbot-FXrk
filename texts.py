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
        "display_name": "Скинни",
        "tone": "жёсткий, прямой",
        "grammatical_gender": "masculine",
        "emoji": "🐈‍⬛",
        "short": "Не характер. Не лень. Чиним вход через действия.",
        "response_templates": {
            "check_barrier": "Сейчас проверим, что именно ломает вход.",
        },
    },
    "marsha": {
        "name": "Марша",
        "display_name": "Марша",
        "tone": "мягкий, поддерживающий",
        "grammatical_gender": "feminine",
        "emoji": "🐈",
        "short": "Мягко возвращаемся. Без наказания. Навык важнее эмоций.",
        "response_templates": {
            "check_barrier": "Давай спокойно посмотрим, что именно сейчас слишком трудно.",
        },
    },
    "beck": {
        "name": "Бек",
        "display_name": "Бек",
        "tone": "аналитичный, структурный",
        "grammatical_gender": "masculine",
        "emoji": "🐈‍🦁",
        "short": "Снижаем стоимость входа и проверяем эффект по действиям.",
        "response_templates": {
            "check_barrier": "Проверим, какой фактор запускает избегание: оценка, неопределённость или перегруз.",
        },
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


MAX_KEYBOARD_BUTTONS = 8


def keyboard_button_count(reply_markup) -> int:
    """Count buttons in reply/inline keyboards for overload guardrails."""
    if not reply_markup:
        return 0
    rows = getattr(reply_markup, "keyboard", None) or getattr(reply_markup, "inline_keyboard", None) or []
    return sum(len(row) for row in rows)

# Crisis limit for non-paid users
CRISIS_LIMIT = 3

MENTAL_HEALTH_BOUNDARY_NOTE = (
    "Я могу дать только навык самопомощи и не заменяю психолога, врача или экстренную службу."
)

CRISIS_SAFETY_NOTE = (
    "Если есть риск причинить вред себе или кому-то, угроза насилия, потеря контроля "
    "или состояние резко ухудшается — пожалуйста, обратись за срочной живой помощью: "
    "позвони в местный экстренный номер или напиши/позвони близкому человеку рядом."
)

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



def _target_header_text(today_target: str) -> str:
    target = (today_target or "").strip()
    if target == "__target_not_selected__":
        return "📌 Дело пока не выбрано\n\nБудем тренироваться\nна типичных ситуациях прокрастинации."
    if not target:
        target = "сегодняшняя задача"
    return f"📌 Дело: {target}"

def format_skill_card(user: dict, skill: dict, today_target: str) -> str:
    """Clean skill card with trainer-specific wording over live skill fields."""
    trainer_key = (user or {}).get("trainer_key") or "marsha"
    trainer = TRAINERS.get(trainer_key, TRAINERS["marsha"])
    steps = _skill_steps(skill)
    steps_text = "\n".join(f"{idx}. {step}" for idx, step in enumerate(steps, start=1))
    minimum_action = skill.get("minimum_action") or skill.get("minimum") or skill.get("micro") or "Открыть задачу на 30 секунд."
    why_short = skill.get("why_short") or skill.get("explain") or "Сейчас тренируем вход, а не результат."
    skill_name = skill.get("name", "Микро-шаг")
    try:
        from skills import core_skill_id_for_variant, core_skill_title
        visible_core_id = user.get("current_core_skill_id") or core_skill_id_for_variant(str(skill.get("skill_id") or ""))
        visible_core_title = core_skill_title(str(visible_core_id))
    except Exception:
        visible_core_title = "Вход через маленький шаг"
    variant_label = user.get("skill_variant_label") or "Вариант сейчас"
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
            f"{_target_header_text(today_target)}\n\n"
            f"🧩 Навык дня: {visible_core_title}\n\n"
            f"{variant_label}:\n{skill_name}\n\n"
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
            f"{_target_header_text(today_target)}\n\n"
            f"🧩 Навык дня: {visible_core_title}\n\n"
            f"{variant_label}:\n{skill_name}\n\n"
            f"{trainer_line}\n\n"
            "Делаешь только это:\n\n"
            f"{steps_text}\n\n"
            "Минимум:\n"
            f"{minimum_action}\n\n"
            "Сделал — вернулся сюда."
        )

    return (
        f"{trainer['emoji']} {trainer['name']}\n\n"
        f"{_target_header_text(today_target)}\n\n"
        f"🧩 Навык дня: {visible_core_title}\n\n"
            f"{variant_label}:\n{skill_name}\n\n"
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
        "skinny": "Не лень.\nСледующий шаг слишком дорогой.\nРежем.",
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
        "Не понял?\n\n"
        "Ок.\n\n"
        "Ещё проще.\n"
        "Одно слово про задачу.\n\n"
        "Всё."
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
        "За первые дни появились первые гипотезы.\n"
        "Пока данных мало: не делаю окончательных выводов о тебе.\n\n"
        f"Похоже, нужно проверить:\n{main_pattern}\n\n"
        f"Навык/вход для проверки:\n{best_skill}\n\n"
        f"Где ещё ломается:\n{weak_point}\n\n"
        "Это не медицинское заключение.\n"
        "Это рабочая карта, которую мы строим по твоим действиям.\n\n"
        "Дальше собираем полную карту на 30 дней:\n"
        "навыки, среда, возврат, внимание, отдых и личный режим.\n\n"
        "Месяц — €14.98"
    )



def preliminary_hypothesis_note() -> str:
    return (
        "Пока это предварительная карта.\n\n"
        "Мы знаем ещё слишком мало.\n"
        "Сейчас я вижу несколько возможных причин.\n\n"
        "Но пока не понимаю:\n"
        "— что помогает тебе возвращаться;\n"
        "— что сильнее всего выбивает;\n"
        "— работает ли уменьшение шага;\n"
        "— помогают ли внешние люди.\n\n"
        "Это станет понятнее через несколько дней.\n"
        "Тогда я соберу твою персональную карту."
    )


def preliminary_diagnosis_conclusion_text(
    main_pattern: str = "",
    useful_signal: str = "",
    skills_focus: list | None = None,
) -> str:
    """Short post-diagnosis conclusion: explicitly preliminary, not a diagnosis."""
    skills_focus = skills_focus or []
    focus_lines = "\n".join(f"— {x}" for x in skills_focus[:3] if x) or "— уменьшить вход в задачу\n— проверить, что помогает возвращаться\n— подобрать рабочий формат старта"
    main_line = main_pattern or "вход в действие сейчас требует слишком много усилия"
    useful_line = f"\nЧто уже можно использовать как ресурс:\n— {useful_signal}\n" if useful_signal else ""
    return (
        "📌 ПРЕДВАРИТЕЛЬНОЕ ЗАКЛЮЧЕНИЕ\n\n"
        "Это первая рабочая модель.\n"
        "Мы ещё собираем данные.\n\n"
        "Сейчас это не медицинское заключение и не окончательный вывод.\n"
        "Это рабочая гипотеза, которую мы будем уточнять по твоим действиям.\n\n"
        f"Что сейчас похоже на главный узел:\n— {main_line}\n"
        f"{useful_line}\n"
        "Основные направления развития на ближайшие дни:\n"
        f"{focus_lines}\n\n"
        "Почему это важно:\n"
        "— ты получаешь не общий совет, а первую персональную модель\n"
        "— дальше я буду подбирать навык, размер шага и объяснение по твоим реакциям\n"
        "— каждый выполненный или не выполненный шаг делает карту точнее\n\n"
        "Через несколько дней модель станет точнее: я буду смотреть, что сработало, где шаг оказался большим, где был возврат и какие навыки реально подходят."
    )


def day3_full_conclusion_text(
    main_pattern: str,
    avoidance_trigger: str,
    successful_skills: str,
    failed_skills: str,
    return_pattern: str,
    behavior_records: str = "",
) -> str:
    behavior_block = f"\nЧто уже проверили действиями:\n{behavior_records}\n" if behavior_records else ""
    return (
        "📌 ПЕРВИЧНАЯ КАРТА ПОСЛЕ ПЕРВЫХ ДНЕЙ\n\n"
        "Это уже не только результат диагностики.\n"
        "Здесь учтены первые реальные действия: что получилось, что не подошло, где пришлось уменьшать шаг и как ты возвращался после сбоев.\n\n"
        "Что сейчас видно:\n"
        f"— основной паттерн: {main_pattern}\n"
        f"— чаще всего мешает: {avoidance_trigger}\n"
        f"— после срыва сейчас похоже: {return_pattern}\n\n"
        "Что проверяли / что могло подойти:\n"
        f"{successful_skills}\n\n"
        "Что не подошло или потребовало упрощения:\n"
        f"{failed_skills}\n"
        f"{behavior_block}\n"
        "Что становится ценностью дальше:\n"
        "— не просто выдавать упражнения, а уточнять твою персональную модель\n"
        "— выбирать сложность шага по тому, что реально получилось\n"
        "— быстрее возвращать тебя после паузы без самокритики\n"
        "— показывать, каким становится твой способ действовать\n\n"
        "Вывод пока остаётся рабочей моделью, а не медицинским заключением. Но система уже знает тебя лучше, чем в первый день, потому что опирается не только на слова, а на поведение."
    )


def day3_primary_map_text(
    start_pattern: str,
    avoidance_trigger: str,
    best_skills: str,
    downscale_pattern: str,
    preferred_activation: str,
    return_pattern: str,
    system_day_signals: str = "",
    behavior_records: str = "",
) -> str:
    system_block = f"Что ещё заметили:\n{system_day_signals}\n\n" if system_day_signals else ""
    behavior_block = f"{behavior_records}\n\n" if behavior_records else ""
    return (
        "🧭 Первичная карта действий\n\n"
        "За эти дни уже видно,\n"
        "что проблема не сводится к “лени”.\n\n"
        "Что система заметила:\n\n"
        f"— тебе особенно трудно начинать:\n{start_pattern}\n\n"
        f"— чаще всего вход ломается из-за:\n{avoidance_trigger}\n\n"
        f"— лучше всего сработали:\n{best_skills}\n\n"
        f"— когда шаг становится слишком большим:\n{downscale_pattern}\n\n"
        f"— похоже, тебе легче действовать:\n{preferred_activation}\n\n"
        f"— после срывов ты чаще:\n{return_pattern}\n\n"
        f"{system_block}"
        f"{behavior_block}"
        "Что будем тренировать дальше:\n\n"
        "1. более дешёвый вход в задачу\n"
        "2. удержание внимания без добивания себя\n"
        "3. возврат после срыва без самокритики\n\n"
        "Это ещё не полная картина.\n"
        "Но система уже начала подстраивать тренировки под тебя.\n\n"
        "Если текущая гипотеза подтвердится,\n"
        "тебе, скорее всего, понадобятся:\n\n"
        "1. навыки входа в задачу\n"
        "2. навыки работы со страхом ошибки\n"
        "3. навыки возврата после выпадения\n\n"
        "Это займёт примерно 3–5 недель тренировки.\n"
        "Но пока это гипотеза.\n\n"
        "Следующие недели нужны не для “мотивации”,\n"
        "а чтобы собрать устойчивую модель:\n"
        "— как тебе легче входить в задачи\n"
        "— как удерживать внимание\n"
        "— как возвращаться без самокритики\n"
        "— какие навыки реально работают именно у тебя\n\n"
        "Это не медицинское заключение. Это твоя рабочая карта действия.\n\n"
        "Сейчас у нас уже есть первые сигналы.\n"
        "Но устойчивые паттерны появляются только через повторения.\n\n"
        "Следующий этап —\n"
        "не просто упражнения,\n"
        "а сбор устойчивой модели:\n"
        "что помогает именно тебе,\n"
        "где ломается внимание,\n"
        "и как выстроить систему,\n"
        "в которую мозгу легче возвращаться.\n\n"
        "Поэтому продолжение — это не библиотека навыков.\n"
        "Это персональная модель, которая каждый день становится точнее.\n\n"
        "Продолжение — 14.98 €/месяц"
    )


def preliminary_development_map_text(
    assumptions: list[str] | None = None,
    checks: list[str] | None = None,
) -> str:
    """User-facing preliminary development map shown after analysis confirmation."""
    assumptions = [str(x) for x in (assumptions or []) if x][:4]
    checks = [str(x) for x in (checks or []) if x][:5]
    if not assumptions:
        assumptions = ["страх оценки", "самокритика после откладывания"]
    if not checks:
        checks = [
            "помогает ли уменьшение шага",
            "помогает ли плохой черновик",
            "помогает ли присутствие других людей",
        ]
    assumptions_text = "\n".join(f"✔ {item}" for item in assumptions)
    checks_text = "\n".join(f"✔ {item}" for item in checks)
    return (
        "🗺 Предварительная карта\n\n"
        "Это не окончательный вывод и не медицинское заключение.\n"
        "Сейчас это рабочие гипотезы, которые мы будем проверять действиями.\n\n"
        "Сейчас мы предполагаем:\n\n"
        f"{assumptions_text}\n\n"
        "Хотим проверить:\n\n"
        f"{checks_text}\n\n"
        "Следующие дни я буду смотреть:\n\n"
        "— что помогает\n"
        "— что мешает\n"
        "— где ты возвращаешься\n"
        "— где застреваешь\n\n"
        "Через несколько дней карта станет точнее."
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
    effect_note: str = "",
    failed_reason_count: int = 0,
    attention_escape_count: int = 0,
    shame_signal: str = "",
    energy_signal: str = "",
    system_day_signals: str = "",
) -> str:
    visible = ["вход часто становится слишком большим"]
    if downscale_count:
        visible.append("после уменьшения шага действие получается легче")
    if shame_signal:
        visible.append("самокритика усиливает ступор")
    if attention_escape_count:
        visible.append("залипание появляется как способ уйти от напряжения")
    if energy_signal and energy_signal != "unknown":
        visible.append("ресурс влияет на стоимость входа")
    visible_text = "\n".join(f"— {item}" for item in visible[:5])
    note_block = f"\n\nЧто отметили после шага:\n“{effect_note}”" if effect_note else ""
    system_block = f"\n\nЧто ещё заметили:\n{system_day_signals}" if system_day_signals else ""
    return (
        "🧭 Твоя предварительная карта\n\n"
        "Пока это не медицинское заключение, а рабочая гипотеза.\n\n"
        "Что уже видно:\n"
        f"{visible_text}\n\n"
        "Что сработало:\n"
        f"{best_skills}"
        f"{note_block}\n\n"
        "Всего за период:\n"
        f"— запусков: {done_count}\n"
        f"— залипаний/сбоев: {failed_reason_count}\n"
        f"— возвратов после залипания: {return_count}\n"
        f"— уменьшений шага: {downscale_count}\n\n"
        "Чаще всего мешает:\n"
        f"😬 {avoidance_trigger}"
        f"{system_block}\n\n"
        "Дальше проверим:\n"
        "— помогает ли тебе внешний контакт / body doubling\n"
        "— какой формат входа держится лучше\n\n"
        "Похоже, тебе легче начинать:\n"
        f"{preferred_activation}"
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
        "Ок. Оставляю короткий режим.\n\n"
        "В нём будет:\n"
        "— один навык в день\n"
        "— базовая тренировка\n"
        "— короткие выводы\n\n"
        "Без полного режима я не буду глубоко собирать карту, "
        "сравнивать паттерны и подбирать систему запуска по твоим реакциям.\n\n"
        "Вернуться к полному режиму можно позже."
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
        "Что сегодня больше мешает?\n\n"
        "Отметь состояние кнопкой или пришли голосовое — и я подберу core skill / версию шага на сегодня."
    )


def evening_checkin_text() -> str:
    return (
        "Как прошёл день?\n\n"
        "Важно не идеально.\n"
        "Важно: был ли хоть один возврат к действию. Можно ответить кнопкой или голосом."
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
        [KeyboardButton(text="📱 Залипаю"), KeyboardButton(text="🚪 Не могу начать")],
        [KeyboardButton(text="😵 Нет сил"), KeyboardButton(text="🌀 Всё слишком большое")],
        [KeyboardButton(text="😬 Тревога")],
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
        "— показывает зеркало развития по /mirror\n"
        "— включает кризисный режим по /crisis\n\n"
        "Команды: /progress, /mirror, /settings, /stop, /start_over, /crisis."
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
        [KeyboardButton(text="💪 Сделать следующий шаг")],
        [KeyboardButton(text="⚡ Я застрял"), KeyboardButton(text="🧭 Моя карта")],
        [KeyboardButton(text="🌙 Закрыть день")],
        [KeyboardButton(text="🆘 Мне небезопасно")],
    ],
    resize_keyboard=True,
)

kb_more_actions = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🧭 Моя карта")],
        [KeyboardButton(text="🎭 Сменить тренера")],
        [KeyboardButton(text="🔁 Заменить навык"), KeyboardButton(text="😑 Ты меня не понял")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True,
)



kb_action_outcome = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Сделал")],
        [KeyboardButton(text="🟡 Застрял / не вышло")],
        [KeyboardButton(text="⏸ Пауза")],
    ],
    resize_keyboard=True,
)

# After every skill card we deliberately keep only three choices.
kb_skill_card = kb_action_outcome
kb_new_day_skill = kb_action_outcome
kb_done = kb_action_outcome

kb_success_next = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Ещё 2 минуты")],
        [KeyboardButton(text="🌙 Закрыть подход")],
        [KeyboardButton(text="🗣️ Что помогло?")],
    ],
    resize_keyboard=True,
)

kb_success_limit = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🌙 Закрыть подход")],
    ],
    resize_keyboard=True,
)

kb_day_core_stop = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🧭 Моя карта")],
        [KeyboardButton(text="🎭 Сменить тренера")],
        [KeyboardButton(text="🆘 Мне небезопасно")],
        [KeyboardButton(text="🌙 До завтра")],
    ],
    resize_keyboard=True
)

kb_failed = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="😬 Страшно, стыдно, боюсь ошибиться")],
        [KeyboardButton(text="📱 Ушёл в телефон / YouTube")],
        [KeyboardButton(text="🔋 Нет сил")],
        [KeyboardButton(text="🧠 Слишком много всего")],
        [KeyboardButton(text="🤷 Не понимаю, зачем это делать")],
        [KeyboardButton(text="🎙️ Опишу голосом или текстом")],
        [KeyboardButton(text="🆘 Мне небезопасно")],
    ],
    resize_keyboard=True,
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
        [KeyboardButton(text="🧭 Моя карта"), KeyboardButton(text="🆘 Мне небезопасно")],
    ],
    resize_keyboard=True
)

kb_downscale_name_task = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Написал")],
        [KeyboardButton(text="🆘 Мне небезопасно")],
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
        [KeyboardButton(text="🎙 Голосом"), KeyboardButton(text="✍️ Текстом")],
        [KeyboardButton(text="🔘 Выбрать состояние кнопками")],
        [KeyboardButton(text="⬅️ Назад")],
    ],
    resize_keyboard=True
)


def crisis_entry_text() -> str:
    return (
        "Что удобнее?\n\n"
        "🎙 Голосом\n"
        "✍️ Текстом\n"
        "🔘 Выбрать состояние кнопками\n\n"
        f"{MENTAL_HEALTH_BOUNDARY_NOTE}\n"
        f"{CRISIS_SAFETY_NOTE}"
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



kb_crisis_tool_select = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⬜ Не могу начать"), KeyboardButton(text="⬜ Залип")],
        [KeyboardButton(text="⬜ Боюсь ошибки"), KeyboardButton(text="⬜ Всё слишком большое")],
        [KeyboardButton(text="⬜ Нет сил"), KeyboardButton(text="⬜ Сам себя сжираю")],
        [KeyboardButton(text="⬜ Тревога"), KeyboardButton(text="⬜ Другое")],
        [KeyboardButton(text="✅ Всё выбрал")],
    ],
    resize_keyboard=True,
)


kb_crisis_effect = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👍 Да"), KeyboardButton(text="😐 Без изменений"), KeyboardButton(text="👎 Нет")],
    ],
    resize_keyboard=True,
)


kb_crisis_action = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Сделал"), KeyboardButton(text="😣 Не могу")],
        [KeyboardButton(text="🧩 Ещё меньше"), KeyboardButton(text="🆘 Мне всё ещё плохо")],
    ],
    resize_keyboard=True,
)


kb_social_support = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👤 Один человек, кому можно написать")],
        [KeyboardButton(text="👥 Коллега / партнёр по работе")],
        [KeyboardButton(text="🏠 Семья / близкий")],
        [KeyboardButton(text="🧑‍💻 Чат / группа / комьюнити")],
        [KeyboardButton(text="🚶 Мне помогает быть среди людей")],
        [KeyboardButton(text="🙅 Сейчас нет опоры")],
        [KeyboardButton(text="✍️ Написать свой вариант")],
    ],
    resize_keyboard=True,
)


def crisis_tool_prompt_text() -> str:
    return (
        "Что сейчас мешает? Можно выбрать несколько.\n\n"
        "⬜ Не могу начать\n"
        "⬜ Залип\n"
        "⬜ Боюсь ошибки\n"
        "⬜ Всё слишком большое\n"
        "⬜ Нет сил\n"
        "⬜ Сам себя сжираю\n"
        "⬜ Тревога\n"
        "⬜ Другое\n\n"
        "Когда выбрал всё подходящее — нажми ✅ Всё выбрал. Можно также написать или прислать голос."
    )


def crisis_tool_limit_text() -> str:
    return (
        "Сегодня лимит быстрых подборов использован.\n\n"
        "Но стабилизационный минимум остаётся доступен:\n"
        "1. Стопы на пол.\n"
        "2. Один длинный выдох.\n"
        "3. Напиши живому человеку или вернись к основному навыку дня.\n\n"
        f"{CRISIS_SAFETY_NOTE}"
    )


def crisis_skill_title(pattern: str) -> str:
    return {
        "attention_escape": "Возврат из залипания",
        "task_entry_block": "Аварийный запуск",
        "perfectionism": "Плохой черновик",
        "overwhelm": "Резак задачи",
        "low_energy": "Минимально жизнеспособный день",
        "self_attack": "Факт вместо приговора",
        "anxiety_loop": "Сначала тело, потом шаг",
        "social_pain": "Контакт и опора",
        "high_risk": "Безопасность",
        "unknown": "Один управляемый шаг",
    }.get(pattern, "Один управляемый шаг")


def crisis_tool_text(pattern: str) -> str:
    tools = {
        "ZALIP": (
            "Похоже, тебя затянуло в быстрый стимул. Это не лень. Это захват внимания.\n"
            "Сейчас не ругаем себя. Возвращаем 5% контроля.\n\n"
            "Навык:\n"
            "1. Закрой/сверни источник залипания.\n"
            "2. Положи телефон дальше руки или закрой одну вкладку.\n"
            "3. Сделай один физический шаг к задаче: открыть документ / назвать задачу / написать одно слово.\n\n"
            "Минимум: Один клик в сторону задачи."
        ),
        "ANXIETY": (
            "Похоже, сейчас не лень, а тревожная активация. Сначала успокаиваем тело, потом решаем задачу.\n\n"
            "Навык:\n"
            "1. Поставь ноги на пол.\n"
            "2. Сделай 3 длинных выдоха.\n"
            "3. Назови 5 предметов вокруг.\n"
            "4. Положи руку на грудь/живот и скажи: “Это тревога, не приказ.”\n"
            "5. После этого выбери действие на 2 минуты.\n\n"
            "Минимум: Один длинный выдох + назвать 3 предмета вокруг."
        ),
        "DEPRESSIVE_LOW_ENERGY": (
            "Сейчас задача не продуктивность. Задача — вернуть минимальную опору.\n\n"
            "Навык:\n"
            "1. Сесть или поставить ноги на пол.\n"
            "2. Выпить воды / умыться / открыть окно.\n"
            "3. Написать одну фразу: “Я сейчас в низком ресурсе.”\n"
            "4. Выбрать самый маленький шаг без требования работать.\n\n"
            "Минимум: Сесть и сделать глоток воды."
        ),
        "SHAME_SELF_ATTACK": (
            "Похоже, сейчас тебя атакует не задача, а внутренний критик. Мы не спорим с ним час. Мы отделяем факт от атаки.\n\n"
            "Навык:\n"
            "1. Факт: что произошло?\n"
            "2. Атака: как ты себя сейчас называешь?\n"
            "3. Более точная фраза: “Я застрял, но это не доказывает, что я плохой.”\n"
            "4. Маленький шаг: плохой черновик / одно слово / открыть задачу.\n\n"
            "Минимум: Напиши: ‘Я застрял, но я не обязан себя добивать’."
        ),
        "NOT_UNDERSTOOD": (
            "Похоже, сейчас боль не только про задачу. Тебе нужна не мотивация, а контакт и опора.\n\n"
            "Навык:\n"
            "1. Выбери одного живого человека.\n"
            "2. Напиши коротко, без объяснений:\n"
            "“Мне сейчас тяжело. Можешь просто быть на связи 10 минут?”\n"
            "3. Если писать страшно — отправь только “можно я тебе напишу?”\n"
            "4. Потом возвращаемся к одному маленькому шагу.\n\n"
            "Минимум: Открыть чат с безопасным человеком."
        ),
        "HIGH_RISK": (
            "Сейчас это не режим продуктивности. Это режим безопасности.\n\n"
            "Ответь коротко:\n"
            "1. Ты сейчас один?\n"
            "2. Есть риск, что ты навредишь себе в ближайшие минуты?\n"
            "3. Есть рядом предметы/средства, которыми можно себе навредить?\n\n"
            "Если риск есть:\n"
            "— отойди от опасных предметов;\n"
            "— выйди к людям / открой дверь / перейди в более безопасное место;\n"
            "— напиши или позвони живому человеку;\n"
            "— обратись в экстренную службу.\n\n"
            "Я могу помочь сделать ближайший безопасный шаг, но не заменяю живую помощь."
        ),
        "UNKNOWN": (
            "Похоже, сейчас много шума.\n\n"
            "Действие:\n"
            "1. Назови одну микроточку, которую можно сделать за 1–2 минуты.\n"
            "2. Сделай только её.\n\n"
            "Минимум: один управляемый шаг."
        ),
        "attention_escape": (
            "Похоже, тебя затянуло в быстрый стимул. Это не лень. Это захват внимания.\n"
            "Сейчас не ругаем себя. Возвращаем 5% контроля.\n\n"
            "Действие:\n"
            "1. Закрой источник залипания или сверни экран.\n"
            "2. Положи телефон дальше руки.\n"
            "3. Сделай один клик в сторону задачи.\n"
            "4. Напиши сюда: “вернулся” или нажми “✅ Сделал”.\n\n"
            "Минимум: один клик."
        ),
        "task_entry_block": (
            "Похоже, сломался вход в задачу. Сейчас не нужен результат — нужен вход.\n\n"
            "Действие:\n"
            "1. Открой место задачи.\n"
            "2. Не работай.\n"
            "3. Назови следующий шаг.\n\n"
            "Минимум: открыть место задачи."
        ),
        "perfectionism": (
            "Похоже, тебя тормозит не задача, а цена ошибки.\n\n"
            "Действие:\n"
            "1. Открой документ/черновик.\n"
            "2. Напиши заголовок: “Плохой черновик”.\n"
            "3. Напиши 1–3 сырых тезиса без редактуры.\n\n"
            "Минимум: 1 плохое предложение."
        ),
        "overwhelm": (
            "Похоже, задача стала слишком большой. Уменьшаем масштаб.\n\n"
            "Действие:\n"
            "1. Не решай всю задачу.\n"
            "2. Назови первый физический шаг.\n"
            "3. Сделай только его.\n\n"
            "Минимум: назвать первый шаг."
        ),
        "low_energy": (
            "Сейчас задача не “соберись”.\n"
            "Сейчас задача — вернуть минимальную опору.\n\n"
            "Действие:\n"
            "1. Сесть.\n"
            "2. Выпить воды.\n"
            "3. Открыть окно / умыться / включить свет.\n"
            "4. Написать одну фразу: “Я сейчас в низком ресурсе”.\n"
            "5. Выбрать не работу, а микрошаг: открыть файл / назвать задачу / написать одно слово.\n\n"
            "Минимум: сесть и сделать глоток воды."
        ),
        "self_attack": (
            "Похоже, сейчас тебя атакует не задача, а внутренний критик.\n\n"
            "Действие:\n"
            "1. Факт: что произошло?\n"
            "2. Атака: как ты себя сейчас называешь?\n"
            "3. Более точная фраза: “Я застрял, но это не доказывает, что я плохой.”\n"
            "4. Маленький шаг: плохой черновик / одно слово / открыть задачу.\n\n"
            "Минимум: напиши “Я застрял, но я не обязан себя добивать”."
        ),
        "anxiety_loop": (
            "Похоже, сейчас не прокрастинация в чистом виде. Это тревога.\n"
            "Сначала тело. Потом задача.\n\n"
            "Действие:\n"
            "1. Ноги на пол.\n"
            "2. Выдох длиннее вдоха: 4 секунды вдох, 6–8 секунд выдох. Три раза.\n"
            "3. Назови 5 предметов вокруг.\n"
            "4. Скажи: “Это тревога, не приказ”.\n"
            "5. Теперь выбери один шаг на 2 минуты.\n\n"
            "Минимум: один длинный выдох + 3 предмета вокруг."
        ),
        "social_pain": (
            "Похоже, сейчас боль не только про задачу.\n"
            "Тебе нужна не мотивация, а контакт и опора.\n\n"
            "Действие:\n"
            "1. Выбери одного человека.\n"
            "2. Напиши: “Мне сейчас тяжело. Можешь быть на связи 10 минут?”\n"
            "3. Если страшно — напиши короче: “Можно я тебе напишу?”\n"
            "4. Потом вернись сюда и нажми “✅ Написал”.\n\n"
            "Минимум: открыть чат с безопасным человеком."
        ),
        "high_risk": (
            "Сейчас это не режим продуктивности. Это режим безопасности.\n\n"
            "Ответь коротко:\n"
            "1. Ты сейчас один?\n"
            "2. Есть риск, что ты можешь навредить себе в ближайшие минуты?\n"
            "3. Есть рядом предметы или средства, которыми можно себе навредить?\n\n"
            "Если риск есть:\n"
            "Отойди от опасных предметов.\n"
            "Перейди туда, где есть люди, или открой дверь.\n"
            "Напиши/позвони живому человеку.\n"
            "Обратись в экстренную службу.\n\n"
            "Я помогу сделать ближайший безопасный шаг, но не заменяю живую помощь."
        ),
        "unknown": (
            "Похоже, сейчас много шума.\n\n"
            "Действие:\n"
            "1. Назови одну микроточку, которую можно сделать за 1–2 минуты.\n"
            "2. Сделай только её.\n\n"
            "Минимум: один управляемый шаг."
        ),
    }
    return tools.get(pattern, tools["unknown"])


def social_support_prompt_text() -> str:
    return (
        "Ещё один важный кусок карты — опоры.\n"
        "Прокрастинация часто усиливается, когда человек остаётся один на один с задачей.\n\n"
        "Ответь коротко или выбери кнопками:\n"
        "Кто может быть твоей опорой, когда ты застрял?"
    )


def social_support_map_text() -> str:
    return (
        "Социальные опоры:\n"
        "— кому можно написать;\n"
        "— помогает ли присутствие других людей;\n"
        "— нужен ли внешний старт;\n"
        "— можно ли использовать короткий отчёт другому человеку."
    )


def crisis_effect_prompt_text() -> str:
    return "Стало хоть на 5% легче?"


def day_training_closed_text() -> str:
    return (
        "Сегодняшняя тренировка завершена.\n\n"
        "Главный навык дня сохранён.\n\n"
        "Новый основной навык откроется завтра."
    )


def day_lock_why_text() -> str:
    return (
        "Почему это работает:\n\n"
        "Навык закрепляется повторением, а не поиском новой техники.\n"
        "Сегодня задача — сохранить один основной вход и не превращать день в автомат с бесконечными советами.\n\n"
        "Завтра откроется новый основной навык."
    )

def crisis_stabilize_text() -> str:
    return (
        "Стоп.\n\n"
        "Сейчас не решаем всю жизнь.\n"
        "Сейчас задача вернуть 5% контроля.\n\n"
        "1. Ноги на пол.\n"
        "2. Один длинный выдох.\n"
        "3. Одна фраза: что происходит?\n\n"
        f"{MENTAL_HEALTH_BOUNDARY_NOTE}\n"
        f"{CRISIS_SAFETY_NOTE}\n\n"
        "Если непосредственной опасности нет — потом подберём следующий шаг."
    )


def crisis_still_bad_text() -> str:
    return (
        "Ок. Тогда задача не продуктивность.\n"
        "Задача — стабилизация и живая поддержка.\n\n"
        "Сделай одну вещь:\n"
        "вода / сесть / открыть окно / написать живому человеку.\n\n"
        f"{CRISIS_SAFETY_NOTE}"
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
        [KeyboardButton(text="✅ Да, в точку"), KeyboardButton(text="😑 Ты меня не понял")],
        [KeyboardButton(text="📚 Подробнее"), KeyboardButton(text="💪 Давай действие")],
        [KeyboardButton(text="🎭 Сменить тренера")],
    ],
    resize_keyboard=True
)

kb_misunderstood_reasons = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="1. Не та проблема"), KeyboardButton(text="2. Слишком общий ответ")],
        [KeyboardButton(text="3. Не тот навык"), KeyboardButton(text="4. Это не про лень")],
        [KeyboardButton(text="5. Объясню по-другому")],
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

kb_working_map = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➡️ Переходим к первому навыку")],
        [KeyboardButton(text="📚 Подробнее"), KeyboardButton(text="😑 Ты меня не понял")],
        [KeyboardButton(text="🎭 Сменить тренера")],
    ],
    resize_keyboard=True
)


kb_trainer_switch = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🤍 Марша — мягко")],
        [KeyboardButton(text="🐈‍⬛ Скинни — чётко")],
        [KeyboardButton(text="🧠 Бек — с объяснениями")],
        [KeyboardButton(text="↩️ Оставить текущего тренера")],
    ],
    resize_keyboard=True
)

kb_misunderstood_reasons = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="1. Не та проблема"), KeyboardButton(text="2. Слишком общий ответ")],
        [KeyboardButton(text="3. Не тот навык"), KeyboardButton(text="4. Это не про лень")],
        [KeyboardButton(text="5. Объясню по-другому")],
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



SYSTEMS_DAY = [
    {
        "id": "external_memory",
        "title": "🌱 Система дня",
        "text": "Это не тренировка.\nПросто идея, которую можно заметить или попробовать сегодня.\n\nНе держи задачи в голове.\nСегодня просто обрати внимание: сколько раз ты пытаешься помнить что-то, вместо того чтобы записать.\n\nДаже одна запись полезнее памяти.",
        "why": "Внешняя память снижает нагрузку на удержание задач и помогает мозгу возвращаться к действию без постоянного прокручивания в голове.",
        "map_label": "вам подходит внешняя память",
    },
    {
        "id": "one_list",
        "title": "🌱 Система дня",
        "text": "Это не тренировка.\nПросто идея, которую можно заметить или попробовать сегодня.\n\nЕсли задачи лежат: в голове, в Telegram, в заметках, в блокноте,\nмозг тратит силы на поиск.\n\nСегодня просто проверь: есть ли одно место, где лежат все задачи.",
        "why": "Один список снижает распад внимания: меньше мест для поиска — меньше трения перед стартом.",
        "map_label": "полезно собрать задачи в одно место",
    },
    {
        "id": "abc_plan",
        "title": "🌱 Система дня",
        "text": "Это не тренировка.\nПросто идея, которую можно заметить или попробовать сегодня.\n\nНе все задачи одинаково важны.\nПопробуй разделить:\nА — важно сегодня\nБ — желательно\nС — можно потом\n\nБез сложных таблиц.",
        "why": "АБС-план уменьшает шум выбора: мозгу проще начать, когда видно, что действительно важно сегодня.",
        "map_label": "помогает простое деление А/Б/С",
    },
    {
        "id": "prepare_entry",
        "title": "🌱 Система дня",
        "text": "Это не тренировка.\nПросто идея, которую можно заметить или попробовать сегодня.\n\nЧасто самое трудное — не работа.\nСамое трудное — вход в неё.\n\nПеред сном подготовь:\n• открытый документ;\n• ссылку;\n• блокнот;\n• черновик.\n\nЗавтрашнему себе будет проще.",
        "why": "Подготовленный вход снижает стоимость старта завтра: меньше решений — легче открыть действие.",
        "map_label": "помогает подготовить вход заранее",
    },
    {
        "id": "no_double_decision",
        "title": "🌱 Система дня",
        "text": "Это не тренировка.\nПросто идея, которую можно заметить или попробовать сегодня.\n\nЕсли ты уже решил что-то сделать, не устраивай повторное голосование в голове.\n\nСегодня просто замечай, сколько раз мозг пытается пересмотреть уже принятое решение.",
        "why": "Повторное решение тратит энергию до действия. Замечание этого момента помогает не начинать переговоры заново.",
        "map_label": "важно не принимать одно решение дважды",
    },
    {
        "id": "phone_not_guilty",
        "title": "🌱 Система дня",
        "text": "Это не тренировка.\nПросто идея, которую можно заметить или попробовать сегодня.\n\nЧасто проблема не в телефоне.\nПроблема в том, что он лежит ближе задачи.\n\nСегодня просто посмотри, что находится на расстоянии вытянутой руки.",
        "why": "Среда часто выигрывает у намерения. То, что ближе рукой, чаще становится следующим действием.",
        "map_label": "среда и расстояние до телефона сильно влияют на старт",
    },
    {
        "id": "visible_next_step_system",
        "title": "🌱 Система дня",
        "text": "Это не тренировка.\nПросто идея, которую можно заметить или попробовать сегодня.\n\nБольшая задача пугает.\nСледующий шаг — нет.\n\nСегодня попробуй оставить на виду только один следующий шаг.",
        "why": "Видимый следующий шаг превращает большую задачу в конкретный вход, к которому легче вернуться.",
        "map_label": "вам помогает видимый следующий шаг",
    },
    {
        "id": "body_doubling_system",
        "title": "🌱 Система дня",
        "text": "Это не тренировка.\nПросто идея, которую можно заметить или попробовать сегодня.\n\nНекоторым людям легче начинать, если рядом кто-то есть.\nСозвон. Коворкинг. Другой человек за столом.\n\nПросто проверь, есть ли разница.",
        "why": "Body doubling даёт внешний контур присутствия. Иногда этого достаточно, чтобы снизить порог входа.",
        "map_label": "похоже, полезен формат body doubling",
    },
    {
        "id": "rest_before_exhaustion",
        "title": "🌱 Система дня",
        "text": "Это не тренировка.\nПросто идея, которую можно заметить или попробовать сегодня.\n\nЕсли отдых начинается только после полного выгорания,\nон работает хуже.\n\nСегодня попробуй сделать паузу раньше, чем почувствуешь край.",
        "why": "Ранний отдых дешевле, чем восстановление после полного истощения. Это часть системы, не награда за идеальность.",
        "map_label": "отдых до истощения может снижать срывы",
    },
    {
        "id": "bad_draft_system",
        "title": "🌱 Система дня",
        "text": "Это не тренировка.\nПросто идея, которую можно заметить или попробовать сегодня.\n\nЧерновик существует, чтобы быть плохим.\nЕсли сразу требовать качество,\nмозг часто выбирает не начинать.",
        "why": "Плохой черновик снижает цену видимости и ошибки: сначала материал существует, качество позже.",
        "map_label": "плохой черновик снижает страх старта",
    },
    {
        "id": "one_tab_system",
        "title": "🌱 Система дня",
        "text": "Это не тренировка.\nПросто идея, которую можно заметить или попробовать сегодня.\n\nКаждое переключение стоит внимания.\nСегодня попробуй хотя бы один раз делать задачу только в одной вкладке.",
        "why": "Одна вкладка уменьшает цену возврата внимания и снижает шанс уйти в быстрые стимулы.",
        "map_label": "одна вкладка помогает удерживать внимание",
    },
    {
        "id": "task_visible",
        "title": "🌱 Система дня",
        "text": "Это не тренировка.\nПросто идея, которую можно заметить или попробовать сегодня.\n\nТо, чего не видно, мозг часто считает несуществующим.\n\nСегодня проверь:\nвидна ли тебе задача, которую ты хочешь сделать.",
        "why": "Видимость задачи работает как внешний сигнал: меньше нужно вспоминать, проще вернуться.",
        "map_label": "задачу важно держать на виду",
    },
]

# Backward-compatible name: this is now the System of Day layer, not a skill.
LONG_TERM_MICRO_HABITS = SYSTEMS_DAY

SYSTEM_PHRASES = [
"Мы сейчас тренируем не мотивацию, а возврат.",
"Система адаптируется по твоим действиям.",
"Срыв — это тоже данные.",
"Навык строится повторениями, а не идеальными днями.",
"Мозгу легче возвращаться туда, где вход маленький.",
"Мы не ломаем сопротивление силой. Мы уменьшаем трение.",
]

kb_micro_habit = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👍 Попробую"), KeyboardButton(text="🤷 Не моё")],
    ],
    resize_keyboard=True
)

kb_skip_data = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔁 Другой навык")],
        [KeyboardButton(text="🧩 Уменьшить шаг")],
        [KeyboardButton(text="🧭 Моя карта")],
        [KeyboardButton(text="🌙 На сегодня хватит")],
    ],
    resize_keyboard=True
)

kb_pay_choice = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💳 Продолжить полный режим")],
        [KeyboardButton(text="📚 Что входит")],
        [KeyboardButton(text="🧭 Показать карту")],
        [KeyboardButton(text="🤔 Остаться в коротком режиме")],
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
    '😮\u200d💨 Ты знаешь, ЧТО делать, но это не становится действием.\n\nПроблема не в силе воли.\nМы тренируем:\n— запуск\n— внимание\n— возврат после срыва\n\nМинимум — 60–120 секунд.\nСрыв — часть процесса.\n\n⚠️ Это не терапия и не медицинское заключение.\nВ кризис — жми «🆘 Мне небезопасно».',
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
            "Вход ломается.\n"
            "Чиним вход."
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
    """Trainer-specific details for the analysis Подробнее button."""
    if trainer_key == "beck":
        return (
            "Почему это работает?\n\n"
            "Когда задача воспринимается как слишком большая,\n"
            "мозг чаще выбирает быстрые награды.\n\n"
            "Поэтому уменьшение входа\n"
            "часто эффективнее увеличения мотивации.\n\n"
            "Что проверяем:\n"
            "если после меньшего шага начать легче,\n"
            "значит проблема не в мотивации,\n"
            "а во входе в задачу."
        )
    if trainer_key == "skinny":
        return (
            "Проблема: вход слишком дорогой.\n"
            "↓\n"
            "Что ломается: мозг уходит в быстрые дела.\n"
            "↓\n"
            "Что делать: уменьшаем вход и проверяем."
        )
    return (
        f"{name}, это не про то, что ты мало стараешься.\n\n"
        "Когда вход становится слишком тяжёлым,\n"
        "мозг ищет способ снизить напряжение.\n\n"
        "Мы не будем давить сильнее.\n"
        "Без давления.\n"
        "Сначала найдём маленький шаг,\n"
        "который можно сделать без стыда и перегруза."
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
        "Если продолжить, система будет не просто давать упражнения,\n"
        "а уточнять твою персональную модель: что помогает, где ломается вход, как возвращаться быстрее.\n\n"
        "Продолжение — 14.98 €/месяц."
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

ADULT_GAMIFICATION_POLICY = {
    "principle": "visualize_real_development_not_game",
    "forbidden": [
        "coins",
        "chests",
        "magic",
        "weapons",
        "pvp",
        "fantasy_characters",
        "game_currency",
    ],
    "allowed": [
        "development_levels",
        "action_series",
        "achievement_markers",
        "weekly_results",
        "monthly_changes",
        "growth_history",
    ],
}


def gamify_status_line(u: dict) -> str:
    """Adult progress line: no currencies, loot, RPG framing or game points."""
    lvl = int(u.get("level") or 1)
    streak = int(u.get("streak") or 0)
    done = int(u.get("done_count") or 0)
    ret = int(u.get("return_count") or 0)
    return (
        f"🏅 Уровень развития: {lvl}\n"
        f"📈 Серия действий: {streak}\n"
        f"✅ Запусков всего: {done}\n"
        f"↩️ Возвратов всего: {ret}"
    )


def progress_achievements_text(u: dict, profile: dict, weekly_counts: dict | None = None) -> str:
    weekly_counts = weekly_counts or {}
    achievements = []
    if int(u.get("done_count") or 0) > 0 or int(weekly_counts.get("done") or weekly_counts.get("action_done") or 0) > 0:
        achievements.append("первый запуск действия")
    if int(u.get("return_count") or 0) > 0 or int(weekly_counts.get("return") or 0) > 0:
        achievements.append("возврат после выпадения")
    if int((profile or {}).get("downscale_count") or 0) > 0:
        achievements.append("уменьшение шага вместо давления")
    if (profile or {}).get("preferred_activation") == "body_doubling":
        achievements.append("найден внешний формат запуска")
    if not achievements:
        achievements.append("базовая линия развития создана")
    return "\n".join(f"— {item}" for item in achievements[:5])


def growth_history_text(u: dict, profile: dict, weekly_counts: dict | None = None) -> str:
    weekly_counts = weekly_counts or {}
    done_week = int(weekly_counts.get("done") or weekly_counts.get("action_done") or 0)
    ret_week = int(weekly_counts.get("return") or 0)
    downscale_total = int((profile or {}).get("downscale_count") or 0)
    created = u.get("first_start_date") or u.get("created_at") or "старт"
    return (
        "История роста:\n"
        f"— точка старта: {created}\n"
        f"— за неделю: запусков {done_week}, возвратов {ret_week}\n"
        f"— за всё время: запусков {int(u.get('done_count') or 0)}, возвратов {int(u.get('return_count') or 0)}\n"
        f"— месячные изменения: копим историю; уже видно уменьшений шага {downscale_total}"
    )

# ============================================================
# AI SYSTEM PROMPTS
# ============================================================

AI_ANALYSIS_SYSTEM_PROMPT = """
Ты — AI-ассистент тренинга ADHD-навыков. Это НЕ психотерапия и НЕ медицинское заключение.

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
        "Ты — ассистент тренинга навыков саморегуляции. Это НЕ терапия, НЕ медицинское заключение.\n"
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
        "Не делай клинических выводов. Не говори про лечение. Без морали.\n"
    )
