#!/usr/bin/env python3
"""Smoke checks for day/session/attempt separation in bot routing."""
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
os.environ["ADMIN_IDS"] = "63001"

import bot  # noqa: E402
from db import (  # noqa: E402
    default_user,
    init_db,
    migrate_db,
    save_user,
    get_user,
    ensure_user_day,
    attempt_count_for_day,
    get_user_day_status,
)


class FakeFromUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.username = f"day_{user_id}"
        self.first_name = "Day"


class FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class FakeMessage:
    def __init__(self, user_id: int, text: str = ""):
        self.from_user = FakeFromUser(user_id)
        self.chat = FakeChat(user_id)
        self.text = text
        self.voice = None
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append(str(text))
        return None


def joined(message: FakeMessage) -> str:
    return "\n".join(message.answers)


async def send(uid: int, text: str) -> tuple[dict, FakeMessage]:
    message = FakeMessage(uid, text)
    await bot.main_flow(message)
    return await get_user(uid, bot.DB_PATH), message


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old_db = bot.DB_PATH
        bot.DB_PATH = str(Path(tmp) / "bot.db")
        try:
            await init_db(bot.DB_PATH)
            await migrate_db(bot.DB_PATH)

            uid = 63001
            user = default_user(uid)
            user.update({
                "chat_id": uid,
                "stage": "training",
                "has_started_training": 1,
                "day": 1,
                "current_skill": "open_only",
                "pending_skill_id": "open_only",
                "daily_skill_id": "open_only",
                "daily_skill_name": "Открыть задачу",
                "done_count": 7,
            })
            day_id = await ensure_user_day(user, bot.DB_PATH, calendar_date="2026-06-26", skill_id="open_only", skill_name="Открыть задачу")
            user["current_day_id"] = day_id
            await save_user(user, bot.DB_PATH)

            # 1. Three extra rounds stay in the same product day and create three attempts.
            for _ in range(3):
                user, repeat_msg = await send(uid, "🔁 Ещё круг")
                assert user["current_day_id"] == day_id
                assert "Ещё одна попытка сегодня. Продолжаем тот же навык." in joined(repeat_msg)
                assert "🌱 Новый день" not in joined(repeat_msg)
                assert "Вчера мы увидели" not in joined(repeat_msg)
            assert await attempt_count_for_day(day_id, bot.DB_PATH) == 3

            # 2. Closing the day marks the active day closed.
            user, enough_msg = await send(uid, "🌙 Хватит на сегодня")
            assert "Закрыть день или просто сделать паузу?" in joined(enough_msg)
            user, close_msg = await send(uid, "✅ Закрыть день")
            assert await get_user_day_status(day_id, bot.DB_PATH) == "closed"
            if "Что сегодня было полезнее всего?" in joined(close_msg):
                user, close_msg = await send(uid, "🧩 Маленький конкретный шаг")
            assert "До завтра. Новый навык откроется после смены календарного дня." in joined(close_msg)

            # 3. Action after close is blocked until a real next day/test transition.
            user, blocked_msg = await send(uid, "💪 Давай действие")
            assert user["current_day_id"] == day_id
            assert "день уже закрыт" in joined(blocked_msg).lower() or "На сегодня достаточно" in joined(blocked_msg)
            assert "🧩 Навык дня" not in joined(blocked_msg)

            # 4-5. force_next_day closes/opens atomically, preserves total progress, resets daily attempts.
            before_done = int(user.get("done_count") or 0)
            user, force_msg = await send(uid, "/force_next_day")
            assert "Тестовый переход выполнен. Открыт День 2." in joined(force_msg)
            assert user["current_day_id"] != day_id
            assert int(user.get("done_count") or 0) == before_done
            assert int(user.get("skill_attempts_today") or 0) == 0
            assert await attempt_count_for_day(user["current_day_id"], bot.DB_PATH) == 0
            assert await get_user_day_status(user["current_day_id"], bot.DB_PATH) == "active"
        finally:
            bot.DB_PATH = old_db

    print("[SMOKE] day flow OK")


if __name__ == "__main__":
    asyncio.run(main())
