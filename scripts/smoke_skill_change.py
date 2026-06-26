#!/usr/bin/env python3
"""Smoke checks for user-requested skill replacement."""
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
from db import default_user, init_db, migrate_db, save_user, get_user, get_action_metrics, get_user_profile  # noqa: E402


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
        self.answers: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs):
        self.answers.append(text)


async def send(uid: int, text: str):
    msg = FakeMessage(uid, text)
    await bot.main_flow(msg)
    return await get_user(uid, bot.DB_PATH), "\n".join(msg.answers)


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old_db_path = bot.DB_PATH
        bot.DB_PATH = str(Path(tmp) / "bot.db")
        try:
            await init_db(bot.DB_PATH)
            await migrate_db(bot.DB_PATH)
            uid = 9101
            u = default_user(uid)
            u.update({
                "chat_id": uid,
                "stage": "training",
                "has_started_training": 1,
                "day": 1,
                "current_day_id": "day-skill-change",
                "current_state": bot.STATE_AWAITING_RESULT,
                "current_action_id": "act-skill-change",
                "day_core_skill_id": "bad_first_step",
                "day_core_skill_date": bot.local_date_for_user(u),
                "current_core_skill_id": "bad_first_step",
                "current_core_skill_date": bot.local_date_for_user(u),
                "daily_skill_id": "bad_first_step",
                "daily_skill_name": "Плохой черновик",
            })
            await save_user(u, bot.DB_PATH)

            assert "🔄 Сменить навык" in {button.text for row in bot.action_keyboard().keyboard for button in row}
            u, prompt = await send(uid, "🔄 Сменить навык")
            assert "Не будем повторять навык" in prompt
            assert u["stage"] == "skill_change_reason"

            u, changed = await send(uid, "😬 Слишком тревожно / страшно")
            assert "Навык заменён" in changed
            assert "Это не откат и не провал" in changed
            assert "Сначала тело, потом задача" in changed
            assert u["daily_skill_name"] == "Сначала тело, потом задача"
            assert u["day_core_skill_id"] == "body_before_task"
            metrics = await get_action_metrics(uid, bot.DB_PATH, day_id="day-skill-change")
            assert metrics["today"]["skill_skipped"] == 0
            profile = await get_user_profile(uid, bot.DB_PATH)
            events = profile.get("user_model_events") or []
            assert any(e.get("event_type") == "intervention_not_helpful" for e in events)
            assert any(e.get("event_type") == "intervention_offered" and e.get("source_skill_id") == "body_before_task" for e in events)
            assert "помогает" not in changed.lower()

            uid2 = 9102
            u2 = default_user(uid2)
            u2.update({"chat_id": uid2, "stage": "training", "has_started_training": 1, "day": 1, "current_day_id": "day-skill-change-meaning", "current_state": bot.STATE_AWAITING_RESULT, "current_action_id": "act-skill-change-2", "day_core_skill_id": "open_only", "day_core_skill_date": bot.local_date_for_user(u2)})
            await save_user(u2, bot.DB_PATH)
            u2, prompt2 = await send(uid2, "🔄 Сменить навык")
            u2, meaning = await send(uid2, "🤷 Не понимаю, зачем это делать")
            assert "Вернуть смысл шага" in meaning
            assert u2["stage"] == "skill_change_meaning"
            u2, changed2 = await send(uid2, "это освобождает меня позже")
            assert "Навык заменён" in changed2
            assert "что освободится позже" in changed2
        finally:
            bot.DB_PATH = old_db_path

    print("[SMOKE] skill change OK")


if __name__ == "__main__":
    asyncio.run(main())
