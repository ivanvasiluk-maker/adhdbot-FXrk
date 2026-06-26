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
        self.answers: list[str] = []

    async def answer(self, text: str, reply_markup=None, **kwargs):
        self.answers.append(text)


def keyboard_texts(markup) -> set[str]:
    return {button.text for row in getattr(markup, "keyboard", []) for button in row}


async def send(uid: int, text: str):
    msg = FakeMessage(uid, text)
    await bot.main_flow(msg)
    return await get_user(uid, bot.DB_PATH), "\n".join(msg.answers)


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
            assert "🆘 Мне небезопасно" in failed_buttons

            uid = 9301
            await seed_user(uid)
            u, prompt = await send(uid, "🎙️ Опишу голосом или текстом")
            assert "Я сначала попробую понять" in prompt
            assert u["stage"] == "stuck_reason_text"

            u, validation = await send(uid, "не понимаю нахуя")
            assert "Похоже" in validation
            assert "зачем вообще" in validation
            assert "Что ближе?" in validation
            assert "Минимальный шаг" not in validation
            assert u["stage"] == "stuck_validation_choice"
            profile = await get_user_profile(uid, bot.DB_PATH)
            assert profile.get("last_free_stuck_hypothesis") == "meaning"

            u, changed = await send(uid, "🧭 Не вижу смысла в самой задаче")
            assert "Навык заменён" in changed
            assert "Вернуть смысл шага" in changed

            uid2 = 9302
            await seed_user(uid2, stage="stuck_reason_text")
            u2, safety_validation = await send(uid2, "я устал, ничего не хочу, всё бессмысленно")
            assert "насколько тебе безопасно" in safety_validation
            assert u2["stage"] == "stuck_validation_choice"
            u2, safety = await send(uid2, "🟡 Не уверен(а), насколько я в безопасности")
            assert "Сейчас не режим продуктивности" in safety
            assert u2["safety_mode"] in {"triage", "urgent"}

            uid3 = 9303
            await seed_user(uid3, stage="stuck_reason_text")
            u3, self_attack = await send(uid3, "я опять всё просрал, ненавижу себя")
            assert "самокритика" in self_attack
            assert "Минимальный шаг" not in self_attack
            u3, calm = await send(uid3, "🤍 Нужно сначала успокоиться")
            assert "Минимальный шаг" in calm or "возвращение контроля" in calm
        finally:
            bot.DB_PATH = old_db_path

    print("[SMOKE] stuck validation OK")


if __name__ == "__main__":
    asyncio.run(main())
