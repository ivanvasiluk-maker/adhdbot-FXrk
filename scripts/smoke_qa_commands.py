#!/usr/bin/env python3
"""Smoke checks for per-user QA command access."""
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
os.environ["TEST_CHEAT_CODE"] = "SMOKE_QA_CODE"

import bot  # noqa: E402
from db import default_user, get_user, init_db, migrate_db, save_user  # noqa: E402


class FakeFromUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.username = f"qa_{user_id}"
        self.first_name = "QA"


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
        old_code = bot.TEST_CHEAT_CODE
        bot.DB_PATH = db_path
        bot.TEST_CHEAT_CODE = "SMOKE_QA_CODE"
        try:
            await init_db(db_path)
            await migrate_db(db_path)
            uid = 7778
            u = default_user(uid)
            u["stage"] = "training"
            await save_user(u, db_path)

            blocked = FakeMessage(uid, "/force_next_day")
            assert await bot.handle_admin_command(blocked, u, blocked.text) is True
            assert any("QA-команда недоступна" in x for x in blocked.answers)
            assert "Выбери действие" not in "\n".join(blocked.answers)

            access = FakeMessage(uid, "/test_access SMOKE_QA_CODE")
            assert await bot.handle_admin_command(access, u, access.text) is False
            assert await bot.handle_user_command(access, u, access.text) is True
            u = await get_user(uid, db_path)
            assert int(u["is_test_user"]) == 1
            assert int(u["fast_forward_enabled"]) == 1

            next_day = FakeMessage(uid, "/force_next_day")
            assert await bot.handle_admin_command(next_day, u, next_day.text) is True
            assert any("Тестовый переход выполнен. Открыт День" in x for x in next_day.answers)

            u = await get_user(uid, db_path)
            offer = FakeMessage(uid, "/show_offer")
            assert await bot.handle_admin_command(offer, u, offer.text) is True
            assert "Выбери действие" not in "\n".join(offer.answers)
            offer_with_mention = FakeMessage(uid, "/show_offer@TestBot")
            assert await bot.handle_admin_command(offer_with_mention, u, offer_with_mention.text) is True
            assert "Выбери действие" not in "\n".join(offer_with_mention.answers)
        finally:
            bot.DB_PATH = old_db
            bot.TEST_CHEAT_CODE = old_code

    print("[SMOKE] QA commands OK")


if __name__ == "__main__":
    asyncio.run(main())
