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
    kb_failed,
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
            "current_state": bot.STATE_PAUSED,
            "state_version": 0,
            "current_action_id": None,
            "current_day_id": "day_smoke",
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

    assert {"💪 Сделать следующий шаг", "⚡ Я застрял", "🧭 Моя карта", "🌙 Закрыть день"}.issubset(keyboard_texts(kb_training_main))
    assert "🆘 Кризис" not in keyboard_texts(kb_training_main)
    assert "📊 Прогресс" not in keyboard_texts(kb_more_actions)
    assert keyboard_texts(kb_skill_card) == {"✅ Сделал", "🟡 Застрял / не вышло", "⏸ Пауза"}
    assert keyboard_texts(kb_failed) == {
        "📱 Ушёл в телефон / YouTube",
        "😬 Страшно, стыдно, боюсь ошибиться",
        "🧠 Слишком много всего",
        "🔋 Нет сил",
        "🤷 Не понимаю, зачем это делать",
        "🎙️ Опишу голосом или текстом",
        "🆘 Мне небезопасно",
    }

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
            assert "Твоя карта" in last_text(map_msg) or "Твоя рабочая карта" in last_text(map_msg) or "Коротко по карте сегодня" in last_text(map_msg), last_text(map_msg)

            # After opening the map, the same post-action menu must still work when that button
            # belongs to the current menu; stale done-menu buttons in day_core_stop fall back.
            note_msg = await send(uid, "📌 Что изменилось?")
            assert "потерял контекст" in last_text(note_msg).lower(), last_text(note_msg)

        await set_post_action_user(uid, db_path, "done", rounds=1)
        repeat_msg = await send(uid, "🔁 Ещё круг")
        assert "Ещё одна попытка сегодня" in all_text(repeat_msg) or "потерял контекст" in last_text(repeat_msg).lower(), last_text(repeat_msg)

        await set_post_action_user(uid, db_path, "done", rounds=1)
        finish_msg = await send(uid, "⏸ Пауза")
        finish_text = all_text(finish_msg).lower()
        assert "шаг уже завершён" in finish_text, all_text(finish_msg)

        await set_post_action_user(uid, db_path, "day_core_stop", rounds=4)
        why_msg = await send(uid, "📚 Почему это работает")
        assert "работает" in last_text(why_msg).lower() or "повтор" in last_text(why_msg).lower(), last_text(why_msg)

        await set_post_action_user(uid, db_path, "day_core_stop", rounds=4)
        tomorrow_msg = await send(uid, "🌙 До завтра")
        if "Что сегодня было полезнее всего?" in all_text(tomorrow_msg):
            tomorrow_msg = await send(uid, "🧩 Маленький конкретный шаг")
        assert "Сегодня ты сделал:" in all_text(tomorrow_msg), all_text(tomorrow_msg)
        assert "Это не оценка продуктивности" in all_text(tomorrow_msg), all_text(tomorrow_msg)

        # Stale buttons from another screen must not launch old branches or show an empty prompt.
        await set_post_action_user(uid, db_path, "day_core_stop", rounds=4)
        stale_repeat_msg = await send(uid, "🔁 Ещё круг")
        assert "потерял контекст" in last_text(stale_repeat_msg).lower() or "день уже закрыт" in last_text(stale_repeat_msg).lower(), last_text(stale_repeat_msg)
        assert "Навык дня" not in all_text(stale_repeat_msg), all_text(stale_repeat_msg)

        u = await get_user(uid, db_path)
        u.update({"stage": "ask_name", "name": ""})
        await save_user(u, db_path)
        stale_done_msg = await send(uid, "✅ Сделал")
        assert "шаг уже завершён" in last_text(stale_done_msg).lower(), last_text(stale_done_msg)
        u = await get_user(uid, db_path)
        assert not u.get("name"), u.get("name")

        await set_post_action_user(uid, db_path, "training", rounds=1)
        training_map_msg = await send(uid, "🧭 Моя карта")
        assert "Твоя карта" in last_text(training_map_msg) or "Коротко по карте сегодня" in last_text(training_map_msg), last_text(training_map_msg)

        await set_post_action_user(uid, db_path, "done", rounds=1)
        more_msg = await send(uid, "Ещё")
        assert "потерял контекст" in last_text(more_msg).lower(), last_text(more_msg)


        await set_post_action_user(uid, db_path, "training", rounds=1)
        u = await get_user(uid, db_path)
        u.update({
            "current_state": bot.STATE_AWAITING_RESULT,
            "state_version": 1,
            "current_action_id": "act_smoke",
            "current_day_id": "day_smoke",
        })
        await save_user(u, db_path)
        stuck_msg = await send(uid, "🟡 Застрял / не вышло")
        assert "не провал" in last_text(stuck_msg).lower(), last_text(stuck_msg)
        assert "📱 Ушёл в телефон / YouTube" in keyboard_texts(stuck_msg.answers[-1]["reply_markup"]), last_text(stuck_msg)

        phone_msg = await send(uid, "📱 Ушёл в телефон / YouTube")
        phone_text = last_text(phone_msg)
        assert "Телефон вне руки" in phone_text, phone_text
        assert "Отодвинуть телефон на 30 секунд" in phone_text, phone_text
        assert keyboard_texts(phone_msg.answers[-1]["reply_markup"]) == {"✅ Сделал", "🟡 Застрял / не вышло", "➕ Ещё 2 минуты", "🌙 Закрыть подход", "🔄 Сменить навык"}

        await set_post_action_user(uid, db_path, "training", rounds=1)
        u = await get_user(uid, db_path)
        u.update({
            "current_state": bot.STATE_AWAITING_RESULT,
            "state_version": 1,
            "current_action_id": "act_smoke_voice",
            "current_day_id": "day_smoke",
        })
        await save_user(u, db_path)
        await send(uid, "🟡 Застрял / не вышло")
        describe_msg = await send(uid, "🎙️ Опишу голосом или текстом")
        assert "опиши как есть" in last_text(describe_msg).lower(), last_text(describe_msg)
        reflected_msg = await send(uid, "Боюсь сделать плохо и стыдно")
        assert "Не надо сразу требовать" in last_text(reflected_msg), last_text(reflected_msg)
        reflected_msg = await send(uid, "📂 Открыть файл на 10 секунд")
        assert "Плохой черновик" in last_text(reflected_msg), last_text(reflected_msg)
        assert "Написать одну плохую строку" in last_text(reflected_msg), last_text(reflected_msg)

        repeat_stuck_msg = await send(uid, "🟡 Застрял / не вышло")
        assert "не провал" in last_text(repeat_stuck_msg).lower(), last_text(repeat_stuck_msg)
        repeated_cognitive_msg = await send(uid, "🧠 Слишком много всего")
        repeated_text = last_text(repeated_cognitive_msg)
        assert "даже маленький шаг к задаче слишком дорогой" in repeated_text, repeated_text
        assert "положи ладонь на стол" in repeated_text, repeated_text
        assert keyboard_texts(repeated_cognitive_msg.answers[-1]["reply_markup"]) == {"✅ Сделал", "🟡 Застрял / не вышло", "➕ Ещё 2 минуты", "🌙 Закрыть подход", "🔄 Сменить навык"}


        await set_post_action_user(uid, db_path, "training", rounds=1)
        u = await get_user(uid, db_path)
        u.update({
            "current_state": bot.STATE_AWAITING_RESULT,
            "state_version": 1,
            "current_action_id": "act_success",
            "current_day_id": "day_success",
            "success_repeat_count": 0,
        })
        await save_user(u, db_path)
        success_msg = await send(uid, "✅ Сделал")
        if "бот понял" in last_text(success_msg) or "было понятно" in last_text(success_msg):
            success_msg = await send(uid, "🟢 Да, очень похоже")
        assert "Подход засчитан" in last_text(success_msg) or "Спасибо, записал" in last_text(success_msg), last_text(success_msg)
        if "Подход засчитан" in last_text(success_msg):
            assert "доказывать, что ты продуктивный" in last_text(success_msg), last_text(success_msg)
            assert keyboard_texts(success_msg.answers[-1]["reply_markup"]) == {"➕ Ещё 2 минуты", "🌙 Закрыть подход", "🗣️ Что помогло?"}

        repeat1_msg = await send(uid, "➕ Ещё 2 минуты")
        assert "Ещё одна попытка сегодня" in last_text(repeat1_msg) or "день" in last_text(repeat1_msg).lower(), last_text(repeat1_msg)
        done2_msg = await send(uid, "✅ Сделал")
        assert "Подход засчитан" in last_text(done2_msg) or "шаг уже завершён" in last_text(done2_msg), last_text(done2_msg)
        repeat2_msg = await send(uid, "➕ Ещё 2 минуты")
        assert "Ещё одна попытка сегодня" in last_text(repeat2_msg) or "день" in last_text(repeat2_msg).lower(), last_text(repeat2_msg)
        done3_msg = await send(uid, "✅ Сделал")
        assert "Подход засчитан" in last_text(done3_msg) or "шаг уже завершён" in last_text(done3_msg), last_text(done3_msg)
        limit_msg = await send(uid, "➕ Ещё 2 минуты")
        assert "На сегодня достаточно тренировать вход" in last_text(limit_msg), last_text(limit_msg)
        assert keyboard_texts(limit_msg.answers[-1]["reply_markup"]) == {"🌙 Закрыть подход", "💪 Другое действие"}

        help_msg = await send(uid, "🗣️ Что помогло?")
        assert "добровольный вопрос" in last_text(help_msg), last_text(help_msg)
        helper_note_msg = await send(uid, "таймер")
        assert "Записал" in last_text(helper_note_msg), last_text(helper_note_msg)

        await set_post_action_user(uid, db_path, "training", rounds=1)
        skip_msg = await send(uid, "Пропустить")
        assert "Пропуск" in last_text(skip_msg) and "данные" in last_text(skip_msg), last_text(skip_msg)


        print("[SMOKE] post-action buttons OK")


if __name__ == "__main__":
    asyncio.run(run())
