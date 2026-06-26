#!/usr/bin/env python3
"""Smoke checks for honest action-event metrics."""
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
    ensure_user_day,
    create_skill_attempt,
    record_action_event,
    get_action_metrics,
)


class FakeFromUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.username = f"metrics_{user_id}"
        self.first_name = "Metrics"


class FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class FakeMessage:
    def __init__(self, user_id: int, text: str):
        self.from_user = FakeFromUser(user_id)
        self.chat = FakeChat(user_id)
        self.text = text
        self.voice = None
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs):
        self.answers.append(str(text))
        return None


async def send(user_id: int, text: str):
    msg = FakeMessage(user_id, text)
    await bot.main_flow(msg)
    return msg


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "bot.db")
        await init_db(db_path)
        await migrate_db(db_path)
        u = default_user(990)
        await save_user(u, db_path)
        day1 = await ensure_user_day(u, db_path, calendar_date="2026-06-23", skill_id="open_only", skill_name="Открыть")

        await record_action_event(u["user_id"], db_path, "slip_reported", day_id=day1, skill_id="open_only")
        metrics = await get_action_metrics(u["user_id"], db_path, day_id=day1)
        assert metrics["today"]["slips"] == 1
        assert metrics["period"]["slips"] == 1
        assert metrics["today"]["micro_approaches"] == 0

        await record_action_event(u["user_id"], db_path, "returned_after_slip", day_id=day1, skill_id="open_only")
        await record_action_event(u["user_id"], db_path, "attempt_completed_self_reported", day_id=day1, skill_id="open_only")
        await record_action_event(u["user_id"], db_path, "too_hard_reported", day_id=day1, skill_id="open_only")
        await record_action_event(u["user_id"], db_path, "skill_skipped", day_id=day1, skill_id="open_only")
        metrics = await get_action_metrics(u["user_id"], db_path, day_id=day1)
        assert metrics["today"]["returns_after_slip"] == 1
        assert metrics["today"]["micro_approaches"] == 1
        assert metrics["today"]["too_hard"] == 1
        assert metrics["today"]["skill_skipped"] == 1

        u["day"] = 2
        u["current_day_id"] = None
        day2 = await ensure_user_day(u, db_path, calendar_date="2026-06-24", skill_id="bad_draft", skill_name="Черновик")
        await create_skill_attempt(u, db_path, skill_id="bad_draft", task_id="task-2", result="started")
        metrics_day2 = await get_action_metrics(u["user_id"], db_path, day_id=day2)
        assert metrics_day2["today"]["slips"] == 0
        assert metrics_day2["today"]["micro_approaches"] == 0
        assert metrics_day2["period"]["slips"] == 1
        assert metrics_day2["period"]["micro_approaches"] == 1
        assert metrics_day2["period"]["attempts_started"] == 1

        old_bot_db = bot.DB_PATH
        bot.DB_PATH = db_path
        try:
            flow_user = default_user(991)
            flow_user.update({
                "chat_id": 991,
                "stage": "training",
                "has_started_training": 1,
                "day": 1,
                "current_skill": "open_only",
                "pending_skill_id": "open_only",
                "daily_skill_id": "open_only",
                "daily_skill_name": "Открыть",
                "current_state": "AWAITING_RESULT",
                "current_action_id": "act_metrics_slip",
            })
            flow_day = await ensure_user_day(flow_user, db_path, calendar_date="2026-06-23", skill_id="open_only", skill_name="Открыть")
            flow_user["current_day_id"] = flow_day
            await save_user(flow_user, db_path)

            await send(991, "🟡 Застрял / не вышло")
            await send(991, "📱 Залип")
            flow_metrics = await get_action_metrics(991, db_path, day_id=flow_day)
            assert flow_metrics["today"]["slips"] == 1
            assert flow_metrics["today"]["micro_approaches"] == 0

            await send(991, "✅ Сделал")
            flow_metrics = await get_action_metrics(991, db_path, day_id=flow_day)
            assert flow_metrics["today"]["returns_after_slip"] == 1
            assert flow_metrics["today"]["micro_approaches"] == 1

            hard_user = default_user(992)
            hard_user.update({"chat_id": 992, "stage": "training", "has_started_training": 1, "day": 1, "current_skill": "open_only", "pending_skill_id": "open_only", "current_state": "AWAITING_RESULT", "current_action_id": "act_metrics_hard"})
            hard_day = await ensure_user_day(hard_user, db_path, calendar_date="2026-06-23", skill_id="open_only", skill_name="Открыть")
            hard_user["current_day_id"] = hard_day
            await save_user(hard_user, db_path)
            await send(992, "🟡 Застрял / не вышло")
            await send(992, "😣 Слишком сложно")
            hard_metrics = await get_action_metrics(992, db_path, day_id=hard_day)
            assert hard_metrics["today"]["too_hard"] == 1
            assert hard_metrics["today"]["micro_approaches"] == 0

            await record_action_event(992, db_path, "skill_skipped", day_id=hard_day, skill_id="open_only")
            hard_metrics = await get_action_metrics(992, db_path, day_id=hard_day)
            assert hard_metrics["today"]["skill_skipped"] == 1
            assert hard_metrics["today"]["attempts_started"] == 0
        finally:
            bot.DB_PATH = old_bot_db

    print("[SMOKE] action metrics OK")


if __name__ == "__main__":
    asyncio.run(main())
