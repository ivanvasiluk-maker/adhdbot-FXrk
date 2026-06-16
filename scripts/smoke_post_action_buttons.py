#!/usr/bin/env python3
"""Smoke-test post-action reply keyboard routing.

This covers the button set that appears after a completed approach and after the
core daily limit. These buttons previously fell through to the generic
"Выбери действие" fallback when the stored stage was `done` or `day_core_stop`.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import bot
from db import get_user, init_db, migrate_db, save_user
from texts import (
    MAX_KEYBOARD_BUTTONS,
    kb_done,
    kb_more_actions,
    kb_skill_card,
    kb_training_main,
    keyboard_button_count,
)


class DummyMessage:
    def __init__(self, uid: int, text: str):
        self.text = text
        self.voice = None
        self.from_user = types.SimpleNamespace(id=uid, username="button_smoke_user")
        self.answers: list[dict[str, object]] = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append({"text": text, "reply_markup": reply_markup, "parse_mode": parse_mode})
        return None


def last_text(message: DummyMessage) -> str:
    return str(message.answers[-1]["text"] if message.answers else "")


def all_text(message: DummyMessage) -> str:
    return "\n".join(str(answer.get("text") or "") for answer in message.answers)


async def set_post_action_user(uid: int, db_path: str, stage: str, *, rounds: int = 1):
    u = await get_user(uid, db_path)
    u.update(
        {
            "stage": stage,
            "name": "Иван",
            "trainer_key": "marsha",
            "bucket": "mixed",
            "analysis_json": "{}",
            "plan_json": '["draft_zero", "open_only", "task_naming"]',
            "day": 1,
            "today_target": "сегодняшняя задача",
            "current_core_skill_id": "draft_zero",
            "day_core_skill_id": "draft_zero",
            "day_core_round_count": rounds,
            "day_core_round_date": bot.local_date_for_user(u),
        }
    )
    await save_user(u, db_path)
    return u


async def send(uid: int, text: str) -> DummyMessage:
    message = DummyMessage(uid, text)
    await bot.main_flow(message)
    assert message.answers, f"no answer for {text!r}"
    assert "Выбери действие" not in last_text(message), f"fallback for {text!r}: {last_text(message)}"
    return message


def keyboard_texts(reply_markup) -> set[str]:
    rows = getattr(reply_markup, "keyboard", None) or getattr(reply_markup, "inline_keyboard", None) or []
    return {str(getattr(button, "text", "")) for row in rows for button in row}


def assert_guarded_keyboard(name: str, reply_markup) -> None:
    count = keyboard_button_count(reply_markup)
    assert count <= MAX_KEYBOARD_BUTTONS, f"{name} has {count} buttons, limit is {MAX_KEYBOARD_BUTTONS}"


async def run() -> None:
    for keyboard_name, reply_markup in (
        ("kb_training_main", kb_training_main),
        ("kb_more_actions", kb_more_actions),
        ("kb_skill_card", kb_skill_card),
        ("kb_done", kb_done),
    ):
        assert_guarded_keyboard(keyboard_name, reply_markup)

    assert {"🔄 Сменить тренера", "🆘 Кризис"}.issubset(keyboard_texts(kb_training_main))
    assert {"😣 Слишком сложно", "Пропустить", "🧭 Моя карта"}.issubset(keyboard_texts(kb_skill_card))

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "button-smoke.db")
        bot.DB_PATH = db_path
        bot.SHEETS_WEBHOOK_URL = ""
        await init_db(db_path)
        await migrate_db(db_path)

        uid = 515151

        # Every post-action storage stage must route the done-menu buttons.
        for stage in ("waiting_next_day", "done", "day_core_stop"):
            await set_post_action_user(uid, db_path, stage, rounds=1)
            map_msg = await send(uid, "🧭 Моя карта")
            assert "Твоя карта" in last_text(map_msg), last_text(map_msg)

            # After opening the map, the same post-action menu must still work.
            note_msg = await send(uid, "📌 Что изменилось?")
            assert "что изменилось после шага" in last_text(note_msg).lower(), last_text(note_msg)

        await set_post_action_user(uid, db_path, "done", rounds=1)
        repeat_msg = await send(uid, "🔁 Ещё круг")
        assert "Навык дня" in last_text(repeat_msg), last_text(repeat_msg)

        await set_post_action_user(uid, db_path, "done", rounds=1)
        finish_msg = await send(uid, "🌙 Хватит на сегодня")
        finish_text = all_text(finish_msg).lower()
        assert "день закрыт" in finish_text or "достаточно" in finish_text or "засчит" in finish_text, all_text(finish_msg)

        await set_post_action_user(uid, db_path, "day_core_stop", rounds=4)
        why_msg = await send(uid, "📚 Почему это работает")
        assert "работает" in last_text(why_msg).lower() or "повтор" in last_text(why_msg).lower(), last_text(why_msg)

        await set_post_action_user(uid, db_path, "day_core_stop", rounds=4)
        tomorrow_msg = await send(uid, "🌙 До завтра")
        assert "до завтра" in last_text(tomorrow_msg).lower() or "завтра" in last_text(tomorrow_msg).lower(), last_text(tomorrow_msg)

        await set_post_action_user(uid, db_path, "training", rounds=1)
        training_map_msg = await send(uid, "🧭 Моя карта")
        assert "Твоя карта" in last_text(training_map_msg), last_text(training_map_msg)

        await set_post_action_user(uid, db_path, "training", rounds=1)
        skip_msg = await send(uid, "Пропустить")
        assert "Пропуск тоже данные" in last_text(skip_msg), last_text(skip_msg)

        await set_post_action_user(uid, db_path, "training", rounds=1)
        trainer_msg = await send(uid, "🔄 Сменить тренера")
        assert "Смен осталось" in last_text(trainer_msg), last_text(trainer_msg)

        print("[SMOKE] post-action buttons OK")


if __name__ == "__main__":
    asyncio.run(run())
