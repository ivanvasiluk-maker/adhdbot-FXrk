#!/usr/bin/env python3
"""Smoke check that /reset_me is usable by regular testers."""
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
os.environ["ADMIN_IDS"] = "9001"

import bot  # noqa: E402
from db import default_user, get_user, init_db, migrate_db, save_user  # noqa: E402


class FakeFromUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.username = f"tester_{user_id}"
        self.first_name = "Tester"
        self.full_name = "Tester"


class FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class FakeMessage:
    def __init__(self, user_id: int, text: str):
        self.from_user = FakeFromUser(user_id)
        self.chat = FakeChat(user_id)
        self.text = text
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append(text)


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "bot.db")
        old_db = bot.DB_PATH
        bot.DB_PATH = db_path
        try:
            await init_db(db_path)
            await migrate_db(db_path)
            uid = 7777
            u = default_user(uid)
            u["stage"] = "training"
            u["current_task_title"] = "делать бота"
            await save_user(u, db_path)

            msg = FakeMessage(uid, "/reset_me")
            assert await bot.handle_admin_command(msg, u, msg.text) is False
            assert await bot.handle_user_command(msg, u, msg.text) is True
            saved = await get_user(uid, db_path)
            assert saved["stage"] == "start"
            assert any("Профиль полностью сброшен" in x for x in msg.answers)
            assert "Выбери действие" not in "\n".join(msg.answers)

            start_msg = FakeMessage(uid, "/start")
            await bot.cmd_start(start_msg)
            start_answers = "\n".join(start_msg.answers)
            assert "Продолжаем с того места" not in start_answers
            assert "Как к тебе обращаться?" in start_answers
            restarted = await get_user(uid, db_path)
            assert restarted["stage"] == "ask_name"
        finally:
            bot.DB_PATH = old_db

    print("[SMOKE] reset command OK")


if __name__ == "__main__":
    asyncio.run(main())
