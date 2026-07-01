#!/usr/bin/env python3
"""Smoke checks for trainer switching through buttons without losing context."""
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
from texts import kb_more_actions, kb_trainer_switch  # noqa: E402


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


def keyboard_texts(markup) -> set[str]:
    return {button.text for row in getattr(markup, "keyboard", []) for button in row}


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

            assert "🎭 Сменить тренера" in keyboard_texts(kb_more_actions)
            switch_buttons = keyboard_texts(kb_trainer_switch)
            assert "🤍 Марша — мягко" in switch_buttons
            assert "🐈‍⬛ Скинни — чётко" in switch_buttons
            assert "🧠 Бек — с объяснениями" in switch_buttons
            assert "↩️ Оставить текущего тренера" in switch_buttons

            uid = 9201
            u = default_user(uid)
            u.update({
                "chat_id": uid,
                "stage": "training",
                "has_started_training": 1,
                "done_count": 1,
                "trainer_key": "marsha",
                "day": 1,
                "current_day_id": "day-trainer-switch",
                "current_state": bot.STATE_AWAITING_RESULT,
                "current_action_id": "action-stays-active",
                "daily_skill_id": "open_only",
                "daily_skill_name": "Открыть без таймера",
                "current_task_title": "делать бота",
            })
            await save_user(u, bot.DB_PATH)

            u, screen = await send(uid, "🎭 Сменить тренера")
            assert "Твой текущий тренер" in screen
            assert "Задача, карта и прогресс сохранятся" in screen
            assert u["stage"] == "trainer_switch"
            assert u["current_action_id"] == "action-stays-active"
            assert u["daily_skill_id"] == "open_only"

            u, changed = await send(uid, "🧠 Бек — с объяснениями")
            assert "Теперь с тобой Бек" in changed
            assert "Текущий подход остаётся тем же" in changed
            assert "с объяснением механизма" in changed
            assert "Текущий подход на месте" in changed
            assert u["trainer_key"] == "beck"
            assert u["current_action_id"] == "action-stays-active"
            assert u["daily_skill_id"] == "open_only"
            assert u["current_task_title"] == "делать бота"
            profile = await get_user_profile(uid, bot.DB_PATH)
            assert profile.get("trainer_current_mode") == "beck"
            assert profile.get("trainer_switch_history")

            u, map_text = await send(uid, "🧭 Моя карта")
            assert "Текущий тренер:" in map_text
            assert "Бек" in map_text

            u, _ = await send(uid, "🎭 Сменить тренера")
            u, same_text = await send(uid, "🧠 Бек — с объяснениями")
            assert "Бек уже активен. Оставляем текущий стиль." in same_text
            assert "Твой текущий тренер" not in same_text
            assert u["trainer_key"] == "beck"

            closed_uid = 9202
            closed = default_user(closed_uid)
            closed.update({
                "chat_id": closed_uid,
                "stage": "waiting_next_day",
                "has_started_training": 1,
                "trainer_key": "skinny",
                "day": 1,
                "current_day_id": "day-trainer-switch-closed",
                "current_state": bot.STATE_DAY_CLOSED,
                "day_closed": 1,
                "today_closed": 1,
                "day_status": "closed",
            })
            await save_user(closed, bot.DB_PATH)
            closed, screen = await send(closed_uid, "🎭 Сменить тренера")
            assert "Твой текущий тренер" in screen
            closed, closed_text = await send(closed_uid, "🧠 Бек — с объяснениями")
            assert "Стиль сохранён. Завтра тебя будет вести Бек." in closed_text
            assert closed["trainer_key"] == "beck"
            assert closed["stage"] == "waiting_next_day"
            closed_profile = await get_user_profile(closed_uid, bot.DB_PATH)
            assert closed_profile.get("trainer_current_mode") == "beck"
        finally:
            bot.DB_PATH = old_db_path

    print("[SMOKE] trainer switch OK")


if __name__ == "__main__":
    asyncio.run(main())
