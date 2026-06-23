#!/usr/bin/env python3
"""Smoke checks for post-payment full-mode onboarding."""
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
from db import default_user, init_db, migrate_db, save_user, get_user, update_user_profile  # noqa: E402


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "bot.db")
        old_db = bot.DB_PATH
        bot.DB_PATH = db_path
        try:
            await init_db(db_path)
            await migrate_db(db_path)
            u = default_user(555)
            u["current_task_title"] = "доработать бота"
            u["current_next_physical_step"] = "открыть файл с правками на 10 секунд"
            await save_user(u, db_path)
            await update_user_profile(u["user_id"], {
                "confirmed_signals": ["пользователь описал задачу: доработать бота"],
                "main_hypothesis": "вход в задачу может расплываться без физического шага",
            }, db_path, source="smoke")
            await bot.grant_paid_access(u, "smoke_payment", {"days": 30})
            saved = await get_user(u["user_id"], db_path)
            assert int(saved["full_mode"]) == 1
            assert saved["full_mode_started_at"]
            assert saved["full_mode_until"]

            profile = await bot.get_user_profile(u["user_id"], db_path)
            plan = bot.build_full_mode_plan(saved, profile)
            text = bot.full_mode_welcome_text(plan)
            assert "Полный режим включён" in text
            assert "На ближайшие 3 дня мы проверим" in text
            assert "Первый персональный эксперимент" in text
            assert "открыть файл с правками" in text
            assert "страх оценки выше среднего" not in text
            assert "уже есть первый рабочий навык" not in text
            assert "уменьшение шага сработало лучше давления" not in text

            low_data_user = default_user(556)
            await save_user(low_data_user, db_path)
            low_plan = bot.build_full_mode_plan(low_data_user, {})
            low_text = bot.full_mode_welcome_text(low_plan)
            assert "Пока данных мало" in low_text
            assert "не будет делать вид, что уже знает тебя полностью" in low_text
        finally:
            bot.DB_PATH = old_db

    print("[SMOKE] full mode OK")


if __name__ == "__main__":
    asyncio.run(main())
