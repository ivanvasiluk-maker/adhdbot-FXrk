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

    print("[SMOKE] action metrics OK")


if __name__ == "__main__":
    asyncio.run(main())
