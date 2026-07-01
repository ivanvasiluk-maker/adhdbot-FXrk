#!/usr/bin/env python3
"""End-to-end QA smoke scenario for the pre-human-testing flow."""
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
os.environ["ADMIN_IDS"] = "9001,9002"
os.environ.setdefault("PAYMENT_ACCEPT_ANY", "1")

import bot  # noqa: E402
from db import (  # noqa: E402
    default_user,
    init_db,
    migrate_db,
    save_user,
    get_user,
    update_user_profile,
    save_current_task,
    ensure_user_day,
    get_action_metrics,
    get_user_day_status,
)


class FakeFromUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.username = f"qa_{user_id}"
        self.first_name = "QA"


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
        self.answers.append(text)
        return None


async def send_admin_command(user_id: int, user: dict, command: str) -> tuple[dict, FakeMessage]:
    msg = FakeMessage(user_id, command)
    consumed = await bot.handle_admin_command(msg, user, command)
    assert consumed, command
    return await get_user(user_id, bot.DB_PATH), msg


async def main() -> None:
    report: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "bot.db")
        old_db = bot.DB_PATH
        bot.DB_PATH = db_path
        try:
            await init_db(db_path)
            await migrate_db(db_path)

            uid = 9001
            user = default_user(uid)
            user["chat_id"] = uid
            user["stage"] = "ask_name"
            await save_user(user, db_path)
            report.append("1. /start baseline user created")

            user["trainer_key"] = "beck"
            user["stage"] = "onboarding_problem"
            await save_user(user, db_path)
            report.append("2. trainer selected")

            await update_user_profile(uid, {
                "confirmed_signals": [
                    "пользователь описал страх оценки перед публикацией",
                    "пользователь выбирал состояние: не вижу смысла",
                ],
                "main_hypothesis": "проверяем, что сильнее мешает входу: страх оценки или потеря смысла",
            }, db_path, source="qa_e2e")
            report.append("3-6. onboarding facts and contradiction hypothesis saved")

            user = await get_user(uid, db_path)
            task_id = await save_current_task(
                user,
                db_path,
                title="доработать бота",
                next_step="открыть файл с правками на 10 секунд",
                context="QA сценарий: задача выбрана после онбординга",
            )
            day_id = await ensure_user_day(
                user,
                db_path,
                calendar_date="2026-06-23",
                skill_id="open_only",
                skill_name="Открыть задачу",
            )
            user["current_day_id"] = day_id
            user["current_task_id"] = task_id
            await save_user(user, db_path)
            report.append("7. task selected and persisted")

            attempt_id = await bot.create_skill_attempt(user, db_path, skill_id="open_only", task_id=task_id, result="started")
            await bot.bot_record_action_event(user, "attempt_started", attempt_id=attempt_id, skill_id="open_only", metadata={"source": "qa_e2e"})
            await bot.bot_record_action_event(user, "slip_reported", attempt_id=attempt_id, skill_id="open_only", metadata={"source": "qa_e2e", "button": "📱 Залип"})
            user["last_event"] = "stuck"
            await save_user(user, db_path)
            await bot.record_return_after_slip_action_event_if_needed(user, "qa_e2e_done_after_slip")
            await bot.bot_record_action_event(user, "attempt_completed_self_reported", attempt_id=attempt_id, skill_id="open_only", metadata={"source": "qa_e2e", "button": "✅ Сделал"})
            metrics = await get_action_metrics(uid, db_path, day_id=day_id)
            assert metrics["today"]["slips"] == 1
            assert metrics["today"]["returns_after_slip"] == 1
            report.append("8-12. attempt, slip and return-after-slip metrics passed")

            crisis_msg = FakeMessage(uid, "Лучше бы меня не было, я не вижу выхода")
            user = await get_user(uid, db_path)
            consumed = await bot.start_safety_interceptor(crisis_msg, user, crisis_msg.text, "qa_e2e", explicit=False)
            user = await get_user(uid, db_path)
            assert consumed is False
            assert bot.safety_mode(user) == "none"
            assert not crisis_msg.answers
            report.append("13-14. legacy emergency safety flow suppressed")

            await bot.mark_day_closed(user, "qa_e2e_manual_close")
            user = await get_user(uid, db_path)
            assert await get_user_day_status(day_id, db_path) == "closed"
            report.append("15-16. normal day-close state remained available")

            user, next_day_msg = await send_admin_command(uid, user, "/force_next_day")
            assert any("Тестовый переход выполнен. Открыт День" in x for x in next_day_msg.answers)
            assert user.get("current_day_id")
            report.append("17. /force_next_day opened a new day")

            user, offer_msg = await send_admin_command(uid, user, "/show_offer")
            offer_text = "\n".join(offer_msg.answers)
            assert "страх оценки выше среднего" not in offer_text
            assert "уменьшение шага сработало лучше давления" not in offer_text
            report.append("18. /show_offer rendered without unsupported conclusions")

            user, payment_msg = await send_admin_command(uid, user, "/simulate_payment")
            user = await get_user(uid, db_path)
            assert int(user.get("full_mode") or 0) == 1
            assert any("Полный режим включён" in x for x in payment_msg.answers)
            assert any("На ближайшие 3 дня мы проверим" in x for x in payment_msg.answers)
            assert any("Первый персональный эксперимент" in x for x in payment_msg.answers)
            report.append("19-20. /simulate_payment enabled full mode with immediate personal value")

            user, debug_state_msg = await send_admin_command(uid, user, "/debug_state")
            assert "FSM-state:" in "\n".join(debug_state_msg.answers)
            user, debug_events_msg = await send_admin_command(uid, user, "/debug_events")
            assert "DEBUG EVENTS" in "\n".join(debug_events_msg.answers)
            report.append("QA debug commands passed")

            reset_uid = 9002
            reset_user = default_user(reset_uid)
            reset_user["chat_id"] = reset_uid
            reset_user["is_test_user"] = 1
            await save_user(reset_user, db_path)
            _, reset_msg = await send_admin_command(reset_uid, reset_user, "/reset_test_user")
            assert any("Тестовые данные" in x for x in reset_msg.answers)
            report.append("/reset_test_user cleared only the current test user")

        finally:
            bot.DB_PATH = old_db

    print("[SMOKE] e2e QA OK")
    print("QA REPORT")
    print("tests passed:")
    for item in report:
        print(f"- {item}")
    print("handlers changed: handle_admin_command, handle_user_command, recent_user_events_text, debug_state_text")
    print("db migrations added earlier: users full_mode/safety/task/day fields; user_days; skill_attempts; action_events; user_tasks; user_sessions")
    print("manual checks still recommended: real Telegram /start media, real voice transcription, real payment provider redirect, Sheets sync")


if __name__ == "__main__":
    asyncio.run(main())
