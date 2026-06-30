#!/usr/bin/env python3
"""Smoke checks for free-text stuck validation before skill advice."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("BOT_TOKEN", "")
os.environ.setdefault("OPENAI_API_KEY", "")

import bot  # noqa: E402
from db import default_user, get_user, get_user_profile, init_db, migrate_db, save_user  # noqa: E402
from texts import kb_failed  # noqa: E402


class FakeFromUser:
    def __init__(self, uid: int):
        self.id = uid
        self.username = "tester"


class FakeChat:
    def __init__(self, uid: int):
        self.id = uid


class FakeMessage:
    def __init__(self, uid: int, text: str = ""):
        self.from_user = FakeFromUser(uid)
        self.chat = FakeChat(uid)
        self.text = text
        self.voice = None
        self.answers: list[dict] = []

    async def answer(self, text: str, reply_markup=None, **kwargs):
        self.answers.append({"text": text, "reply_markup": reply_markup})


def keyboard_texts(markup) -> set[str]:
    return {button.text for row in getattr(markup, "keyboard", []) for button in row}


async def send(uid: int, text: str):
    msg = FakeMessage(uid, text)
    await bot.main_flow(msg)
    return await get_user(uid, bot.DB_PATH), "\n".join(item["text"] for item in msg.answers), msg


async def seed_user(uid: int, *, stage: str = "failed_options"):
    u = default_user(uid)
    u.update({
        "chat_id": uid,
        "stage": stage,
        "has_started_training": 1,
        "day": 1,
        "current_day_id": f"day-stuck-{uid}",
        "current_state": bot.STATE_AWAITING_STUCK_REASON,
        "current_action_id": f"act-stuck-{uid}",
        "daily_skill_id": "open_only",
        "daily_skill_name": "Открыть без таймера",
        "current_task_title": "делать бота",
    })
    await save_user(u, bot.DB_PATH)
    return u


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old_db_path = bot.DB_PATH
        bot.DB_PATH = str(Path(tmp) / "bot.db")
        try:
            await init_db(bot.DB_PATH)
            await migrate_db(bot.DB_PATH)
            failed_buttons = keyboard_texts(kb_failed)
            assert "🤷 Не понимаю, зачем это делать" in failed_buttons
            assert "🆘 Мне небезопасно" not in failed_buttons

            uid = 9301
            await seed_user(uid)
            u, prompt, _ = await send(uid, "🎙️ Опишу голосом или текстом")
            assert "Я сначала попробую понять" in prompt
            assert u["stage"] == "stuck_reason_text"

            u, validation, validation_msg = await send(uid, "не понимаю нахуя")
            assert "Я услышал" in validation
            assert "Главный механизм" in validation
            assert "Рабочая гипотеза" in validation
            assert "Навык:" in validation
            assert "Минимальный физический шаг" in validation
            buttons = keyboard_texts(validation_msg.answers[-1]["reply_markup"])
            assert {"✅ Да, похоже", "🟡 Не совсем", "🔄 Сменить навык", "🧠 Уточнить"}.issubset(buttons)
            assert u["stage"] == "stuck_validation_choice"
            profile = await get_user_profile(uid, bot.DB_PATH)
            assert profile.get("last_free_stuck_hypothesis") == "meaning"

            u, changed, _ = await send(uid, "✅ Да, похоже")
            assert "Минимальный шаг" in changed or "возвращение контроля" in changed

            uid2 = 9302
            await seed_user(uid2, stage="stuck_reason_text")
            u2, safety_validation, _ = await send(uid2, "я устал, ничего не хочу, всё бессмысленно")
            assert "безопас" in safety_validation
            assert "Минимальный физический шаг" in safety_validation
            assert u2["stage"] == "stuck_validation_choice"
            u2, уточнить, _ = await send(uid2, "🧠 Уточнить")
            assert "уточни" in уточнить.lower()
            assert u2["stage"] == "stuck_reason_text"

            uid3 = 9303
            await seed_user(uid3, stage="stuck_reason_text")
            u3, self_attack, _ = await send(uid3, "я опять всё просрал, ненавижу себя")
            assert "самокритика" in self_attack
            assert "Минимальный физический шаг" in self_attack
            u3, calm, _ = await send(uid3, "✅ Да, похоже")
            assert "Минимальный шаг" in calm or "возвращение контроля" in calm

            uid4 = 9304
            await seed_user(uid4, stage="stuck_reason_text")
            u4, question, _ = await send(uid4, "застрял")
            assert "Пока данных мало" in question
            assert "Сейчас тяжелее выбрать" in question
            assert u4["stage"] == "stuck_reason_text"
            u4, clarified, clarified_msg = await send(uid4, "выбрать с чего начать, слишком много задач")
            assert "Главный механизм" in clarified
            assert "Навык:" in clarified
            assert "Минимальный физический шаг" in clarified
            assert {"✅ Да, похоже", "🟡 Не совсем", "🔄 Сменить навык", "🧠 Уточнить"}.issubset(keyboard_texts(clarified_msg.answers[-1]["reply_markup"]))

            uid5 = 9305
            await seed_user(uid5, stage="stuck_reason_text")
            u5, q1, _ = await send(uid5, "застрял")
            assert "Сейчас тяжелее выбрать" in q1
            u5, q2, _ = await send(uid5, "не могу")
            assert "Тревога больше" in q2
            u5, q3, _ = await send(uid5, "сложно")
            assert "Тебе сейчас нужен" in q3
            u5, final_after_three, _ = await send(uid5, "плохо")
            assert "Навык:" in final_after_three
            assert "Минимальный физический шаг" in final_after_three
        finally:
            bot.DB_PATH = old_db_path

    print("[SMOKE] stuck validation OK")


if __name__ == "__main__":
    asyncio.run(main())
