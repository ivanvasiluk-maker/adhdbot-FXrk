#!/usr/bin/env python3
"""Smoke check that /reset_me is usable by regular testers."""
from __future__ import annotations

import asyncio
import json
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
from db import default_user, get_user, init_db, migrate_db, save_user, log_event, record_action_event, record_user_feedback, save_current_task  # noqa: E402


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
            u["current_task_id"] = "task-old"
            u["trainer_key"] = "beck"
            u["trainer"] = "beck"
            u["points"] = 42
            u["streak"] = 9
            u["done_count"] = 5
            u["return_count"] = 3
            u["is_test_user"] = 1
            u["fast_forward_enabled"] = 1
            u["payment_status"] = "paid"
            u["trial_phase"] = "paid"
            u["access_status"] = "paid"
            u["paid_until"] = "2099-01-01T00:00:00Z"
            u["profile_json"] = {
                "best_skill": "open_only",
                "successful_skills": ["open_only"],
                "user_model_events": [{"event_type": "intervention_helpful", "source_skill_id": "open_only"}],
            }
            await save_user(u, db_path)
            async with bot.aiosqlite.connect(db_path) as db:
                await db.execute("INSERT INTO user_days (day_id, user_id, day_number, calendar_date, status, opened_at) VALUES (?, ?, ?, ?, ?, ?)", ("day-old", uid, 3, "2026-06-01", "active", "2026-06-01T00:00:00Z"))
                await db.execute("INSERT INTO skill_attempts (day_id, user_id, skill_id, task_id, result, created_at) VALUES (?, ?, ?, ?, ?, ?)", ("day-old", uid, "open_only", "task-old", "done", "2026-06-01T00:01:00Z"))
                await db.execute("INSERT INTO user_sessions (session_id, user_id, opened_at, last_activity_at, current_screen) VALUES (?, ?, ?, ?, ?)", ("session-old", uid, "2026-06-01T00:00:00Z", "2026-06-01T00:02:00Z", "training"))
                await db.commit()
            await save_current_task(u, db_path, title="old title", description="old desc", context="old ctx", next_step="old step")
            await record_action_event(uid, db_path, "attempt_started", day_id="day-old", skill_id="open_only", task_id="task-old")
            await record_user_feedback(uid, db_path, "micro", "helped", comment="old feedback", day_id="day-old", day_number=3, skill_id="open_only", trainer_key="beck")
            await log_event(uid, "training", "old_event", {"old": True}, db_path, None)

            msg = FakeMessage(uid, "/reset_me")
            assert await bot.handle_admin_command(msg, u, msg.text) is False
            assert await bot.handle_user_command(msg, u, msg.text) is True
            async with bot.aiosqlite.connect(db_path) as db:
                users_count = (await (await db.execute("SELECT COUNT(*) FROM users WHERE user_id=?", (uid,))).fetchone())[0]
                assert users_count == 0, users_count
            saved = await get_user(uid, db_path)
            assert saved["stage"] == "start"
            assert saved["current_task_title"] is None
            assert saved["points"] == 0
            assert saved["streak"] == 0
            assert saved["done_count"] == 0
            assert saved["return_count"] == 0
            assert saved["is_test_user"] == 0
            assert saved["fast_forward_enabled"] == 0
            assert saved["payment_status"] == "trial"
            assert saved["trial_phase"] == "trial3"
            assert saved["access_status"] == "trial"
            assert saved["paid_until"] is None
            profile_json = json.loads(saved["profile_json"]) if isinstance(saved["profile_json"], str) else saved["profile_json"]
            assert not profile_json.get("successful_skills")
            assert not profile_json.get("user_model_events")
            async with bot.aiosqlite.connect(db_path) as db:
                for table in ("events", "user_days", "skill_attempts", "action_events", "user_feedback", "user_tasks", "user_sessions"):
                    count = (await (await db.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id=?", (uid,))).fetchone())[0]
                    assert count == 0, (table, count)
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
