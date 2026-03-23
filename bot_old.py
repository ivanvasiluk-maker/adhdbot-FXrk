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
from aiogram.types import Message, CallbackQuery, KeyboardButton
from aiogram.types import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode

from dotenv import load_dotenv
load_dotenv(override=True)

# Whisper (опционально; если нет — голос отключится)
# Импорт openai с таймаутом (может зависать при инициализации)
openai = None
def import_openai():
    global openai
    try:
        import openai as oa
        openai = oa
    except Exception as e:
        print(f"⚠️  OpenAI import failed: {e}")

# Пытаемся импортировать в отдельном потоке с таймаутом
import_thread = threading.Thread(target=import_openai, daemon=True)
import_thread.start()
import_thread.join(timeout=2.0)  # Ждём максимум 2 секунды

if openai is None:
    print("⚠️  OpenAI import timed out, continuing without AI features")
print("ENV BOT_TOKEN =", repr(os.getenv("BOT_TOKEN")))

# ============================================================
# 1) CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
print("DEBUG BOT_TOKEN =", repr(BOT_TOKEN))


# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini").strip()
OPENAI_WHISPER_MODEL = os.getenv("OPENAI_WHISPER_MODEL", "whisper-1").strip()

AI_ANALYSIS_ENABLED = bool(OPENAI_API_KEY)

# DB
DB_PATH = os.getenv("DB_PATH", "bot.db").strip()

# Оплата (Payment Link)
PAYMENT_URL = os.getenv("PAYMENT_URL", "").strip()
PAYMENT_URL_DISCOUNT = os.getenv("PAYMENT_URL_DISCOUNT", "").strip()
PAYMENT_URL_FULL = os.getenv("PAYMENT_URL_FULL", "").strip()

# Опционально: лог событий в Google Sheets (через Apps Script webhook)
SHEETS_WEBHOOK_URL = os.getenv("SHEETS_WEBHOOK_URL", "").strip()

# Геймификация (минимальная)
GAMIFY_ENABLED = True

# Logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

# OpenAI client
client = None
if AI_ANALYSIS_ENABLED and openai is not None:
    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        client = None
        AI_ANALYSIS_ENABLED = False

# ============================================================
# 2) TRAINERS (стили)
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

def trainer_say(trainer_key: str, text: str) -> str:
    t = TRAINERS.get(trainer_key, TRAINERS["marsha"])
    return f"{t['emoji']} *{t['name']}*: {text}"

# Crisis limit for non-paid users
CRISIS_LIMIT = 3

# Praise phrases per trainer
PRAISE = {
    "skinny": "Сделал. Факт есть. Это тренировка.",
    "marsha": "Это важно. Ты не бросил(а).",
    "beck": "Есть действие → есть обучение."
}

# ============================================================
# 2.6) TRAINER PRESENTATION & SELECTION
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

async def send_trainer_photo_if_any(chat_id: int, trainer_key: str):
    """Send trainer photo if a matching file exists in ./images.
    Looks for images/{trainer_key}.(png|jpg|jpeg|webp) and sends first found.
    """
    try:
        from aiogram.types import InputFile
        # prefer files next to this script
        base = os.path.join(os.path.dirname(__file__), "images")
        
        # Попробуем найти файл
        for ext in ("jpg", "jpeg", "png", "webp"):
            p = os.path.join(base, f"{trainer_key}.{ext}")
            if os.path.exists(p):
                try:
                    # create a short-lived Bot to send photo (safe if main bot not exposed)
                    b = Bot(token=BOT_TOKEN)
                    try:
                        log.info(f"Sending trainer photo: {p} to chat {chat_id}")
                        await b.send_photo(chat_id, InputFile(p))
                        log.info(f"Trainer photo sent successfully: {trainer_key}")
                    finally:
                        await b.session.close()
                    return
                except Exception as e:
                    log.error(f"Error sending photo {p}: {e}")
                    continue
        
        log.warning(f"No trainer photo found for {trainer_key} in {base}")
    except Exception as e:
        log.error(f"Error in send_trainer_photo_if_any: {e}")
        return

async def send_trainer_introduction(m: Message, u: dict):
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
# 2.5) TEST QUESTIONS (для быстрого узнавания bucket)
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

def resolve_bucket_from_test(answers: list[str]) -> str:
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

# ============================================================
# 3) KEYBOARDS
# ============================================================

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
        [KeyboardButton(text="✅ Сделал(а)"), KeyboardButton(text="↩️ Вернулся(лась)")],
        [KeyboardButton(text="🆘 Кризис"), KeyboardButton(text="📊 Мой прогресс")],
        [KeyboardButton(text="❓ Сомневаюсь, работает ли")],
        [KeyboardButton(text="ℹ️ Подробнее про навык"), KeyboardButton(text="🔁 Заменить навык")],
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
        [KeyboardButton(text="Ты меня не понял")],
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
        [KeyboardButton(text="✅ Да, в точку")],
        [KeyboardButton(text="🤔 Немного не так")],
    ],
    resize_keyboard=True
)

kb_pay_choice = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💳 Оплатить со скидкой")],
        [KeyboardButton(text="💳 Оплатить без скидки")],
        [KeyboardButton(text="➕ Ещё 4 дня без оплаты")],
        [KeyboardButton(text="❌ Не готов(а)")],
    ],
    resize_keyboard=True
)

def payment_inline_discount() -> InlineKeyboardMarkup:
    if not PAYMENT_URL_DISCOUNT:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Скидка: ссылка не настроена", callback_data="noop")]]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить со скидкой", url=PAYMENT_URL_DISCOUNT)]]
    )

def payment_inline_full() -> InlineKeyboardMarkup:
    if not PAYMENT_URL_FULL:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Полная: ссылка не настроена", callback_data="noop")]]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить без скидки", url=PAYMENT_URL_FULL)]]
    )

kb_yes_no_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data="yes")],
        [InlineKeyboardButton(text="❌ Нет", callback_data="no")],
    ]
)

def payment_inline() -> InlineKeyboardMarkup:
    if not PAYMENT_URL:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Ссылка на оплату не настроена", callback_data="noop")]]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить", url=PAYMENT_URL)]]
    )

# Onboarding screens shown during quick onboarding
ONBOARDING_SCREENS = [
    (
        "😮‍💨 Ты уже, скорее всего, всё пробовал(а).\n\n"
        "📋 Трекеры\n"
        "🧩 Микрошаги\n"
        "🗣 Советы «просто начни»\n"
        "🧠 Даже терапию — возможно\n\n"
        "Ты знаешь, ЧТО делать.\n"
        "Но это не становится действием."
    ),
    (
        "⚠️ Проблема не в знаниях.\n"
        "И не в силе воли.\n\n"
        "Навыки не работают,\n"
        "если их не тренируют системно.\n\n"
        "🧠 Навыки × тренировки × время = эффект"
    ),
    (
        "Здесь не будет:\n"
        "❌ трекеров «сделал / не сделал»\n"
        "❌ мотивации, которая сдувается\n"
        "❌ разговоров без действий\n\n"
        "Здесь будет:\n"
        "✅ тренировка психических функций\n"
        "✅ пошагово, с поддержкой\n"
        "✅ даже без мотивации\n\n"
        "⚠️ Это не терапия.\n"
        "Мы тренируем навыки."
    ),
]

# ============================================================
# 4) DB: schema + CRUD
# ============================================================

USER_FIELDS = [
    "user_id",
    "chat_id",
    "name",
    "trainer_key",
    "input_mode",
    "stage",
    "bucket",
    "analysis_json",
    "plan_json",
    "day",
    "created_at",
    "points",
    "level",
    "streak",
    "last_active",
    "plan_overrides_json",
    "trial_days",
    "trial_phase",
    "pending_plan_change",
    "crisis_count",
    "test_answers",
    "done_count",
    "return_count",
    "analysis_retry_count",
    "has_started_training",
]

def default_user(uid: int) -> Dict[str, Any]:
    return {
        "user_id": uid,
        "chat_id": uid,
        "name": None,
        "trainer_key": "marsha",
        "input_mode": "text",   # text | voice | test
        "stage": "start",
        "bucket": "mixed",
        "analysis_json": None,
        "plan_json": None,
        "day": 1,
        "points": 0,
        "level": 1,
        "streak": 0,
        "last_active": 0.0,
        "plan_overrides_json": None,
        "trial_days": 3,
        "trial_phase": "trial3",
        "pending_plan_change": None,
        "crisis_count": 0,
        "created_at": time.time(),
        "test_answers": [],  # Временное хранилище для ответов теста
        "done_count": 0,
        "return_count": 0,
        "analysis_retry_count": 0,
        "has_started_training": 0,  # Флаг: 1 если юзер начал день 1
    }

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER,
                name TEXT,
                trainer_key TEXT,
                input_mode TEXT,
                stage TEXT,
                bucket TEXT,
                analysis_json TEXT,
                plan_json TEXT,
                day INTEGER,
                created_at REAL,
                points INTEGER,
                level INTEGER,
                streak INTEGER,
                last_active REAL,
                plan_overrides_json TEXT,
                trial_days INTEGER,
                trial_phase TEXT,
                pending_plan_change TEXT,
                crisis_count INTEGER,
                test_answers TEXT,
                done_count INTEGER,
                return_count INTEGER,
                has_started_training INTEGER
            )
            """
        )
        await db.commit()

async def get_user(uid: int) -> Dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row:
            u = default_user(uid)
            await save_user(u)
            return u

        cols = [description[0] for description in cur.description] if cur.description else []
        if cols:
            u = dict(zip(cols, row))
        else:
            u = dict(row) if hasattr(row, 'keys') else {}
        
        # Deserialize test_answers if stored as JSON string
        if 'test_answers' in u and u.get('test_answers'):
            try:
                u['test_answers'] = json.loads(u['test_answers']) if isinstance(u['test_answers'], str) else u['test_answers']
            except Exception:
                u['test_answers'] = []
        else:
            u['test_answers'] = []
        return u

async def save_user(u: Dict[str, Any]):
    cols = USER_FIELDS
    vals = []
    for c in cols:
        v = u.get(c)
        # Serialize lists/dicts to JSON for storage
        if isinstance(v, (list, dict)):
            try:
                v = json.dumps(v, ensure_ascii=False)
            except Exception:
                v = None
        vals.append(v)
    placeholders = ",".join(["?"] * len(cols))
    cols_sql = ",".join(cols)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"INSERT OR REPLACE INTO users ({cols_sql}) VALUES ({placeholders})",
            tuple(vals),
        )
        await db.commit()

# ============================================================
# DB MIGRATION + EVENTS (аналитика) + GAMIFY FIELDS
# ============================================================

EXTRA_USER_COLS = {
    "points": "INTEGER",
    "level": "INTEGER",
    "streak": "INTEGER",
    "last_active": "REAL",
    "plan_overrides_json": "TEXT",   # правки плана после кризиса
    "trial_days": "INTEGER",         # 3 или 7
    "trial_phase": "TEXT",           # "trial3" / "trial7" / "paid" / ...
    "pending_plan_change": "TEXT",   # отложенная правка плана после кризиса
    "crisis_count": "INTEGER",       # лимит в trial
    "test_answers": "TEXT",
    "done_count": "INTEGER",
    "return_count": "INTEGER",
    "analysis_retry_count": "INTEGER",  # сколько раз пользователь сказал "ты меня не понял"
    "has_started_training": "INTEGER"  # 1 если юзер начал день 1
}

async def migrate_db():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("PRAGMA table_info(users)")
        cols = [r[1] for r in await cur.fetchall()]

        for col, ctype in EXTRA_USER_COLS.items():
            if col not in cols:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {ctype}")

        await db.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            user_id INTEGER,
            stage TEXT,
            event TEXT,
            meta TEXT
        )
        """)
        await db.commit()

async def log_event(user_id: int, stage: str, event: str, meta: dict | None = None):
    meta_s = json.dumps(meta or {}, ensure_ascii=False)
    ts = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO events(ts,user_id,stage,event,meta) VALUES(?,?,?,?,?)",
            (ts, user_id, stage, event, meta_s)
        )
        await db.commit()

    if SHEETS_WEBHOOK_URL:
        try:
            import urllib.request
            payload = json.dumps({
                "ts": ts,
                "user_id": user_id,
                "stage": stage,
                "event": event,
                "meta": meta or {}
            }, ensure_ascii=False).encode("utf-8")

            req = urllib.request.Request(
                SHEETS_WEBHOOK_URL,
                data=payload,
                headers={"Content-Type":"application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=3).read()
        except Exception:
            pass

def gamify_apply(u: dict, delta_points: int, reason: str):
    if not GAMIFY_ENABLED:
        return
    u["points"] = int(u.get("points") or 0) + int(delta_points)
    u["level"] = max(1, int(u.get("points") or 0) // 10 + 1)

    now = time.time()
    last = float(u.get("last_active") or 0.0)
    if now - last > 18 * 3600:
        u["streak"] = 1
    else:
        u["streak"] = int(u.get("streak") or 0) + 1
    u["last_active"] = now
    # reason можно логировать отдельно


# ============================================================
# 5) UTIL: JSON extract + safe text
# ============================================================

def _extract_json(text: str) -> Optional[dict]:
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

def clamp_str(s: str, n: int = 1400) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."


def should_ping(u: dict, hours: int) -> bool:
    return time.time() - (u.get("last_active") or 0) > hours * 3600

# ============================================================
# 6) SKILLS DB (Product doc in code)
#    (4 недели) — DBT / CBT / ACT / самокритика / тревога
# ============================================================

# ============================================================
# SKILLS_DB (4 недели) — DBT / CBT / ACT / самокритика / тревога
# ============================================================

SKILLS_DB = {
    # ---------- WEEK 1: Стабилизация и возврат ----------
    "w1_return_no_punish": {
        "week": 1, "domain": ["DBT","CFT"], "bucket": ["anxiety","low_energy","distractibility","mixed"],
        "name": "Возврат без наказания",
        "goal": "Разорвать связку «срыв → самокритика → отказ»",
        "how": "Заметил(а) срыв → фраза-якорь «Я возвращаюсь — это и есть навык» → шаг ≤ 2 минут.",
        "minimum": "Произнеси фразу-якорь. Даже без шага.",
        "use_when": "После срыва, прокрастинации, отказа продолжать.",
        "sabotage": ["уже поздно", "я всё испортил(а)", "нет смысла начинать заново"],
        "tags": ["return","shame","stability"],
        "steps": [
            "Заметь, что остановился(лась)",
            "Скажи себе: 'Я возвращаюсь — это и есть навык'",
            "Сделай первый шаг ≤ 2 минут"
        ],
        "micro": "Произнеси фразу и сделай один шаг",
        "why": "Навык разрывает цикл самокритики и возвращает контроль",
        "traps": ["уже поздно", "надо делать идеально"]
    },
    "w1_micro_start": {
        "week": 1, "domain": ["CBT","BA"], "bucket": ["low_energy","mixed","anxiety","distractibility"],
        "name": "Микро-старт",
        "goal": "Запуск действия без мотивации",
        "how": "Выбери действие ≤ 2 минут. Критерий успеха: начал(а), не закончил(а).",
        "minimum": "Открой файл / встань / возьми предмет.",
        "use_when": "Когда «не хочется вообще» или «начну потом».",
        "sabotage": ["слишком мало", "бессмысленно", "надо сделать идеально"],
        "tags": ["activation","start"],
        "steps": [
            "Выбери действие ≤ 2 минут",
            "Скажи себе: 'я не заканчиваю — я начинаю'",
            "Сделай первый физический шаг"
        ],
        "micro": "Открой файл / встань / возьми предмет",
        "why": "Навык обходит блок мотивации и снижает страх старта",
        "traps": ["слишком мало", "надо сразу нормально"]
    },
    "w1_energy_base": {
        "week": 1, "domain": ["DBT"], "bucket": ["low_energy","mixed"],
        "name": "База восстановления",
        "goal": "Вернуть минимальный ресурс для тренировки",
        "how": "Выбери ОДНО: вода/еда/душ/3–5 минут воздуха/10 минут без экрана.",
        "minimum": "1 глоток воды / 1 глубокий вдох.",
        "use_when": "Когда тело выключено, пусто, тяжесть.",
        "sabotage": ["слишком просто", "не поможет", "потом"],
        "tags": ["energy","restore"],
        "steps": [
            "Выбери одно из: вода/еда/душ/3–5 минут воздуха/10 минут без экрана",
            "Сделай минимум: 1 глоток/1 вдох/1 короткое действие",
            "Вернись к задаче или отдохни 5 минут"
        ],
        "micro": "1 глоток воды / 1 глубокий вдох",
        "why": "Восстановление базовых ресурсов позволяет тренировать навыки",
        "traps": ["это слишком просто", "не помогает сразу"]
    },

    # ---------- WEEK 2: Тревога и мысли ----------
    "w2_notice_thought": {
        "week": 2, "domain": ["ACT","CBT"], "bucket": ["anxiety","mixed"],
        "name": "Замечать мысль (дефузия)",
        "goal": "Снизить власть тревожных мыслей без борьбы",
        "how": "Поймал(а) мысль → назвал(а) «мысль о …» → вернулся(лась) к делу на 60–120 сек.",
        "minimum": "Просто назвать мысль 1 раз.",
        "use_when": "Когда тревога мешает начинать/продолжать.",
        "sabotage": ["слишком много мыслей", "мысли правдивые", "не могу выключить голову"],
        "tags": ["anxiety","thoughts","act"],
        "steps": [
            "Заметь мысль",
            "Назови: 'мысль о ...'",
            "Вернись к делу на 60–120 сек"
        ],
        "micro": "Назови мысль 1 раз",
        "why": "Отделение мысли от факта снижает её влияние",
        "traps": ["мысли правдивы", "их слишком много"]
    },
    "w2_action_experiment": {
        "week": 2, "domain": ["CBT","ACT"], "bucket": ["anxiety","mixed"],
        "name": "Проверка мысли действием",
        "goal": "Выйти из анализа в опыт",
        "how": "Сделай микро-действие ≤ 3 минут вопреки тревоге. Отметь, что случилось реально.",
        "minimum": "1 попытка 30–60 сек.",
        "use_when": "«Я не справлюсь», «позорно», «слишком сложно».",
        "sabotage": ["надо сначала понять", "сначала подготовиться идеально"],
        "tags": ["experiment","cbt"],
        "steps": [
            "Выбери микро-действие ≤ 3 минут",
            "Сделай его вопреки тревоге",
            "Отметь, что произошло — факты"
        ],
        "micro": "Попробуй 30–60 сек",
        "why": "Эксперимент разрушает предсказания страха и даёт данные",
        "traps": ["надо понять сначала", "готовиться идеально"]
    },

    # ---------- WEEK 3: Самокритика и стыд ----------
    "w3_separate_critic": {
        "week": 3, "domain": ["CFT","CBT"], "bucket": ["anxiety","low_energy","mixed","distractibility"],
        "name": "Отделить критика",
        "goal": "Не принимать самокритику за истину",
        "how": "Фраза критика → метка «это голос критика» → короткий ответ как тренер другу.",
        "minimum": "Только метка «критик».",
        "use_when": "Стыд, самоунижение, «я тупой/ленивый».",
        "sabotage": ["это правда", "если не ругать — расслаблюсь"],
        "tags": ["selfcrit","shame"],
        "steps": [
            "Заметь голос критика",
            "Скажи: 'это голос критика'",
            "Ответь себе как другу — коротко и поддерживающе"
        ],
        "micro": "Просто пометь: 'критик'",
        "why": "Отделение голоса критика уменьшает его власть",
        "traps": ["это правда", "если не критиковать — расслаблюсь"]
    },
    "w3_support_instead_punish": {
        "week": 3, "domain": ["CFT"], "bucket": ["anxiety","low_energy","mixed"],
        "name": "Поддержка вместо наказания",
        "goal": "Вернуть энергию после ошибки",
        "how": "Спроси: «Как бы я поддержал близкого?» → 1 тёплая фраза → 1 микро-шаг.",
        "minimum": "Одна фраза поддержки.",
        "use_when": "После провала, отката, конфликтов.",
        "sabotage": ["это слабость", "я не заслужил"],
        "tags": ["support","motivation"],
        "steps": [
            "Спроси: 'Как бы я поддержал(а) близкого?'",
            "Сформулируй 1 тёплую фразу",
            "Сделай один микро-шаг для восстановления"
        ],
        "micro": "Одна фраза поддержки",
        "why": "Поддержка восстанавливает энергию и снижает самонаказание",
        "traps": ["это слабость", "я не заслужил"]
    },

    # ---------- WEEK 4: Удержание и система ----------
    "w4_soft_attention_return": {
        "week": 4, "domain": ["ACT"], "bucket": ["distractibility","mixed"],
        "name": "Мягкий возврат внимания",
        "goal": "Отвлекаемость без войны",
        "how": "Заметил(а) отвлечение → метка «ушёл» → вернулся(лась) на 2 минуты.",
        "minimum": "Один возврат на 30 сек.",
        "use_when": "Скачки внимания, соцсети, вкладки, импульсы.",
        "sabotage": ["меня постоянно выносит", "я неспособен"],
        "tags": ["focus","return"],
        "steps": [
            "Заметь отвлечение",
            "Скажи: 'ушёл'",
            "Вернись на 2 минуты"
        ],
        "micro": "Вернись на 30 сек",
        "why": "Короткие возвраты укрепляют внимание без борьбы",
        "traps": ["это не помогает", "слишком часто отвлекаюсь"]
    },
    "w4_env_shield": {
        "week": 4, "domain": ["CBT"], "bucket": ["distractibility","mixed","anxiety"],
        "name": "Щит окружения",
        "goal": "Снизить нагрузку на волю",
        "how": "На 20 минут убрать 1 стимул: уведомления/телефон/вкладку/шум.",
        "minimum": "Убрать 1 стимул на 5 минут.",
        "use_when": "Когда срывает на стимулы.",
        "sabotage": ["мне нужно быть на связи", "я всё равно отвлекусь"],
        "tags": ["environment","control"],
        "steps": [
            "Выбери один стимул, который мешает",
            "Убери его на 20 минут",
            "Проверь, как изменилось внимание"
        ],
        "micro": "Убрать 1 уведомление / одну вкладку",
        "why": "Снижение стимулов уменьшает нагрузку на волю",
        "traps": ["мне нужно быть на связи", "я всё равно отвлекусь"]
    },
    "w4_weekly_review": {
        "week": 4, "domain": ["META"], "bucket": ["anxiety","low_energy","distractibility","mixed"],
        "name": "Еженедельный разбор прогресса",
        "goal": "Видеть прогресс и не сдаваться",
        "how": "3 вопроса: что сработало? где срыв? какой 1 навык оставляем на неделю?",
        "minimum": "Ответить на 1 из 3 вопросов.",
        "use_when": "Раз в неделю.",
        "sabotage": ["не хочу смотреть", "там всё плохо"],
        "tags": ["review","progress"],
        "steps": [
            "Ответь: что сработало?",
            "Определи где были срывы",
            "Выбери 1 навык на следующую неделю"
        ],
        "micro": "Ответить на 1 вопрос",
        "why": "Обзор помогает закрепить прогресс и сделать план реалистичным",
        "traps": ["не хочу смотреть", "мало прогресса"]
    },
}

# ============================================================
# 4-недельные шаблоны по bucket (28 дней)
# ИИ может предлагать замены, но ТОЛЬКО из SKILLS_DB.
# ============================================================

PROGRAM_TEMPLATES = {
    "anxiety": {
        1: ["w1_return_no_punish","w1_micro_start","w1_return_no_punish","w1_energy_base","w1_micro_start","w1_return_no_punish","w1_energy_base"],
        2: ["w2_notice_thought","w2_action_experiment","w2_notice_thought","w2_action_experiment","w2_notice_thought","w2_action_experiment","w1_return_no_punish"],
        3: ["w3_separate_critic","w3_support_instead_punish","w3_separate_critic","w3_support_instead_punish","w2_notice_thought","w1_micro_start","w1_return_no_punish"],
        4: ["w4_env_shield","w4_soft_attention_return","w2_notice_thought","w1_micro_start","w3_support_instead_punish","w4_weekly_review","w1_return_no_punish"],
    },
    "low_energy": {
        1: ["w1_energy_base","w1_micro_start","w1_energy_base","w1_return_no_punish","w1_micro_start","w1_energy_base","w1_return_no_punish"],
        2: ["w1_micro_start","w1_energy_base","w1_micro_start","w1_return_no_punish","w3_support_instead_punish","w1_energy_base","w1_return_no_punish"],
        3: ["w3_separate_critic","w3_support_instead_punish","w1_micro_start","w1_energy_base","w1_return_no_punish","w3_support_instead_punish","w1_return_no_punish"],
        4: ["w4_env_shield","w4_soft_attention_return","w1_micro_start","w1_energy_base","w3_support_instead_punish","w4_weekly_review","w1_return_no_punish"],
    },
    "distractibility": {
        1: ["w1_micro_start","w1_return_no_punish","w1_micro_start","w1_energy_base","w1_return_no_punish","w1_micro_start","w1_energy_base"],
        2: ["w4_soft_attention_return","w4_env_shield","w4_soft_attention_return","w4_env_shield","w1_return_no_punish","w1_micro_start","w1_return_no_punish"],
        3: ["w3_separate_critic","w1_return_no_punish","w4_env_shield","w4_soft_attention_return","w3_support_instead_punish","w1_micro_start","w1_return_no_punish"],
        4: ["w4_env_shield","w4_soft_attention_return","w1_micro_start","w2_notice_thought","w3_support_instead_punish","w4_weekly_review","w1_return_no_punish"],
    },
    "mixed": {
        1: ["w1_return_no_punish","w1_micro_start","w1_energy_base","w1_return_no_punish","w1_micro_start","w1_energy_base","w1_return_no_punish"],
        2: ["w2_notice_thought","w4_env_shield","w2_action_experiment","w4_soft_attention_return","w1_return_no_punish","w1_micro_start","w1_return_no_punish"],
        3: ["w3_separate_critic","w3_support_instead_punish","w2_notice_thought","w1_micro_start","w1_return_no_punish","w4_env_shield","w1_return_no_punish"],
        4: ["w4_env_shield","w4_soft_attention_return","w2_notice_thought","w1_micro_start","w3_support_instead_punish","w4_weekly_review","w1_return_no_punish"],
    }
}

def build_28_day_plan(bucket: str) -> list[str]:
    b = bucket if bucket in PROGRAM_TEMPLATES else "mixed"
    days: list[str] = []
    for wk in (1, 2, 3, 4):
        days.extend(PROGRAM_TEMPLATES[b][wk])
    return days

# ============================================================
# 7) PLANS (MVP fallback) — 3 days
# ============================================================

PLANS = {
    "anxiety": ["notice_thought", "micro_start", "return_no_punish"],
    "low_energy": ["micro_start", "return_no_punish", "micro_start"],
    "distractibility": ["micro_start", "micro_start", "return_no_punish"],
    "mixed": ["return_no_punish", "micro_start", "notice_thought"],
}

# ============================================================
# 7) SALES & ONBOARDING TEXTS (карта, гарантия, таймеры)
# ============================================================

# 🗺 МЕСЯЧНАЯ КАРТА ТРЕНИРОВКИ

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
# 7.5) АНАЛИЗ → КОНТРАКТ → ПУТЬ (финальный продающий текст)
# ============================================================

def analysis_contract_short(name: str, trainer_key: str, bucket: str) -> str:
    """Короткая версия контракта (показывается всегда после анализа)"""
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
        "Но не за один день."
    )

def analysis_contract_long(name: str, trainer_key: str, bucket: str) -> str:
    """Подробная версия контракта (по кнопке «Подробнее»)"""
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
        "5️⃣ Сроки\n"
        "Первые изменения — через 2–3 недели.\n"
        "Устойчивость — 4–8 недель.\n\n"
        f"{trainer_finish}\n\n"
        "Мы будем идти шаг за шагом."
    )

def month_map_text(bucket: str) -> str:
    """Карта тренировки на месяц (показывается сразу после анализа)"""
    return (
        "🗺 Твой маршрут на месяц:\n\n"
        "Неделя 1 — стабилизация\n"
        "• возврат без самонаказания\n"
        "• микро-старт\n\n"
        "Неделя 2 — работа с тревогой / вниманием\n"
        "• замечать мысли\n"
        "• возвращаться в действие\n\n"
        "Неделя 3 — самокритика\n"
        "• снижать давление\n"
        "• поддерживать себя\n\n"
        "Неделя 4 — закрепление\n"
        "• удержание навыков\n"
        "• адаптация под тебя\n\n"
        "Это не марафон.\n"
        "Это тренировка."
    )

def guarantee_block(trainer_key: str) -> str:
    """Гарантийный блок перед днём 1 (по стилю тренера)"""
    return {
        "marsha": (
            "🤍 Гарантия:\n"
            "Если тебе будет тяжело — мы замедлимся.\n"
            "Если сорвёшься — это часть пути, а не провал.\n"
            "Я рядом."
        ),
        "skinny": (
            "🛡 Гарантия:\n"
            "Ты можешь срываться.\n"
            "Ты не можешь сдаться.\n"
            "Я не отпущу процесс."
        ),
        "beck": (
            "📘 Гарантия:\n"
            "Программа адаптируется по данным.\n"
            "Если метод не работает — он меняется.\n"
            "Это встроено."
        ),
    }.get(trainer_key, "Я буду рядом. Процесс гарантирован.")

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

# ============================================================
# ANALYSIS FUNNEL: CONTRACT → MAP → GUARANTEE
# ============================================================

def analysis_contract_short(name: str, trainer_key: str, bucket: str) -> str:
    """Краткий контракт (продающий блок)"""
    style_line = {
        "skinny": "Будет конкретика. Будет действие.",
        "marsha": "Будет поддержка. Без давления.",
        "beck": "Будет структура и объяснение механики."
    }.get(trainer_key, "")

    bucket_text = {
        "anxiety": "Тревожный тип избегания.",
        "low_energy": "Тип сопротивления и истощения.",
        "distractibility": "Тип отвлекаемости.",
        "mixed": "Смешанный тип прокрастинации."
    }.get(bucket, "Смешанный тип.")

    return (
        f"🧠 {name}, вот что происходит.\n\n"
        f"{bucket_text}\n\n"
        f"{style_line}\n\n"
        "Мы не будем просто давать упражнения.\n"
        "Мы будем перестраивать твою систему саморегуляции.\n\n"
        "Это займёт 4 недели.\n"
        "И первые сдвиги обычно появляются через 2–3 недели.\n\n"
        "Готов идти по системе?"
    )


def month_map_text(bucket: str) -> str:
    """Текст карты месяца"""
    return (
        "🗺 КАРТА 4 НЕДЕЛЬ\n\n"
        "1️⃣ Стабилизация — возврат и запуск\n"
        "2️⃣ Работа с тревогой / вниманием\n"
        "3️⃣ Работа с самокритикой\n"
        "4️⃣ Удержание системы\n\n"
        "Это не случайные упражнения.\n"
        "Это последовательная перестройка.\n"
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


# ============================================================
# 8) AI: analyze -> bucket + explanation + offer
# ============================================================

# COMPREHENSIVE AI ANALYSIS SYSTEM PROMPT (для глубокого разбора)
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

async def ai_analyze_comprehensive(user_text: str, trainer_key: str = "marsha") -> dict:
    """
    Comprehensive AI analysis that returns full JSON contract.
    Returns the same structure as expected response from spec.
    """
    if not (AI_ANALYSIS_ENABLED and client):
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
        model=OPENAI_CHAT_MODEL,
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

async def ai_analyze(user_text: str) -> dict:
    if not (AI_ANALYSIS_ENABLED and client):
        return {
            "bucket": "mixed",
            "summary": "Похоже на смешанный профиль: немного тревоги + избегание + низкий ресурс.",
            "confidence": 0.55,
            "top_signals": ["избегание", "тревога", "низкая энергия"],
            "first_action": "Сделай один микро-старт ≤ 2 минут."
        }

    system = build_ai_system_prompt()
    resp = client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
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


def skills_for_bucket(bucket: str) -> list[dict]:
    # вернём навыки, подходящие bucket + универсальные
    out: list[dict] = []
    for sid, s in SKILLS_DB.items():
        if bucket in s.get("bucket", []) or "mixed" in s.get("bucket", []):
            out.append({"id": sid, **s})
    return out

def is_paid(u: dict) -> bool:
    return u.get("trial_phase") == "paid"

async def ai_crisis_help(trainer_key: str, bucket: str, user_text: str) -> dict:
    """
    Возвращает JSON:
    {
      "support": "...короткая поддержка/переориентация...",
      "skill_id": "...из SKILLS_DB...",
      "why_this": "...почему этот навык сейчас...",
      "micro_step": "...конкретный шаг на 60–120 сек...",
      "plan_change": {"day_offset": 1, "replace_with": "skill_id"} | null
    }
    """
    if not (AI_ANALYSIS_ENABLED and client):
        # fallback
        return {
            "support": "Ок. Сейчас не обсуждаем жизнь целиком. Берём один шаг, который можно сделать прямо сейчас.",
            "skill_id": "w1_return_no_punish",
            "why_this": "Ключ — вернуть контроль через возврат без самонаказания.",
            "micro_step": "Скажи «Я возвращаюсь — это и есть навык» и сделай один шаг ≤ 2 минут.",
            "plan_change": None
        }

    allowed_ids = list(SKILLS_DB.keys())
    system = (
        "Ты — кризисный помощник тренинга навыков саморегуляции.\n"
        "Это НЕ терапия и НЕ диагноз. Нельзя обещать лечение.\n"
        "Твоя цель: быстро стабилизировать и вернуть человека в действие.\n\n"
        "Выбирай skill_id ТОЛЬКО из списка allowed_ids.\n"
        "Ответ строго JSON, без текста вокруг.\n"
    )
    user = json.dumps({
        "trainer_style": trainer_key,
        "bucket": bucket,
        "user_text": user_text,
        "allowed_ids": allowed_ids
    }, ensure_ascii=False)

    resp = client.chat.completions.create(
        model=OPENAI_CHAT_MODEL,
        messages=[{"role":"system","content":system},{"role":"user","content":user}],
        temperature=0.3,
    )
    data = _extract_json(resp.choices[0].message.content or "") or {}
    sid = data.get("skill_id")
    if sid not in SKILLS_DB:
        sid = "w1_return_no_punish"
    pc = data.get("plan_change")
    if pc:
        rid = pc.get("replace_with")
        if rid not in SKILLS_DB:
            pc = None
    return {
        "support": (data.get("support") or "").strip(),
        "skill_id": sid,
        "why_this": (data.get("why_this") or "").strip(),
        "micro_step": (data.get("micro_step") or "").strip(),
        "plan_change": pc
    }


def get_current_plan(u: dict) -> list[str]:
    # основной план
    bucket = u.get("bucket") or "mixed"
    base = json.loads(u.get("plan_json") or "[]")
    if not base:
        base = build_28_day_plan(bucket)

    # применяем правки
    overrides = json.loads(u.get("plan_overrides_json") or "{}") if u.get("plan_overrides_json") else {}
    for k, sid in overrides.items():
        try:
            day_idx = int(k) - 1
            if 0 <= day_idx < len(base) and sid in SKILLS_DB:
                base[day_idx] = sid
        except Exception:
            continue
    return base

def propose_plan_override(u: dict, day_number: int, new_skill_id: str):
    if new_skill_id not in SKILLS_DB:
        return
    overrides = json.loads(u.get("plan_overrides_json") or "{}") if u.get("plan_overrides_json") else {}
    overrides[str(day_number)] = new_skill_id
    u["plan_overrides_json"] = json.dumps(overrides, ensure_ascii=False)

def build_plan(bucket: str) -> List[str]:
    return PLANS.get(bucket, PLANS["mixed"])

# ============================================================
# 9) DAY 1 — scripts
# ============================================================


# ============================================================
# Day 2–7 (и далее) — единый шаблон тренировочного дня
# ============================================================

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

async def start_day(m: Message, u: dict, day: int):
    plan = get_current_plan(u)
    if day < 1:
        day = 1
    if day > len(plan):
        day = len(plan)

    sid = plan[day - 1]
    skill = SKILLS_DB[sid]

    u["day"] = day
    u["stage"] = "training"
    
    # Log day 1 started and set training flag
    if day == 1:
        u["has_started_training"] = 1
        await log_event(u["user_id"], "training", "day1_started", {})
    
    await save_user(u)

    await m.answer(
        trainer_say(
            u.get("trainer_key") or "marsha",
            day_task_text(u.get("name") or "друг", u.get("trainer_key") or "marsha", day, skill)
        ),
        reply_markup=kb_training_main
    )

    line = random.choice(DAILY_LIVE_LINES[u.get("trainer_key") or "marsha"])
    await m.answer(trainer_say(u.get("trainer_key") or "marsha", line))

    await m.answer(midday_ping(u.get("name") or "друг", u.get("trainer_key") or "marsha"))

async def start_day1(m: Message, u: Dict[str, Any]):
    name = u.get("name") or "друг"
    trainer_key = u.get("trainer_key") or "marsha"

    plan_ids = json.loads(u.get("plan_json") or "[]")
    if not plan_ids:
        plan_ids = build_plan(u.get("bucket") or "mixed")
        u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
        await save_user(u)

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

async def start_day_simple(m: Message, u: Dict[str, Any], day: int):
    name = u.get("name") or "друг"
    trainer_key = u.get("trainer_key") or "marsha"

    plan_ids = json.loads(u.get("plan_json") or "[]")
    if not plan_ids:
        plan_ids = build_plan(u.get("bucket") or "mixed")
        u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
        await save_user(u)

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

async def advance_day(m: Message, u: Dict[str, Any], next_day: int):
    u["day"] = next_day
    await save_user(u)
    await start_day_simple(m, u, next_day)

# ============================================================
# 10) Whisper: transcribe voice to text
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

        # openai python sdk expects file-like
        import io
        bio = io.BytesIO(data)
        bio.name = "voice.ogg"

        tr = client.audio.transcriptions.create(
            model=OPENAI_WHISPER_MODEL,
            file=bio,
        )
        text = getattr(tr, "text", None)
        if not text:
            # newer format may return dict
            try:
                text = tr["text"]
            except Exception:
                text = None
        return (text or "").strip() or None
    except Exception as e:
        log.exception("whisper error: %s", e)
        return None

# ============================================================
# 11) ROUTER + HANDLERS
# ============================================================

router = Router()

@router.message(CommandStart())
async def cmd_start(m: Message):
    uid = m.from_user.id
    u = await get_user(uid)

    u["chat_id"] = m.chat.id
    
    # Проверяем: уже ли начал тренировку
    if u.get("has_started_training"):
        # Юзер уже в процессе тренировки, не показываем онбординг
        trainer_key = u.get("trainer_key") or "marsha"
        current_day = u.get("day") or 1
        
        # Получаем текущий навык
        plan = get_current_plan(u)
        idx = max(0, min(len(plan) - 1, current_day - 1))
        sid = plan[idx]
        skill = SKILLS_DB.get(sid, {})
        
        # Ответ: продолжаем с текущего дня
        msg = (
            f"Ты уже в тренировке, {u.get('name') or 'друг'}! 💪\n\n"
            f"День {current_day}\n"
            f"🧩 {skill.get('name', 'Навык')}\n\n"
            "Продолжаем?"
        )
        await m.answer(
            trainer_say(trainer_key, msg),
            reply_markup=kb_training_main
        )
        return
    
    # Первый запуск - онбординг
    # Send first 3 onboarding screens sequentially without waiting for user response
    for screen in ONBOARDING_SCREENS[:3]:
        await m.answer(screen)
        await asyncio.sleep(0.3)
    
    # Now ask for name
    u["stage"] = "ask_name"
    await save_user(u)

    await m.answer(
        "Привет. Я тренер навыков саморегуляции.\n"
        "Как тебя зовут? (1 слово)",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True),
    )

@router.message()
async def main_flow(m: Message):
    uid = m.from_user.id
    u = await get_user(uid)

    text = (m.text or "").strip()

    # stage routing
    if u["stage"] == "ask_name":
        if text and text.lower() != "пропустить":
            u["name"] = text[:50]
        await log_event(u["user_id"], "onboarding", "name_provided", {})
        u["stage"] = "choose_trainer"
        await save_user(u)
        await m.answer(
            "Ок. Выбери тренера:",
            reply_markup=kb_trainers,
        )
        return

    if u["stage"] == "choose_trainer":
        low = text.lower().strip()
        
        # Обработка кнопок тренеров с точным совпадением
        if text == "🐈‍⬛ Скинни (жёстко)" or "скинни" in low:
            u["trainer_key"] = "skinny"
        elif text == "🐈 Марша (мягко)" or "марша" in low:
            u["trainer_key"] = "marsha"
        elif text == "🐈‍🦁 Бек (аналитично)" or "бек" in low:
            u["trainer_key"] = "beck"
        else:
            await m.answer("Выбери кнопкой 👇", reply_markup=kb_trainers)
            return

        await log_event(u["user_id"], "onboarding", "trainer_chosen", {"trainer": u["trainer_key"]})
        # Move to trainer intro stage
        u["stage"] = "trainer_intro"
        await save_user(u)

        # Show trainer photo (if any) and intro
        await send_trainer_photo_if_any(m.chat.id, u["trainer_key"])
        await send_trainer_introduction(m, u)

        await m.answer("Готов начать диагностику?", reply_markup=kb_yes_no)
        return

    # ============================================================
    # TRAINER INTRO CONFIRM
    # ============================================================

    if u.get("stage") == "trainer_intro":
        low = (text or "").lower()

        if "да" in low:
            u["stage"] = "choose_input_mode"
            await save_user(u)
            await m.answer(
                f"{u.get('name')}, как удобнее пройти диагностику?",
                reply_markup=kb_input_mode
            )
            return

        if "нет" in low:
            u["stage"] = "choose_trainer"
            await save_user(u)
            await m.answer("Выбери другого тренера 👇", reply_markup=kb_trainers)
            return

        await m.answer("Выбери: ✅ Да / ❌ Нет", reply_markup=kb_yes_no)
        return

    if u["stage"] == "choose_input_mode":
        low = text.lower().strip()
        
        # Обработка первой кнопки: текст
        if text == "🧠 Диагностика текстом" or "текст" in low:
            u["input_mode"] = "text"
            u["stage"] = "await_problem_text"
            await save_user(u)
            await m.answer(
                "Ок. Напиши 2–5 предложений: что сейчас мешает делать важное?",
                reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Пропустить")]], resize_keyboard=True),
            )
            return
        
        # Обработка второй кнопки: голос
        if text == "🎙 Диагностика голосом" or "голос" in low:
            u["input_mode"] = "voice"
            u["stage"] = "await_problem_voice"
            await save_user(u)
            await m.answer(
                "Ок. Пришли голосовое (10–30 сек): что сейчас мешает делать важное?",
                reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Назад")]], resize_keyboard=True),
            )
            return
        
        # Обработка третьей кнопки: тест
        if text == "❓ Быстрый тест (5 вопросов)" or "тест" in low:
            u["input_mode"] = "test"
            u["stage"] = "taking_test"
            u["test_answers"] = []
            await save_user(u)
            
            # Показываем первый вопрос теста
            first_q = TEST_QUESTIONS[0]
            msg = f"❓ Вопрос 1/5:\n\n{first_q['text']}"
            
            await m.answer(msg, reply_markup=create_test_question_keyboard(1))
            return

        await m.answer("Выбери кнопкой 👇", reply_markup=kb_input_mode)
        return

    if u["stage"] == "await_problem_text":
        if not text or text.lower() == "пропустить":
            # fallback minimal
            user_text = "Прокрастинация/избегание, хочу начать, но откладываю."
        else:
            user_text = text

        u["analysis_json"] = json.dumps({"user_text": clamp_str(user_text, 1000)}, ensure_ascii=False)
        u["stage"] = "run_analysis"
        await save_user(u)

        await m.answer("Ок. Быстрый разбор…")
        await run_analysis(m, u, user_text)
        return

    if u["stage"] == "await_problem_voice":
        if text and text.lower() == "назад":
            u["stage"] = "choose_input_mode"
            await save_user(u)
            await m.answer("Ок. Выбери режим:", reply_markup=kb_input_mode)
            return

        if not m.voice:
            await m.answer("Пришли голосовое 🎙")
            return

        t = await whisper_transcribe(m)
        if not t:
            u["stage"] = "await_problem_text"
            await save_user(u)
            await m.answer("Не смог разобрать. Напиши текстом 1–3 предложения.")
            return

        u["analysis_json"] = json.dumps({"user_text": clamp_str(t, 1000)}, ensure_ascii=False)
        u["stage"] = "run_analysis"
        await save_user(u)

        await m.answer("Ок. Быстрый разбор…")
        await run_analysis(m, u, t)
        return

    if u.get("stage") == "analysis_contract":
        low = (text or "").lower()

        if "да" in low:
            u["stage"] = "analysis_map"
            await save_user(u)

            await m.answer(month_map_text(u.get("bucket")))
            await m.answer(
                guarantee_block(u.get("trainer_key")),
                reply_markup=kb_yes_no
            )
            return

        if "нет" in low:
            await m.answer("Ок. Вернёмся позже.")
            return

    if u.get("stage") == "analysis_map":
        low = (text or "").lower()

        if "да" in low:
            u["stage"] = "training"
            await save_user(u)

            await log_event(u["user_id"], "analysis", "day1_started", {})
            await start_day(m, u, 1)
            return

        if "нет" in low:
            await m.answer("Ок. Без гарантии — не стартуем.")
            return

    if u["stage"] == "confirm_analysis":
        low = text.lower()

        # ---------- Подтверждение ----------
        if "в точку" in low or (text == "✅ Да, в точку"):
            await log_event(u["user_id"], "analysis", "analysis_accepted", {})
            u["stage"] = "analysis_contract"
            await save_user(u)
            await m.answer(
                analysis_contract_short(
                    u.get("name") or "друг",
                    u.get("trainer_key"),
                    u.get("bucket")
                )
            )
            await m.answer(
                "📜 Принять контракт на 4 недели?",
                reply_markup=kb_yes_no
            )
            return

        # ---------- Немного не так ----------
        if "немного" in low or "не так" in low or (text == "🤔 Немного не так"):
            u["stage"] = "analysis_refine"
            await save_user(u)

            await m.answer(
                "Ок. Тогда уточним.\n\n"
                "Ответь коротко:\n"
                "1️⃣ Тебе сложнее начать или удерживать?\n"
                "2️⃣ Больше тревоги или больше пустоты?\n"
                "3️⃣ Или тебя больше всего выбивают отвлечения?\n\n"
                "Напиши одним предложением."
            )
            return

        await m.answer("Выбери кнопку 👇", reply_markup=kb_analysis_confirm)
        return

    # ------------- ANALYSIS RETRY (уточнение после "ты меня не понял") -------------
    if u.get("stage") == "analysis_retry_await_clarification":
        if not text:
            await m.answer("Напиши, пожалуйста, что не совпадает с реальностью. (1–3 предложения)")
            return
        
        # Повторно анализируем с новым текстом
        u["analysis_json"] = json.dumps({"user_text": clamp_str(text, 1000)}, ensure_ascii=False)
        u["stage"] = "run_analysis"
        await save_user(u)
        
        await m.answer("Ок. Переразбор…")
        await run_analysis(m, u, text)
        return

    # ============================================================
    # Повторный анализ после уточнения
    # ============================================================

    if u["stage"] == "analysis_refine":
        if not text:
            await m.answer("Напиши 1–2 предложения.")
            return

        # добавляем уточнение к исходному тексту
        u["raw_text"] = (u.get("raw_text") or "") + "\n\nУточнение пользователя: " + text
        u["stage"] = "run_analysis"
        await save_user(u)

        await m.answer("Ок. Пересобираю модель...")
        await run_analysis(m, u, text)
        return

    # ------------- TRAINING (Day 1–7 and далее) -------------
    if u.get("stage") == "training":
        low = text.lower().strip()
        day = int(u.get("day") or 1)

        if text == "ℹ️ Подробнее про навык" or "подробнее" in low:
            plan = get_current_plan(u)
            idx = max(0, min(len(plan) - 1, int(u.get("day") or 1) - 1))
            sid = plan[idx]
            s = SKILLS_DB.get(sid, {})

            steps = s.get("steps") or ([s.get("how")] if s.get("how") else [])
            msg = (
                f"🧩 {s.get('name','Навык')}\n\n"
                f"🎯 Зачем:\n{s.get('goal','')}\n\n"
                "🔹 Делай так:\n" +
                "\n".join([f"{i+1}. {x}" for i, x in enumerate(steps)]) +
                f"\n\n⚡ Микро-версия:\n{s.get('micro', s.get('minimum',''))}"
            )
            await m.answer(msg, reply_markup=kb_more_clarify)
            return

        if text == "👍 Понял(а), продолжаем" or text == "📚 Подробнее почему это работает" or "подробнее почему" in low:
            trainer_key = u.get("trainer_key") or "marsha"
            
            if text == "👍 Понял(а), продолжаем" or ("понял" in low and "подробнее" not in low):
                # Just return to training
                await log_event(u["user_id"], "training", "doubt_understood", {"trainer": trainer_key})
                await m.answer(
                    trainer_say(trainer_key, PRAISE.get(trainer_key, "Идём дальше!")),
                    reply_markup=kb_training_main
                )
                return
            else:
                # Show detailed explanation
                await log_event(u["user_id"], "training", "doubt_details_requested", {"trainer": trainer_key})
                
                if trainer_key == "skinny":
                    details_text = (
                        "📊 Почему микро-тренули работают:\n\n"
                        "• 60 сек — это минимум для активации нейро-связей\n"
                        "• Повторяемость важнее объёма\n"
                        "• 3 дня подряд = установка нового паттерна\n"
                        "• Эффект накапливается. Видно на день 3-4.\n\n"
                        "Сделал → умеешь. Так работает мозг."
                    )
                elif trainer_key == "beck":
                    details_text = (
                        "🧬 Нейро-механика повторения:\n\n"
                        "• Синапс усиливается при каждом выполнении (Hebb's Law)\n"
                        "• Миелинизация идёт на 3-7 день регулярности\n"
                        "• Метрика done/return показывает адаптацию мозга\n"
                        "• Долгосрочная потенциация = стабильный навык\n\n"
                        "Графики покажут, когда функция встроилась."
                    )
                else:  # marsha
                    details_text = (
                        "🌱 Как работает безопасный рост:\n\n"
                        "• Микро-шаги = нет перегрузки и стыда\n"
                        "• Повтор = уверенность, не сомнения\n"
                        "• Каждый успех закраски невидимым рост\n"
                        "• Если день не пошёл — просто возвращаемся завтра\n\n"
                        "Эффект видно не на неделе, а на двух."
                    )
                
                await m.answer(
                    trainer_say(trainer_key, details_text),
                    reply_markup=kb_training_main
                )
                return

        if text == "Ты меня не понял" or "не понял" in low:
            # User requests re-analysis / clarification
            u["analysis_retry_count"] = int(u.get("analysis_retry_count") or 0) + 1
            retry_count = u["analysis_retry_count"]

            if retry_count > 2:
                u["stage"] = "training"
                await save_user(u)
                await log_event(u["user_id"], "analysis", "retry_limit_reached", {})
                await m.answer(
                    trainer_say(
                        u["trainer_key"],
                        "Я уже трижды пытался понять. 😊\n\nДавай начнём тренировку и посмотрим, как это будет работать в жизни.\n\nМожет быть, это станет яснее когда ты начнёшь."
                    ),
                    reply_markup=kb_training_main
                )
                await start_day(m=m, u=u, day=1)
                return

            # Ask for clarification
            await save_user(u)
            u["stage"] = "analysis_retry_await_clarification"
            await save_user(u)
            await m.answer(
                trainer_say(
                    u["trainer_key"],
                    f"Ок. Уточни ещё раз (попытка {retry_count}/2):\n\nЧто конкретно здесь не правда? Расскажи подробнее."
                )
            )
            return

        if text == "🆘 Кризис" or "кризис" in low:
            u["stage"] = "crisis_choose_mode"
            await save_user(u)
            await log_event(u["user_id"], u["stage"], "crisis_open", {})
            await m.answer("🆘 Ок. Как удобнее?", reply_markup=kb_crisis_mode)
            return

        if text == "📊 Мой прогресс" or "мой прогресс" in low or "прогресс" in low:
            await send_progress_report(m, u)
            return

        if text == "✅ Сделал(а)" or "сделал" in low:
            await log_event(u["user_id"], "training", "done", {"day": day})
            u["done_count"] += 1
            gamify_apply(u, 2, "done")
            await save_user(u)
            await m.answer(trainer_say(u.get("trainer_key") or "marsha", "Факт принят. Ты тренируешь навык."))
            # Praise
            try:
                await m.answer(trainer_say(u.get("trainer_key") or "marsha", PRAISE.get(u.get("trainer_key") or "marsha", "")))
            except Exception:
                pass

            if day == 7:
                await send_weekly_summary(m, u)

            # Offer early after Day 3 for trial3
            if day == 3 and u.get("trial_phase") == "trial3":
                await m.answer(
                    "Ты уже видел(а):\n"
                    "это не мотивация.\n"
                    "Это тренировка.\n\n"
                    "💳 Сейчас — цена со скидкой.",
                    reply_markup=kb_pay_choice
                )
                u["stage"] = "offer"
                await save_user(u)
                return

            if day >= 7 and u.get("trial_phase") in ("trial3", "trial7", None):
                await m.answer("Выбирай вариант оплаты:", reply_markup=kb_pay_choice)
                u["stage"] = "offer"
                await save_user(u)
                return

            await start_day(m, u, day + 1)
            return

        if text == "↩️ Вернулся(лась)" or "вернулся" in low:
            await log_event(u["user_id"], "training", "return", {"day": day})
            u["return_count"] += 1
            gamify_apply(u, 1, "return")
            await save_user(u)
            await m.answer(trainer_say(u.get("trainer_key") or "marsha", "Возврат засчитан. Это ключевой навык."))
            # Praise
            try:
                await m.answer(trainer_say(u.get("trainer_key") or "marsha", PRAISE.get(u.get("trainer_key") or "marsha", "")))
            except Exception:
                pass

            if day == 7:
                await send_weekly_summary(m, u)

            if day == 3 and u.get("trial_phase") == "trial3":
                await m.answer(
                    "Ты уже видел(а):\n"
                    "это не мотивация.\n"
                    "Это тренировка.\n\n"
                    "💳 Сейчас — цена со скидкой.",
                    reply_markup=kb_pay_choice
                )
                u["stage"] = "offer"
                await save_user(u)
                return

            if day >= 7 and u.get("trial_phase") in ("trial3", "trial7", None):
                await m.answer("Выбирай вариант оплаты:", reply_markup=kb_pay_choice)
                u["stage"] = "offer"
                await save_user(u)
                return

            await start_day(m, u, day + 1)
            return

        if text == "❓ Сомневаюсь, работает ли" or "сомневаюсь" in low:
            trainer_key = u.get("trainer_key") or "marsha"
            
            # Log event
            await log_event(u["user_id"], "training", "doubt_pressed", {"trainer": trainer_key})
            
            # Different responses per trainer
            if trainer_key == "skinny":
                doubt_text = (
                    "Поможет/не поможет — узнаем только выполнением 60 секунд.\n\n"
                    "Факт есть или факта нет.\n"
                    "Третьего не дано.\n\n"
                    "Делай сегодня — увидишь завтра."
                )
            elif trainer_key == "beck":
                doubt_text = (
                    "Это не вопрос веры, это вопрос эффекта.\n\n"
                    "Тренинг навыков работает через повторение.\n"
                    "Мы измеряем микро-метриками: done/return.\n\n"
                    "Через 2 недели будет график нейро-адаптации."
                )
            else:  # marsha
                doubt_text = (
                    "Сомнение нормально.\n\n"
                    "Мы проверяем не верой, а маленькими фактами.\n"
                    "Каждый день — один факт за 60 секунд.\n\n"
                    "Если не подходит — меняем инструмент."
                )
            
            await m.answer(trainer_say(trainer_key, doubt_text), reply_markup=kb_doubt_response)
            return

        if "не пошло" in low or "не подходит" in low or "не работает" in low:
            u["stage"] = "skill_replace"
            await save_user(u)
            await log_event(u["user_id"], "training", "skill_replace_requested", {"day": day, "reason": text})
            await m.answer(
                trainer_say(
                    u.get("trainer_key") or "marsha",
                    "Ок. Подберём другой навык.\n\nЭто часть процесса."
                )
            )
            return

        if text == "🔁 Заменить навык" or "заменить" in low:
            u["stage"] = "skill_replace"
            await save_user(u)
            await log_event(u["user_id"], "training", "skill_replace_requested", {"day": day, "reason": "button"})
            await m.answer(
                trainer_say(
                    u.get("trainer_key") or "marsha",
                    "Ок. Подберём другой навык.\n\nЭто часть процесса."
                )
            )
            return

        await m.answer("Выбери кнопку 👇", reply_markup=kb_training_main)
        return

    # --- кризис режим ---
    if u.get("stage") == "crisis_choose_mode":
        low = text.lower().strip()
        
        # Обработка кнопок кризиса с точным совпадением
        if text == "⬅️ Назад" or "назад" in low:
            u["stage"] = "training"
            await save_user(u)
            await m.answer("Ок. Возвращаемся в тренировку.", reply_markup=kb_training_main)
            return
        if text == "🎙 Кризис голосом" or "голос" in low:
            u["stage"] = "crisis_voice"
            await save_user(u)
            await m.answer("🎙 Запиши голосом: что происходит и что мешает прямо сейчас?")
            return
        if text == "✍️ Кризис текстом" or "текст" in low:
            u["stage"] = "crisis_text"
            await save_user(u)
            await m.answer("✍️ Напиши: что происходит и что мешает прямо сейчас? (1–3 предложения)")
            return
        await m.answer("Выбери кнопкой 👇", reply_markup=kb_crisis_mode)
        return

    if u.get("stage") == "crisis_text":
        if not text:
            await m.answer("Напиши 1–3 предложения.")
            return
        await handle_crisis(m, u, text)
        return

    if u.get("stage") == "crisis_voice":
        if not m.voice:
            await m.answer("Пришли голосовое 🎙")
            return
        t = await whisper_transcribe(m)
        if not t:
            await m.answer("Не смог разобрать. Напиши текстом 1–3 предложения.")
            u["stage"] = "crisis_text"
            await save_user(u)
            return
        await handle_crisis(m, u, t)
        return

    if u.get("stage") == "crisis_plan_confirm":
        low = text.lower().strip()
        
        # Обработка кнопок yes/no с точным совпадением
        if text == "✅ Да" or "да" in low:
            pending = json.loads(u.get("pending_plan_change") or "{}") if u.get("pending_plan_change") else {}
            day_num = pending.get("day_num")
            sid = pending.get("skill_id")
            if day_num and sid:
                propose_plan_override(u, int(day_num), sid)
                u["pending_plan_change"] = None
                await save_user(u)
                await log_event(u["user_id"], u.get("stage", ""), "plan_change_accept", {"day": day_num, "skill": sid})
                await m.answer("✅ Ок. Я обновил план. Завтра будет эта версия.")
            u["stage"] = "training"
            await save_user(u)
            await m.answer("Возвращаемся в тренировку.", reply_markup=kb_training_main)
            return

        if text == "❌ Нет" or "нет" in low:
            u["pending_plan_change"] = None
            u["stage"] = "training"
            await save_user(u)
            await log_event(u["user_id"], u.get("stage", ""), "plan_change_reject", {})
            await m.answer("Ок. План не меняю. Возвращаемся.", reply_markup=kb_training_main)
            return

        await m.answer("Выбери: ✅ Да / ❌ Нет", reply_markup=kb_yes_no)
        return

    # ------------- OFFER (две ссылки оплаты) -------------
    if u.get("stage") == "offer":
        low = text.lower().strip()

        if text == "💳 Оплатить со скидкой" or "со скидкой" in low:
            await m.answer("Ок. Скидка по ссылке 👇")
            await m.answer(" ", reply_markup=payment_inline_discount())
            return

        if text == "💳 Оплатить без скидки" or "без скидки" in low:
            await m.answer("Ок. Полная цена по ссылке 👇")
            await m.answer(" ", reply_markup=payment_inline_full())
            return
        
        if text == "➕ Ещё 4 дня без оплаты" or "ещё" in low or "дня" in low:
            await m.answer("Ок. Продолжаем тренировку! 💪")
            u["stage"] = "training"
            await save_user(u)
            await m.answer("Выбери действие:", reply_markup=kb_training_main)
            return

        if text == "❌ Не готов(а)" or "не готов" in low:
            await m.answer("Ок. Если захочешь продолжить — просто напиши /start")
            u["stage"] = "idle"
            await save_user(u)
            return

        if "ещ" in low:
            u["trial_days"] = 7
            u["trial_phase"] = "trial7"
            await save_user(u)
            await m.answer("Ок. Ещё 4 дня в пробе. Продолжаем.", reply_markup=kb_training_main)
            u["stage"] = "training"
            await save_user(u)
            return

        await m.answer("Выбирай кнопкой 👇", reply_markup=kb_pay_choice)
        return

    # fallback
    await m.answer("Напиши /start чтобы начать заново.")

# ============================================================
# 12) Analysis runner
# ============================================================

async def run_analysis(m: Message, u: Dict[str, Any], user_text: str):
    # Try quick analysis first (keeps fallback behavior)
    r = await ai_analyze(user_text)

    # Attempt to get a comprehensive analysis (may fallback internally)
    comp = await ai_analyze_comprehensive(user_text, u.get("trainer_key", "marsha"))

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
    await save_user(u)

    # Log that analysis was shown
    await log_event(u["user_id"], "analysis", "analysis_shown", {"bucket": u.get("bucket")})

    # Short selling text + buttons (matches comprehensive flow)
    short_text = comp.get("short_summary") or r.get("summary") or "Похоже на тебя?"
    msg = f"{short_text}\n\nЭто похоже на тебя?"

    await m.answer(msg, reply_markup=kb_analysis_confirm)

# ============================================================
# 13) Callbacks (inline yes/no)
# ============================================================

@router.callback_query(F.data.in_({"yes", "no", "noop"}))
async def on_callbacks(c: CallbackQuery):
    uid = c.from_user.id
    u = await get_user(uid)

    if c.data == "noop":
        await c.answer()
        return

    if u.get("stage") == "confirm_analysis":
        if c.data == "yes":
            u["stage"] = "training"
            await save_user(u)
            await c.message.answer(trainer_say(u["trainer_key"], "Ок. Стартуем День 1."), reply_markup=kb_training_main)
            await start_day(m=c.message, u=u, day=1)
        else:
            u["stage"] = "await_problem_text"
            await save_user(u)
            await c.message.answer("Ок. Тогда уточни: что больше всего мешает? (2–3 предложения)")
        await c.answer()
        return

    await c.answer()

# ============================================================
# 13.5) TEST CALLBACK HANDLER (для вопросов теста)
# ============================================================

@router.callback_query(F.data.startswith("test_q"))
async def on_test_answer(c: CallbackQuery):
    """Обработка ответа на вопрос теста"""
    uid = c.from_user.id
    u = await get_user(uid)
    
    try:
        # Парсим callback_data формата: test_q1_anxiety
        parts = c.data.split("_")
        if len(parts) < 3:
            await c.answer("Ошибка в данных")
            return
        
        q_num = int(parts[1][1:])  # test_q1 -> 1
        bucket_answer = "_".join(parts[2:])  # Для multi-word buckets
        
        # Получаем текущий тест
        test_answers = u.get("test_answers") or []
        test_answers.append(bucket_answer)
        u["test_answers"] = test_answers
        # persist current answers so subsequent callbacks see progress
        await save_user(u)
        
        # Проверяем, прошли ли все 5 вопросов
        if len(test_answers) < len(TEST_QUESTIONS):
            next_q_num = len(test_answers) + 1
            next_q = next((x for x in TEST_QUESTIONS if x["id"] == next_q_num), None)
            if next_q:
                await c.message.edit_text(
                    f"❓ Вопрос {next_q_num}/5:\n\n{next_q['text']}",
                    reply_markup=create_test_question_keyboard(next_q_num)
                )
            await c.answer()
        else:
            # Тест завершён, определяем bucket
            resolved_bucket = resolve_bucket_from_test(test_answers)
            u["bucket"] = resolved_bucket
            u["test_answers"] = []  # Очищаем тест
            u["stage"] = "test_complete_show_analysis"
            await save_user(u)
            
            # Отправляем comprehensive analysis
            await show_comprehensive_analysis(c.message, u)
            await c.answer()
            
    except Exception as e:
        log.error(f"Error in test callback: {e}")
        await c.answer("Ошибка обработки ответа")

async def show_comprehensive_analysis(m: Message, u: Dict[str, Any]):
    """Показать comprehensive analysis с кнопками"""
    bucket = u.get("bucket") or "mixed"
    
    # Попробуем получить full analysis из AI (если text был сохранён)
    user_text = ""
    if u.get("analysis_json"):
        try:
            analysis_data = json.loads(u.get("analysis_json") or "{}")
            user_text = analysis_data.get("user_text", "")
        except:
            pass
    
    # Если нет текста, используем fallback для bucket
    if not user_text:
        user_text = f"У меня проблемы с {bucket}"
    
    # Отправляем в AI для comprehensive analysis
    comp = await ai_analyze_comprehensive(user_text, u.get("trainer_key", "marsha"))
    
    # Сохраняем полный анализ
    u["analysis_json"] = json.dumps(comp, ensure_ascii=False)
    u["bucket"] = comp.get("bucket", bucket)
    
    # Строим 28-дневный план
    plan_ids = build_28_day_plan(u["bucket"])
    u["plan_json"] = json.dumps(plan_ids, ensure_ascii=False)
    u["day"] = 1
    u["stage"] = "confirm_analysis"
    await save_user(u)
    
    # Log analysis shown event
    await log_event(u["user_id"], "analysis", "analysis_shown", {"bucket": u.get("bucket")})
    
    # Короткий текст + кнопка подтверждения
    msg = f"{comp.get('short_summary', 'Похоже на тебя?')}\n\nЭто похоже на тебя?"
    
    await m.answer(msg, reply_markup=kb_analysis_confirm)

# ============================================================
# 13.6) ANALYSIS DETAILED CALLBACK
# ============================================================

@router.callback_query(F.data == "analysis_proceed")
async def on_analysis_proceed(c: CallbackQuery):
    """Callback: принятие анализа → старт контрактной воронки"""
    u = await get_user(c.from_user.id)

    # фиксируем событие
    await log_event(u["user_id"], "analysis", "analysis_accepted", {})

    u["stage"] = "analysis_contract"
    await save_user(u)

    await c.message.answer(
        analysis_contract_short(
            u.get("name") or "друг",
            u.get("trainer_key"),
            u.get("bucket")
        )
    )

    await c.message.answer(
        "📜 Принять контракт на 4 недели?",
        reply_markup=kb_yes_no
    )

    await c.answer()


@router.callback_query(F.data == "analysis_detailed")
async def on_analysis_detailed(c: CallbackQuery):
    """Callback: показать подробный анализ"""
    u = await get_user(c.from_user.id)
    
    # Показываем полный анализ
    analysis = {}
    if u.get("analysis_json"):
        try:
            analysis = json.loads(u.get("analysis_json") or "{}")
        except:
            pass
    
    detailed_msg = (
        f"🎯 *Что происходит:*\n{analysis.get('what_is_happening', '')}\n\n"
        f"*Почему это происходит:*\n{analysis.get('why_it_happens', '')}\n\n"
        f"*Это не твоя вина:*\n{analysis.get('not_your_fault_or_control_zone', '')}\n\n"
        f"*Почему это тренируется:*\n{analysis.get('why_change_is_possible', '')}\n\n"
        f"*Путь тренировки:*\n{analysis.get('training_path', '')}\n\n"
        f"*Фокус на навыки:*\n" + 
        "\n".join([f"• {skill}" for skill in analysis.get('skills_focus', [])]) +
        f"\n\n⏱ *Сроки:*\n{analysis.get('timeline', '')}\n\n"
        f"✅ *Гарантия поддержки:*\n{analysis.get('support_guarantee', '')}"
    )
    
    reply_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Начнём", callback_data="detailed_start")],
        ]
    )
    
    await c.message.answer(detailed_msg, reply_markup=reply_kb)
    await c.answer()


@router.callback_query(F.data == "detailed_start")
async def on_detailed_start(c: CallbackQuery):
    """После просмотра подробнее - начинаем День 1"""
    uid = c.from_user.id
    u = await get_user(uid)
    
    u["stage"] = "training"
    await save_user(u)
    await c.message.answer(trainer_say(u["trainer_key"], "Ок. Стартуем День 1."), reply_markup=kb_training_main)
    await start_day(m=c.message, u=u, day=1)
    await c.answer()


@router.callback_query(F.data == "analysis_retry")
async def on_analysis_retry(c: CallbackQuery):
    """Callback: пользователь сказал "ты меня не понял" - переанализируем"""
    uid = c.from_user.id
    u = await get_user(uid)
    
    # Увеличиваем счётчик попыток
    u["analysis_retry_count"] = int(u.get("analysis_retry_count") or 0) + 1
    retry_count = u["analysis_retry_count"]
    
    # Максимум 2 попытки переспросить
    if retry_count > 2:
        u["stage"] = "training"
        await save_user(u)
        await c.message.answer(
            trainer_say(
                u["trainer_key"],
                "Я уже трижды пытался понять. 😊\n\n"
                "Давай начнём тренировку и посмотрим, как это будет работать в жизни.\n\n"
                "Может быть, это станет яснее когда ты начнёшь."
            ),
            reply_markup=kb_training_main
        )
        await start_day(m=c.message, u=u, day=1)
        await c.answer()
        return
    
    # Просим уточнения
    await save_user(u)
    await c.message.answer(
        trainer_say(
            u["trainer_key"],
            f"Ок. Уточни ещё раз (попытка {retry_count}/2):\n\n"
            "Что конкретно здесь не правда? Расскажи подробнее."
        )
    )
    
    u["stage"] = "analysis_retry_await_clarification"
    await save_user(u)
    await c.answer()

# ============================================================
# 14) Offer stage (MVP) — payment link
# ============================================================

async def send_offer(m: Message, u: Dict[str, Any]):
    await m.answer(
        "Три дня — проба. Дальше — платно.\n"
        "Оплата по ссылке:",
    )
    await m.answer(" ", reply_markup=payment_inline())

# ============================================================
# 15) Progress and crisis helpers (added)
# ============================================================

async def send_weekly_summary(m: Message, u: dict):
    uid = u["user_id"]
    since = time.time() - 7 * 24 * 3600

    async with aiosqlite.connect(DB_PATH) as db:
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

def gamify_status_line(u: dict) -> str:
    pts = int(u.get("points") or 0)
    lvl = int(u.get("level") or 1)
    streak = int(u.get("streak") or 0)
    return f"🏅 Очки: {pts} | Уровень: {lvl} | Стрик: {streak}"

async def send_progress_report(m: Message, u: dict):
    uid = u["user_id"]
    since = time.time() - 7 * 24 * 3600
    async with aiosqlite.connect(DB_PATH) as db:
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
    await log_event(uid, u.get("stage",""), "progress_view", {"done": done, "return": ret, "crisis": crisis})

async def handle_crisis(m: Message, u: dict, user_text: str):
    name = u.get("name") or "друг"
    trainer_key = u.get("trainer_key") or "marsha"
    bucket = u.get("bucket") or "mixed"

    # increment first; limit free crisis uses
    u["crisis_count"] = int(u.get("crisis_count") or 0) + 1
    await save_user(u)

    if not is_paid(u) and int(u.get("crisis_count") or 0) > CRISIS_LIMIT:
        await m.answer("🆘 Кризис — доступен без ограничений в полной версии.")
        return

    await log_event(u["user_id"], u.get("stage",""), "crisis_message", {"len": len(user_text)})
    gamify_apply(u, 1, "crisis_used")

    await m.answer(trainer_say(trainer_key, "Ок. Сейчас быстро стабилизируем и вернём контроль."))
    r = await ai_crisis_help(trainer_key, bucket, user_text)

    sid = r["skill_id"]
    s = SKILLS_DB[sid]

    msg = (
        f"🆘 {name}, коротко:\n"
        f"{r['support']}\n\n"
        f"🧩 Навык сейчас: {s['name']}\n"
        f"🎯 Зачем: {s['goal']}\n"
        f"✅ Микро-шаг: {r['micro_step'] or s.get('minimum', s.get('how'))}\n\n"
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
            await save_user(u)
            await m.answer(
                f"Хочешь, я на завтра (день {day_num}) заменю навык на:\n"
                f"➡️ {SKILLS_DB[new_sid]['name']} ?",
                reply_markup=kb_yes_no
            )
            return

    u["stage"] = "training"
    await save_user(u)
    await m.answer("Возвращаемся в тренировку 👇", reply_markup=kb_training_main)

# ============================================================
# 16) BACKGROUND TASKS
# ============================================================

async def background_ping(bot):
    while True:
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute("SELECT * FROM users")
            rows = await cur.fetchall()

        for row in rows:
            u = dict(zip(USER_FIELDS, row))
            if should_ping(u, 24) and u.get("stage") == "training":
                await bot.send_message(
                    u["chat_id"],
                    inactivity_ping(u.get("trainer_key"))
                )

        await asyncio.sleep(3600)

# ============================================================
# 17) RUN
# ============================================================

async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is empty")
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    dp = Dispatcher()
    dp.include_router(router)

    await init_db()
    await migrate_db()

    asyncio.create_task(background_ping(bot))

    log.info("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
