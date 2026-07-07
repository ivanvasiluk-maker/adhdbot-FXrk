#!/usr/bin/env python3
"""Smoke checks for persistent task context."""
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
from db import default_user, init_db, migrate_db, save_user, save_current_task, update_current_task_step, get_user_tasks  # noqa: E402


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "bot.db")
        await init_db(db_path)
        await migrate_db(db_path)
        u = default_user(321)
        await save_user(u, db_path)

        first_task_id = await save_current_task(u, db_path, title="делать бота")
        assert u["current_task_title"] == "делать бота"
        skill_text = bot.build_current_skill_text({"name": "Открыть задачу", "steps": ["Открой место задачи"]}, u=u)
        assert skill_text.count("🧩 Навык:") == 1
        assert skill_text.count("Минимум:") == 1
        assert "Открой «делать бота»" in skill_text
        assert "сегодняшняя задача" not in skill_text
        assert "Открой место задачи" not in skill_text

        extracted = bot.extract_task_context_from_text("Нужно дописать презентацию клиенту к завтра, но боюсь сделать плохо и испытать стыд")
        assert extracted["current_task_name"] == "дописать презентацию клиенту", extracted
        assert extracted["current_task_object"] == "презентация", extracted
        assert extracted["current_deadline"] == "завтра", extracted
        assert extracted["current_task_fear"] == "сделать плохо и испытать стыд", extracted
        presentation_user = {"current_task_name": "дописать презентацию клиенту", "current_task_object": "презентация"}
        presentation_text = bot.build_current_skill_text({"name": "Плохой черновик", "steps": ["Открой место задачи", "Напиши плохую строку"]}, u=presentation_user)
        assert "Открой презентацию для клиента" in presentation_text, presentation_text
        assert "Напиши одну плохую строку для презентации" in presentation_text, presentation_text
        assert "Напиши одну плохую строку для презентацию" not in presentation_text, presentation_text
        assert "выбранной задаче" not in presentation_text

        assert bot.task_needs_physical_step("делать бота") is True
        await update_current_task_step(u, db_path, "открыть файл с правками на 10 секунд")
        assert "открыть файл с правками на 10 секунд" in bot.returning_to_task_text(u)

        second_task_id = await save_current_task(u, db_path, title="написать пост")
        assert second_task_id != first_task_id
        tasks = await get_user_tasks(u["user_id"], db_path)
        by_id = {task["task_id"]: task for task in tasks}
        assert by_id[first_task_id]["status"] == "paused"
        assert by_id[second_task_id]["status"] == "active"

        crisis_user = dict(u)
        crisis_user["safety_mode"] = "support"
        crisis_user["safety_last_risk"] = "no"
        assert bot.safety_mode(crisis_user) == "support"
        # The aftercare resume button should not silently inject the task into a skill card.
        assert "safety_user_chose_resume_later" in Path(REPO_ROOT / "bot.py").read_text(encoding="utf-8")

    print("[SMOKE] task context OK")


if __name__ == "__main__":
    asyncio.run(main())
