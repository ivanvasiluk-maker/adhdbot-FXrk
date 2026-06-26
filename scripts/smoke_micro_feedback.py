#!/usr/bin/env python3
"""Smoke checks for tester micro-feedback prompts and storage."""
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
from db import (  # noqa: E402
    default_user,
    init_db,
    migrate_db,
    save_user,
    recent_user_feedback,
    user_feedback_count,
)


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
        self.answers.append({"text": text, "reply_markup": reply_markup, **kwargs})


def keyboard_texts(markup) -> list[str]:
    return [button.text for row in getattr(markup, "keyboard", []) for button in row]


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "bot.db")
        old_db_path = bot.DB_PATH
        bot.DB_PATH = db_path
        try:
            await init_db(db_path)
            await migrate_db(db_path)

            uid = 9001
            u = default_user(uid)
            u.update({
                "stage": "training_main",
                "current_day_id": "day-feedback-1",
                "current_core_skill_id": "open_without_timer",
                "daily_skill_id": "open_without_timer",
                "trainer_key": "skinny",
                "done_count": 1,
            })
            await save_user(u, db_path)

            first_prompt = FakeMessage(uid)
            assert await bot.ask_instruction_clarity_feedback(first_prompt, u) is True
            assert "было понятно" in first_prompt.answers[-1]["text"]
            assert "🎙️ Напишу или скажу сам(а)" in keyboard_texts(first_prompt.answers[-1]["reply_markup"])

            answer = FakeMessage(uid, "🟡 В целом понятно")
            assert await bot.handle_feedback_response(answer, u, answer.text) is True
            rows = await recent_user_feedback(uid, db_path)
            assert rows[0]["feedback_type"] == "feedback_instruction_clarity"
            assert rows[0]["value"] == "partly_clear"
            assert rows[0]["skill_id"]
            assert rows[0]["trainer_key"] == "skinny"

            repeat_prompt = FakeMessage(uid)
            assert await bot.ask_instruction_clarity_feedback(repeat_prompt, u) is False
            assert await user_feedback_count(uid, db_path, "feedback_instruction_clarity", day_id="day-feedback-1") == 1

            stuck_user = default_user(9002)
            stuck_user.update({"stage": "downscale", "current_day_id": "day-feedback-2", "trainer_key": "marsha"})
            await save_user(stuck_user, db_path)
            stuck_prompt = FakeMessage(9002)
            assert await bot.ask_validation_feedback(stuck_prompt, stuck_user, "phone", "Телефон вне руки") is True
            assert "бот понял" in stuck_prompt.answers[-1]["text"]
            missed = FakeMessage(9002, "🔴 Нет, мимо")
            assert await bot.handle_feedback_response(missed, stuck_user, missed.text) is True
            assert "что бот не понял" in missed.answers[-1]["text"]
            free = FakeMessage(9002, "я говорил про страх, а не телефон")
            assert await bot.handle_feedback_response(free, stuck_user, free.text) is True
            rows = await recent_user_feedback(9002, db_path)
            assert rows[0]["feedback_type"] == "feedback_validation"
            assert rows[0]["value"] == "free_text"
            assert "страх" in rows[0]["comment"]

            day_user = default_user(9003)
            day_user.update({"day": 1, "current_day_id": "day-feedback-3", "trainer_key": "beck", "done_count": 2})
            await save_user(day_user, db_path)
            day_prompt = FakeMessage(9003)
            assert await bot.ask_day_value_feedback(day_prompt, day_user) is True
            assert "Что сегодня было полезнее" in day_prompt.answers[-1]["text"]
            nothing = FakeMessage(9003, "😐 Пока ничего")
            assert await bot.handle_feedback_response(nothing, day_user, nothing.text) is True
            assert "Что было главным" in nothing.answers[-1]["text"]
            reason = FakeMessage(9003, "😬 Было слишком много текста")
            assert await bot.handle_feedback_response(reason, day_user, reason.text) is True
            rows = await recent_user_feedback(9003, db_path)
            assert rows[0]["feedback_type"] == "feedback_day_value"
            assert rows[0]["value"] == "nothing_yet"
            assert rows[0]["metadata"]["reason"] == "too_much_text"

            value_user = default_user(9004)
            value_user.update({"day": 3, "current_day_id": "day-feedback-4"})
            await save_user(value_user, db_path)
            value_prompt = FakeMessage(9004)
            assert await bot.ask_product_value_feedback(value_prompt, value_user) is True
            assert "жалко его потерять" in value_prompt.answers[-1]["text"]
            score = FakeMessage(9004, "6")
            assert await bot.handle_feedback_response(score, value_user, score.text) is True
            assert "мешает почувствовать пользу" in score.answers[-1]["text"]

            offer_user = default_user(9005)
            offer_user.update({"stage": "feedback_offer", "current_day_id": "day-feedback-5", "trainer_key": "skinny"})
            await save_user(offer_user, db_path)
            offer_answer = FakeMessage(9005, "🧠 Не понимаю разницу режимов")
            assert await bot.handle_feedback_response(offer_answer, offer_user, offer_answer.text) is True
            rows = await recent_user_feedback(9005, db_path)
            assert rows[0]["feedback_type"] == "offer_feedback"
            assert "разницу" in rows[0]["comment"]

            safety_user = default_user(9006)
            safety_user.update({"safety_mode": "urgent", "current_day_id": "day-feedback-6", "done_count": 1})
            await save_user(safety_user, db_path)
            safety_prompt = FakeMessage(9006)
            assert await bot.ask_instruction_clarity_feedback(safety_prompt, safety_user) is False

            debug_text = await bot.recent_user_feedback_text(uid, 20)
            assert "понятность первого навыка" in debug_text
        finally:
            bot.DB_PATH = old_db_path

    print("[SMOKE] micro feedback OK")


if __name__ == "__main__":
    asyncio.run(main())
