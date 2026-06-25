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
from db import get_user, init_db, migrate_db, render_short_user_map, save_user, ensure_user_day, record_action_event, update_user_profile
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

    assert keyboard_texts(kb_training_main) == {"💪 Сделать следующий шаг", "⚡ Я застрял", "🧭 Моя карта", "🌙 Закрыть день"}
    temporarily_removed = {
        "Ещё",
        "👍 Попробую",
        "🔁 Другой навык",
        "🔁 Заменить навык",
        "🧩 Уменьшить шаг",
        "❓ Быстрый тест (5 вопросов)",
        "📊 Прогресс",
    }
    assert not (temporarily_removed & keyboard_texts(kb_training_main))
    assert not (temporarily_removed & keyboard_texts(kb_more_actions))
    assert "🆘 Кризис" not in keyboard_texts(kb_training_main)
    assert "🆘 Мне небезопасно" not in keyboard_texts(kb_training_main)
    assert keyboard_texts(kb_skill_card) == {"✅ Сделал", "🟡 Застрял / не вышло", "⏸ Пауза"}
    assert keyboard_texts(kb_failed) == {
        "📱 Ушёл в телефон / YouTube",
        "😬 Страшно, стыдно, боюсь ошибиться",
        "🧠 Слишком много всего",
        "🔋 Нет сил",
        "🎙️ Опишу голосом или текстом",
    }

    honest_map = render_short_user_map({
        "main_pattern": "fear_of_evaluation",
        "attention_escape_count": 1,
        "confirmed_signals": ["страшно показать недоделанное"],
    })
    assert "🧭 Твоя рабочая карта" in honest_map, honest_map
    assert "Ты сказал:" in honest_map and "страшно показать недоделанное" in honest_map, honest_map
    assert "Пока предполагаем:" in honest_map and "Страх оценки" in honest_map, honest_map
    assert "Уже проверили:" in honest_map and "пока нет проверенного паттерна" in honest_map, honest_map
    assert "маленький шаг помог вернуться к задаче" not in honest_map, honest_map

    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "button-smoke.db")
        bot.DB_PATH = db_path
        bot.SHEETS_WEBHOOK_URL = ""
        await init_db(db_path)
        await migrate_db(db_path)

        uid = 515151



        first_uid = uid + 20
        first_user = await get_user(first_uid, db_path)
        first_user.update({"stage": "await_problem_text", "trainer_key": "marsha", "bucket": "mixed"})
        await save_user(first_user, db_path)
        first_msg = await send(first_uid, "Страшно показать недоделанный отчёт, поэтому ухожу в Telegram")
        first_text = last_text(first_msg)
        assert len(first_msg.answers) == 1, first_msg.answers
        assert "не потому, что ты “ленишься”" in first_text, first_text
        assert "Страшно показать недоделанный отчёт" in first_text, first_text
        assert "Не будем разбирать всё" in first_text, first_text
        assert "Это займёт 30–90 секунд" in first_text, first_text
        assert "После шага можно остановиться" in first_text, first_text
        assert "полная карта" not in first_text.lower() and "рабочая карта" not in first_text.lower(), first_text
        assert keyboard_texts(first_msg.answers[-1]["reply_markup"]) == {"✅ Сделал", "🟡 Застрял / не вышло", "⏸ Пауза", "😑 Не то"}
        first_user = await get_user(first_uid, db_path)
        assert first_user.get("current_action_id"), first_user
        assert first_user.get("stage") == "training", first_user.get("stage")
        not_it_msg = await send(first_uid, "😑 Не то")
        assert "Не буду тянуть тебя в чужую схему" in last_text(not_it_msg), last_text(not_it_msg)
        assert keyboard_texts(not_it_msg.answers[-1]["reply_markup"]) == {"🎯 Не та задача", "😬 Не та причина", "🧩 Шаг слишком большой", "🗣️ Скажу по-своему"}

        offer_uid = uid + 10
        offer_user = await get_user(offer_uid, db_path)
        offer_user.update({"stage": "training", "day": 3, "first_start_date": "2026-06-23", "current_state": bot.STATE_PAUSED})
        await save_user(offer_user, db_path)
        assert await bot.should_show_day3_offer(offer_user, 3) is False
        for day_num, date in ((1, "2026-06-23"), (2, "2026-06-24")):
            offer_user["day"] = day_num
            offer_user["current_day_id"] = None
            day_id = await ensure_user_day(offer_user, db_path, calendar_date=date, skill_id="open_only", skill_name="Open")
            await record_action_event(offer_uid, db_path, "attempt_completed_self_reported", day_id=day_id, skill_id="open_only")
        await record_action_event(offer_uid, db_path, "stuck_reason_selected", day_id=offer_user.get("current_day_id") or f"{offer_uid}:2", skill_id="phone_far_3min")
        await update_user_profile(offer_uid, {"main_hypothesis": "страх оценки делает вход тяжелее", "secondary_hypotheses": ["помогает ли убрать телефон"]}, db_path, source="smoke_offer_ready")
        offer_user = await get_user(offer_uid, db_path)
        offer_user.update({"day": 3, "first_start_date": "2026-06-23"})
        await save_user(offer_user, db_path)
        assert await bot.day3_offer_eligibility(offer_user)
        eligibility = await bot.day3_offer_eligibility(offer_user)
        assert eligibility["ok"] is True, eligibility

        # Every post-action storage stage must route the done-menu buttons.
        for stage in ("waiting_next_day", "done", "day_core_stop"):
            await set_post_action_user(uid, db_path, stage, rounds=1)
            map_msg = await send(uid, "🧭 Моя карта")
            assert "Твоя рабочая карта" in last_text(map_msg), last_text(map_msg)
            assert all(section in last_text(map_msg) for section in ("Ты сказал:", "Пока предполагаем:", "Уже проверили:", "Надо проверить:")), last_text(map_msg)

            # After opening the map, the same post-action menu must still work when that button
            # belongs to the current menu; stale done-menu buttons in day_core_stop fall back.
            note_msg = await send(uid, "📌 Что изменилось?")
            assert "техническая ошибка" in last_text(note_msg).lower(), last_text(note_msg)

        await set_post_action_user(uid, db_path, "done", rounds=1)
        repeat_msg = await send(uid, "🔁 Ещё круг")
        assert "техническая ошибка" in last_text(repeat_msg).lower(), last_text(repeat_msg)
        assert "Навык дня" not in all_text(repeat_msg), all_text(repeat_msg)

        await set_post_action_user(uid, db_path, "done", rounds=1)
        finish_msg = await send(uid, "⏸ Пауза")
        finish_text = all_text(finish_msg).lower()
        assert "техническая ошибка" in finish_text, all_text(finish_msg)

        await set_post_action_user(uid, db_path, "day_core_stop", rounds=4)
        why_msg = await send(uid, "📚 Почему это работает")
        assert "работает" in last_text(why_msg).lower() or "повтор" in last_text(why_msg).lower(), last_text(why_msg)

        await set_post_action_user(uid, db_path, "day_core_stop", rounds=4)
        tomorrow_msg = await send(uid, "🌙 До завтра")
        assert "Сегодня ты сделал:" in all_text(tomorrow_msg), all_text(tomorrow_msg)
        assert "Это не оценка продуктивности" in all_text(tomorrow_msg), all_text(tomorrow_msg)

        # Stale buttons from another screen must not launch old branches or show an empty prompt.
        await set_post_action_user(uid, db_path, "day_core_stop", rounds=4)
        stale_repeat_msg = await send(uid, "🔁 Ещё круг")
        assert "техническая ошибка" in last_text(stale_repeat_msg).lower(), last_text(stale_repeat_msg)
        assert "Навык дня" not in all_text(stale_repeat_msg), all_text(stale_repeat_msg)

        u = await get_user(uid, db_path)
        u.update({"stage": "ask_name", "name": ""})
        await save_user(u, db_path)
        stale_done_msg = await send(uid, "✅ Сделал")
        assert "техническая ошибка" in last_text(stale_done_msg).lower(), last_text(stale_done_msg)
        u = await get_user(uid, db_path)
        assert not u.get("name"), u.get("name")

        await set_post_action_user(uid, db_path, "training", rounds=1)
        training_map_msg = await send(uid, "🧭 Моя карта")
        assert "Твоя рабочая карта" in last_text(training_map_msg), last_text(training_map_msg)
        assert "Это не диагноз" in last_text(training_map_msg), last_text(training_map_msg)

        await set_post_action_user(uid, db_path, "done", rounds=1)
        more_msg = await send(uid, "Ещё")
        assert "техническая ошибка" in last_text(more_msg).lower(), last_text(more_msg)

        await set_post_action_user(uid, db_path, "training", rounds=1)
        u = await get_user(uid, db_path)
        u.update({
            "current_state": bot.STATE_AWAITING_RESULT,
            "current_action_id": "act_old_misunderstood",
            "current_day_id": "day_smoke",
            "current_skill": "draft_zero",
            "pending_skill_id": "draft_zero",
        })
        await save_user(u, db_path)
        misunderstood_msg = await send(uid, "😑 Ты меня не понял")
        assert "Не буду защищать прошлый ответ" in last_text(misunderstood_msg), last_text(misunderstood_msg)
        assert keyboard_texts(misunderstood_msg.answers[-1]["reply_markup"]) == {
            "Не та проблема",
            "Слишком общий ответ",
            "Не тот шаг",
            "Мне страшно / тяжело, а не лень",
            "Объясню по-своему",
        }
        u = await get_user(uid, db_path)
        assert u.get("current_action_id") in {None, ""}, u.get("current_action_id")
        new_step_msg = await send(uid, "Не тот шаг")
        assert "Старый шаг закрыт" in last_text(new_step_msg), last_text(new_step_msg)
        assert "Не повторяю прошлый навык" in last_text(new_step_msg), last_text(new_step_msg)
        assert keyboard_texts(new_step_msg.answers[-1]["reply_markup"]) == {"✅ Сделал", "🟡 Застрял / не вышло", "⏸ Пауза", "😑 Не то"}
        u = await get_user(uid, db_path)
        assert u.get("current_action_id") and u.get("current_action_id") != "act_old_misunderstood", u.get("current_action_id")
        assert u.get("current_skill") != "draft_zero", u.get("current_skill")


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
        assert "не провал, а данные" in last_text(stuck_msg).lower(), last_text(stuck_msg)
        assert "📱 Ушёл в телефон / YouTube" in keyboard_texts(stuck_msg.answers[-1]["reply_markup"]), last_text(stuck_msg)

        phone_msg = await send(uid, "📱 Ушёл в телефон / YouTube")
        phone_text = last_text(phone_msg)
        assert "Телефон вне руки" in phone_text, phone_text
        assert "Отодвинуть телефон на 30 секунд" in phone_text, phone_text
        assert keyboard_texts(phone_msg.answers[-1]["reply_markup"]) == {"✅ Сделал", "🟡 Застрял / не вышло", "⏸ Пауза", "😑 Не то"}

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
        assert "что именно помешало" in last_text(describe_msg).lower(), last_text(describe_msg)
        reflected_msg = await send(uid, "Боюсь сделать плохо и стыдно")
        assert "Плохой черновик" in last_text(reflected_msg), last_text(reflected_msg)
        assert "Написать одну плохую строку" in last_text(reflected_msg), last_text(reflected_msg)

        repeat_stuck_msg = await send(uid, "🟡 Застрял / не вышло")
        assert "не провал, а данные" in last_text(repeat_stuck_msg).lower(), last_text(repeat_stuck_msg)
        repeated_cognitive_msg = await send(uid, "🧠 Слишком много всего")
        repeated_text = last_text(repeated_cognitive_msg)
        assert "даже маленький шаг к задаче слишком дорогой" in repeated_text, repeated_text
        assert "положи ладонь на стол" in repeated_text, repeated_text
        assert keyboard_texts(repeated_cognitive_msg.answers[-1]["reply_markup"]) == {"✅ Сделал", "🟡 Застрял / не вышло", "⏸ Пауза", "😑 Не то"}


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
        assert "Подход засчитан" in last_text(success_msg), last_text(success_msg)
        assert "доказывать, что ты продуктивный" in last_text(success_msg), last_text(success_msg)
        assert keyboard_texts(success_msg.answers[-1]["reply_markup"]) == {"➕ Ещё 2 минуты", "🌙 Закрыть подход", "🗣️ Что помогло?"}

        repeat1_msg = await send(uid, "➕ Ещё 2 минуты")
        assert "Ещё одна попытка сегодня" in last_text(repeat1_msg), last_text(repeat1_msg)
        done2_msg = await send(uid, "✅ Сделал")
        assert "Подход засчитан" in last_text(done2_msg), last_text(done2_msg)
        repeat2_msg = await send(uid, "➕ Ещё 2 минуты")
        assert "Ещё одна попытка сегодня" in last_text(repeat2_msg), last_text(repeat2_msg)
        done3_msg = await send(uid, "✅ Сделал")
        assert "Подход засчитан" in last_text(done3_msg), last_text(done3_msg)
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
