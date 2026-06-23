#!/usr/bin/env python3
"""Smoke checks for day/session/attempt separation."""
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
    get_user,
    ensure_user_day,
    create_skill_attempt,
    attempt_count_for_day,
    close_user_day,
    get_user_day_status,
)


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "bot.db")
        await init_db(db_path)
        await migrate_db(db_path)

        u = default_user(777)
        u["day"] = 1
        u["done_count"] = 5
        await save_user(u, db_path)
        u = await get_user(777, db_path)

        day_id = await ensure_user_day(u, db_path, calendar_date="2026-06-23", skill_id="open_only", skill_name="Открыть задачу")
        u["current_day_id"] = day_id
        await save_user(u, db_path)

        attempt_ids = []
        for _ in range(3):
            attempt_ids.append(await create_skill_attempt(u, db_path, skill_id="open_only", task_id="same-task", result="started"))
        assert len(set(attempt_ids)) == 3
        assert await attempt_count_for_day(day_id, db_path) == 3
        assert u["current_day_id"] == day_id

        await close_user_day(u, db_path)
        assert await get_user_day_status(day_id, db_path) == "closed"

        total_done = u["done_count"]
        u["day"] = 2
        u["current_day_id"] = None
        new_day_id = await ensure_user_day(u, db_path, calendar_date="2026-06-24", skill_id="bad_draft", skill_name="Плохой черновик")
        assert new_day_id != day_id
        assert u["done_count"] == total_done
        assert await attempt_count_for_day(new_day_id, db_path) == 0

    print("[SMOKE] day attempts OK")


if __name__ == "__main__":
    asyncio.run(main())
