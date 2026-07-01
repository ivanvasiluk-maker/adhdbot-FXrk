#!/usr/bin/env python3
"""Smoke-test post-action reply keyboard routing.

This covers the button set that appears after a completed approach and after the
core daily limit. These buttons previously fell through to the generic
"Выбери действие" fallback when the stored stage was `done` or `day_core_stop`.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import bot
from db import get_user, get_user_profile, init_db, migrate_db, save_user
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
            "plan_json": '["open_only", "open_only", "task_naming"]',
            "day": 1,
            "today_target": "сегодняшняя задача",
            "current_core_skill_id": "open_only",
            "day_core_skill_id": "open_only",
            "day_core_round_count": rounds,
            "done_count": rounds,
            "day_core_round_date": bot.local_date_for_user(u),
            "current_state": bot.STATE_PAUSED,
            "state_version": 0,
            "current_action_id": None,
            "current_day_id": "day_smoke",
            "day_closed": 1 if stage == "day_core_stop" else 0,
            "today_closed": 1 if stage == "day_core_stop" else 0,
            "day_status": "closed" if stage == "day_core_stop" else "open",
            "last_day_closed_at": bot.local_date_for_user(u) if stage == "day_core_stop" else None,
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

    action_context = bot.build_action_request_context(
        {
            "current_action_id": "act_ctx",
            "current_state": bot.STATE_AWAITING_RESULT,
            "current_task_title": "дописать PR",
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "daily_skill_status": "in_progress",
            "trainer_key": "beck",
            "day_status": "open",
        },
        {
            "successful_skills": ["open_only"],
            "failed_skills": ["open_only"],
            "last_free_stuck_text": "застрял на формулировке",
        },
        {"skills": []},
    )
    assert action_context["active_action"] is True
    assert action_context["day_closed"] is False
    assert action_context["fresh_stuck_text"] == "застрял на формулировке"
    assert action_context["selected_task"] == "дописать PR"
    assert action_context["current_skill_id"] == "open_without_timer"
    assert action_context["skill_history"]["worked"] == ["open_only"]
    assert action_context["skill_history"]["did_not_work"] == ["open_only"]
    assert action_context["trainer_key"] == "beck"

    assert {"💪 Сделать следующий шаг", "⚡ Я застрял", "🆘 Кризис прокрастинации", "🧭 Моя карта", "🌙 Закрыть день"}.issubset(keyboard_texts(kb_training_main))
    assert "🆘 Кризис" not in keyboard_texts(kb_training_main)
    assert "🆘 Мне небезопасно" not in keyboard_texts(kb_training_main)
    assert "📊 Прогресс" not in keyboard_texts(kb_more_actions)
    assert keyboard_texts(kb_skill_card) == {"💪 Начать тренировку", "🤷 Не моё", "🔄 Выбрать другой навык", "🧠 Почему этот навык", "⚡ Я уже застрял"}
    assert bot.STALE_ACTION_CHANGED_TEXT == bot.POST_MINIMUM_CONTINUE_TEXT
    assert "Следующий навык откроется завтра" not in bot.STALE_ACTION_CHANGED_TEXT

    assert keyboard_texts(kb_failed) == {
        "📱 Ушёл в телефон / YouTube",
        "😬 Страшно, стыдно, боюсь ошибиться",
        "🧠 Слишком много всего",
        "🔋 Нет сил",
        "🫨 Тревога и перегруз",
        "🧨 Самокритика после срыва",
        "🤷 Не понимаю, зачем это делать",
        "🎙️ Опишу голосом или текстом",
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
            assert "Твоя карта" in last_text(map_msg) or "Твоя рабочая карта" in last_text(map_msg) or "Коротко по карте сегодня" in last_text(map_msg) or "Твоя короткая карта" in last_text(map_msg), last_text(map_msg)

            # After opening the map, the same post-action menu must still work when that button
            # belongs to the current menu; stale done-menu buttons in day_core_stop fall back.
            note_msg = await send(uid, "📌 Что изменилось?")
            assert "старый экран" in last_text(note_msg).lower() or "день уже закрыт" in last_text(note_msg).lower(), last_text(note_msg)

        await set_post_action_user(uid, db_path, "training", rounds=1)
        not_my_msg = await send(uid, "🤷 Не моё")
        assert "Это не откат" in last_text(not_my_msg), last_text(not_my_msg)
        assert "Новый навык" in last_text(not_my_msg), last_text(not_my_msg)
        profile = await get_user_profile(uid, db_path)
        assert profile.get("last_not_fit_reason") == "not_my_skill", profile
        assert profile.get("last_not_fit_skill"), profile
        assert profile.get("last_better_entry_type") == "not_my_skill", profile

        await set_post_action_user(uid, db_path, "done", rounds=1)
        repeat_msg = await send(uid, "🔁 Ещё круг")
        assert "Ещё одна попытка сегодня" in all_text(repeat_msg) or "следующий шаг" in last_text(repeat_msg).lower() or "старый экран" in last_text(repeat_msg).lower(), last_text(repeat_msg)

        await set_post_action_user(uid, db_path, "done", rounds=1)
        finish_msg = await send(uid, "⏸ Пауза")
        finish_text = all_text(finish_msg).lower()
        assert "минимум на сегодня уже выполнен" in finish_text, all_text(finish_msg)

        await set_post_action_user(uid, db_path, "day_core_stop", rounds=4)
        why_msg = await send(uid, "📚 Почему это работает")
        assert "сегодняшний подход уже закрыт" in last_text(why_msg).lower(), last_text(why_msg)

        await set_post_action_user(uid, db_path, "day_core_stop", rounds=4)
        tomorrow_msg = await send(uid, "🌙 До завтра")
        assert "День уже закрыт." in all_text(tomorrow_msg), all_text(tomorrow_msg)
        assert "Сегодня ты сделал:" not in all_text(tomorrow_msg), all_text(tomorrow_msg)

        # Stale buttons from another screen must not launch old branches or show an empty prompt.
        await set_post_action_user(uid, db_path, "day_core_stop", rounds=4)
        stale_repeat_msg = await send(uid, "🔁 Ещё круг")
        assert "день уже закрыт, и минимум ты выполнил" in last_text(stale_repeat_msg).lower(), last_text(stale_repeat_msg)
        assert keyboard_texts(stale_repeat_msg.answers[-1]["reply_markup"]) == {"✅ Да, ещё один короткий шаг", "🌙 Нет, оставить день закрытым"}
        assert "Навык дня" not in all_text(stale_repeat_msg), all_text(stale_repeat_msg)
        voluntary_msg = await send(uid, "✅ Да, ещё один короткий шаг")
        assert "Добровольный короткий подход" in last_text(voluntary_msg), last_text(voluntary_msg)
        assert "Навык дня" in last_text(voluntary_msg), last_text(voluntary_msg)

        u = await get_user(uid, db_path)
        u.update({"stage": "ask_name", "name": ""})
        await save_user(u, db_path)
        stale_done_msg = await send(uid, "✅ Сделал")
        assert "минимум на сегодня уже выполнен" in last_text(stale_done_msg).lower() or "день уже закрыт" in last_text(stale_done_msg).lower(), last_text(stale_done_msg)
        u = await get_user(uid, db_path)
        assert not u.get("name"), u.get("name")

        await set_post_action_user(uid, db_path, "training", rounds=1)
        u = await get_user(uid, db_path)
        u["current_state"] = bot.STATE_PAUSED
        u["pending_feedback_json"] = json.dumps({"type": "stuck_validation", "kind": "meaning", "text": "застрял на формулировке"}, ensure_ascii=False)
        await save_user(u, db_path)
        action_with_stuck_msg = await send(uid, "💪 Давай действие")
        assert "Я услышал" in last_text(action_with_stuck_msg), last_text(action_with_stuck_msg)
        assert "Навык дня" not in last_text(action_with_stuck_msg), last_text(action_with_stuck_msg)

        await set_post_action_user(uid, db_path, "training", rounds=1)
        training_map_msg = await send(uid, "🧭 Моя карта")
        assert "Твоя карта" in last_text(training_map_msg) or "Коротко по карте сегодня" in last_text(training_map_msg), last_text(training_map_msg)

        await set_post_action_user(uid, db_path, "done", rounds=1)
        more_msg = await send(uid, "Ещё")
        assert "старый экран" in last_text(more_msg).lower(), last_text(more_msg)


        await set_post_action_user(uid, db_path, "training", rounds=1)
        u = await get_user(uid, db_path)
        u.update({
            "current_state": bot.STATE_AWAITING_RESULT,
            "state_version": 1,
            "current_action_id": "act_smoke",
            "current_day_id": "day_smoke",
            "day_closed": 0,
            "today_closed": 0,
            "day_status": "open",
            "last_day_closed_at": None,
        })
        await save_user(u, db_path)
        stuck_msg = await send(uid, "🟡 Застрял / не вышло")
        assert "не провал" in last_text(stuck_msg).lower(), last_text(stuck_msg)
        assert "📱 Ушёл в телефон / YouTube" in keyboard_texts(stuck_msg.answers[-1]["reply_markup"]), last_text(stuck_msg)

        phone_msg = await send(uid, "📱 Ушёл в телефон / YouTube")
        phone_text = last_text(phone_msg)
        assert "Телефон / YouTube / новости" in phone_text, phone_text
        assert "Убрать телефон вне руки" in phone_text, phone_text
        assert keyboard_texts(phone_msg.answers[-1]["reply_markup"]) == {"✅ Сделал", "🟡 Не получилось", "🌙 На сегодня достаточно", "⚙️ Другой вариант"}

        done_feedback_prompt = await send(uid, "✅ Сделал")
        assert "Зафиксируем честно" in last_text(done_feedback_prompt), last_text(done_feedback_prompt)
        assert {"✅ Сделал — стало легче", "😐 Сделал — но легче не стало", "🚪 Сделал — начал задачу", "🟡 Не получилось", "🤷 Не мой навык", "😣 Слишком сложно", "🔄 Нужен другой вход", "⏳ Не пробовал / не успел"}.issubset(keyboard_texts(done_feedback_prompt.answers[-1]["reply_markup"]))
        no_relief_msg = await send(uid, "😐 Сделал — но легче не стало")
        assert "не стало" in last_text(no_relief_msg).lower()
        profile = await get_user_profile(uid, db_path)
        assert profile.get("last_skill_effect") == "not_helpful", profile
        assert "successful_skills" not in profile or not profile.get("successful_skills"), profile

        await set_post_action_user(uid, db_path, "training", rounds=1)
        u = await get_user(uid, db_path)
        u.update({
            "current_state": bot.STATE_AWAITING_RESULT,
            "state_version": 1,
            "current_action_id": "act_smoke_voice",
            "current_day_id": "day_smoke",
            "day_closed": 0,
            "today_closed": 0,
            "day_status": "open",
            "last_day_closed_at": None,
        })
        await save_user(u, db_path)
        await send(uid, "🟡 Застрял / не вышло")
        describe_msg = await send(uid, "🎙️ Опишу голосом или текстом")
        assert "опиши как есть" in last_text(describe_msg).lower(), last_text(describe_msg)
        reflected_msg = await send(uid, "Боюсь сделать плохо и стыдно")
        assert "Главный узел" in last_text(reflected_msg), last_text(reflected_msg)
        assert "Рабочая гипотеза" in last_text(reflected_msg), last_text(reflected_msg)
        assert "Минимальный физический шаг" in last_text(reflected_msg), last_text(reflected_msg)
        assert {"✅ Да, похоже", "🟡 Не совсем", "🔄 Сменить навык", "🧠 Уточнить"}.issubset(keyboard_texts(reflected_msg.answers[-1]["reply_markup"]))
        reflected_msg = await send(uid, "✅ Да, похоже")
        assert "Плохой черновик" in last_text(reflected_msg), last_text(reflected_msg)
        assert "Написать одну плохую строку" in last_text(reflected_msg), last_text(reflected_msg)

        repeat_stuck_msg = await send(uid, "🟡 Застрял / не вышло")
        assert "не провал" in last_text(repeat_stuck_msg).lower(), last_text(repeat_stuck_msg)
        repeated_cognitive_msg = await send(uid, "🧠 Слишком много всего")
        repeated_text = last_text(repeated_cognitive_msg)
        assert "Это не откат" in repeated_text, repeated_text
        assert "даже маленький шаг к задаче слишком дорогой" in repeated_text, repeated_text
        assert "положи ладонь на стол" in repeated_text, repeated_text
        assert keyboard_texts(repeated_cognitive_msg.answers[-1]["reply_markup"]) == {"✅ Сделал", "🟡 Не получилось", "🌙 На сегодня достаточно", "⚙️ Другой вариант"}
        profile = await get_user_profile(uid, db_path)
        assert profile.get("last_not_fit_skill"), profile
        assert profile.get("last_not_fit_reason") in {"overwhelm", "shame", "phone", "energy", "not_my_skill"}, profile
        assert profile.get("last_better_entry_type"), profile
        assert profile.get("current_tolerable_difficulty"), profile


        await set_post_action_user(uid, db_path, "training", rounds=1)
        u = await get_user(uid, db_path)
        u.update({
            "current_state": bot.STATE_AWAITING_RESULT,
            "state_version": 1,
            "current_action_id": "act_success",
            "current_day_id": "day_success",
            "current_core_skill_id": "open_only",
            "current_skill_variant_id": "open_only",
            "current_core_skill_date": bot.local_date_for_user(u),
            "day_core_skill_id": "open_only",
            "day_core_skill_date": bot.local_date_for_user(u),
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "success_repeat_count": 0,
        })
        await save_user(u, db_path)
        success_msg = await send(uid, "✅ Сделал")
        assert "Зафиксируем честно" in last_text(success_msg), last_text(success_msg)
        success_msg = await send(uid, "✅ Сделал — стало легче")
        assert "Есть первый сигнал" in last_text(success_msg), last_text(success_msg)
        assert keyboard_texts(success_msg.answers[-1]["reply_markup"]) == {"➕ Ещё 2 минуты", "💪 Продолжить тренировку", "🌙 На сегодня достаточно"}

        repeat1_msg = await send(uid, "➕ Ещё 2 минуты")
        assert "Ещё 2 минуты" in last_text(repeat1_msg), last_text(repeat1_msg)
        assert "Напиши одно слово" in last_text(repeat1_msg), last_text(repeat1_msg)
        done2_msg = await send(uid, "✅ Сделал")
        assert "Ещё один короткий шаг засчитан" in last_text(done2_msg), last_text(done2_msg)
        assert "➕ Ещё 2 минуты" not in keyboard_texts(done2_msg.answers[-1]["reply_markup"]), keyboard_texts(done2_msg.answers[-1]["reply_markup"])
        limit_msg = await send(uid, "➕ Ещё 2 минуты")
        assert "Минимум на сегодня уже выполнен" in last_text(limit_msg), last_text(limit_msg)
        assert keyboard_texts(limit_msg.answers[-1]["reply_markup"]) == {"💪 Продолжить тренировку", "🌙 На сегодня достаточно"}

        enough_msg = await send(uid, "🌙 На сегодня достаточно")
        assert "тренировка остаётся доступной" in last_text(enough_msg), last_text(enough_msg)

        await set_post_action_user(uid, db_path, "success_menu", rounds=1)
        u = await get_user(uid, db_path)
        u.update({
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "daily_skill_status": "completed",
            "done_count": 1,
            "current_state": bot.STATE_PAUSED,
            "day_closed": 0,
            "today_closed": 0,
            "day_status": "open",
            "last_day_closed_at": None,
        })
        await save_user(u, db_path)
        next_step_msg = await send(uid, "💪 Продолжить тренировку")
        next_step_text = last_text(next_step_msg)
        assert "Это следующий шаг, не повтор старта" in next_step_text, next_step_text
        assert "раз файл уже открыт" in next_step_text, next_step_text
        assert "Лучший доступный шаг сейчас" not in next_step_text, next_step_text

        await set_post_action_user(uid, db_path, "training", rounds=1)
        skip_msg = await send(uid, "Пропустить")
        assert "Пропуск" in last_text(skip_msg) and "данные" in last_text(skip_msg), last_text(skip_msg)


        print("[SMOKE] post-action buttons OK")


if __name__ == "__main__":
    asyncio.run(run())
