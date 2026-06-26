#!/usr/bin/env python3
"""Static/logic smoke checks for crisis keyboards and button handlers."""
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
from db import default_user, USER_FIELDS, init_db, migrate_db, save_user, get_user, ensure_user_day, get_user_day_status  # noqa: E402
from texts import (  # noqa: E402
    kb_crisis_mode,
    kb_crisis_stabilize,
    kb_crisis_tool_select,
    kb_crisis_effect,
    kb_crisis_action,
    kb_social_support,
    crisis_tool_text,
    social_support_prompt_text,
    social_support_map_text,
)

BOT_SOURCE = (REPO_ROOT / "bot.py").read_text(encoding="utf-8")


class FakeFromUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.username = f"safety_{user_id}"
        self.first_name = "Safety"


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
        self.answers.append(str(text))
        return None


def joined_answers(message: FakeMessage) -> str:
    return "\n".join(message.answers)


def keyboard_texts(kb) -> list[str]:
    texts: list[str] = []
    for row in kb.keyboard:
        for button in row:
            texts.append(button.text)
    return texts


def assert_source_contains(button: str, *markers: str) -> None:
    if not any(marker in BOT_SOURCE for marker in markers):
        raise AssertionError(f"Crisis button {button!r} has no handler marker among {markers!r}")


async def main() -> None:
    # Entry buttons: every visible crisis entry button must have an explicit branch.
    entry_buttons = keyboard_texts(kb_crisis_mode)
    assert "🎙 Голосом" in entry_buttons
    assert "✍️ Текстом" in entry_buttons
    assert "🔘 Выбрать состояние кнопками" in entry_buttons
    assert "⬅️ Назад" in entry_buttons
    assert_source_contains("🎙 Голосом", '"🎙 Голосом"', '"голос" in low')
    assert_source_contains("✍️ Текстом", '"✍️ Текстом"', '"текст" in low')
    assert_source_contains("🔘 Выбрать состояние кнопками", '"выбрать" in low', '"кноп" in low')
    assert_source_contains("⬅️ Назад", '"⬅️ Назад"', '"назад" in low')

    # Multi-select buttons map to a crisis pattern and toggle visually.
    tool_buttons = keyboard_texts(kb_crisis_tool_select)
    expected_patterns = {
        "⬜ Не могу начать": "task_entry_block",
        "⬜ Залип": "attention_escape",
        "⬜ Боюсь ошибки": "perfectionism",
        "⬜ Всё слишком большое": "overwhelm",
        "⬜ Нет сил": "low_energy",
        "⬜ Сам себя сжираю": "self_attack",
        "⬜ Тревога": "anxiety_loop",
        "⬜ Другое": "unknown",
    }
    for button_text, expected in expected_patterns.items():
        assert button_text in tool_buttons
        assert bot._crisis_pattern_from_button(button_text) == expected
        assert bot._crisis_pattern_from_button(button_text.replace("⬜", "✅")) == expected
    assert bot.detect_crisis_stack("залип в ютуб и скроллю", []) == "ZALIP"
    assert bot.detect_crisis_stack("паника, страшно, не могу дышать", []) == "ANXIETY"
    assert bot.detect_crisis_stack("нет сил, лежу, ничего не хочу", []) == "DEPRESSIVE_LOW_ENERGY"
    assert bot.detect_crisis_stack("стыдно, я ничтожество и облажался", []) == "SHAME_SELF_ATTACK"
    assert bot.detect_crisis_stack("меня не понимают и никому не нужен", []) == "NOT_UNDERSTOOD"
    assert bot.detect_crisis_stack("лучше бы меня не было, есть план", []) == "HIGH_RISK"
    assert bot.detect_crisis_stack("", ["⬜ Залип", "⬜ Тревога", "⬜ Боюсь ошибки"]) == "ZALIP"
    assert "✅ Всё выбрал" in tool_buttons
    assert_source_contains("✅ Всё выбрал", '"✅ Всё выбрал"', '"всё выбрал"')

    selected_kb = bot.crisis_multiselect_keyboard(["attention_escape", "anxiety_loop"])
    selected_texts = keyboard_texts(selected_kb)
    assert "✅ Залип" in selected_texts
    assert "✅ Тревога" in selected_texts
    assert "⬜ Боюсь ошибки" in selected_texts

    combo = bot.combined_crisis_tool_text(["attention_escape", "anxiety_loop", "perfectionism"])
    assert "тревога → страх ошибки → уход в залипание" in combo
    assert "Значит, сначала не давим на продуктивность" in combo
    assert "Стек на 3–5 минут" in combo
    assert "Плохой черновик" in combo

    expected_stack_markers = {
        "attention_escape": ["Это не лень. Это захват внимания", "Минимум: один клик"],
        "anxiety_loop": ["Сначала тело. Потом задача", "Это тревога, не приказ"],
        "low_energy": ["Сейчас задача не “соберись”", "сесть и сделать глоток воды"],
        "social_pain": ["контакт и опора", "Можешь быть на связи 10 минут"],
        "high_risk": ["режим безопасности", "не заменяю живую помощь"],
    }
    for pattern, markers in expected_stack_markers.items():
        text = crisis_tool_text(pattern)
        for marker in markers:
            assert marker in text, (pattern, marker, text)

    action_buttons = keyboard_texts(kb_crisis_action)
    for button_text in ["✅ Сделал", "😣 Не могу", "🧩 Ещё меньше", "🆘 Мне всё ещё плохо"]:
        assert button_text in action_buttons
        assert_source_contains(button_text, button_text, button_text.replace("ё", "е"))

    support_buttons = keyboard_texts(kb_social_support)
    for button_text in [
        "👤 Один человек, кому можно написать",
        "👥 Коллега / партнёр по работе",
        "🏠 Семья / близкий",
        "🧑‍💻 Чат / группа / комьюнити",
        "🚶 Мне помогает быть среди людей",
        "🙅 Сейчас нет опоры",
        "✍️ Написать свой вариант",
    ]:
        assert button_text in support_buttons
    assert "Ещё один важный кусок карты — опоры" in social_support_prompt_text()
    assert "Ответь коротко или выбери кнопками" in social_support_prompt_text()
    assert "Социальные опоры:" in social_support_map_text()
    assert "нужен ли внешний старт" in social_support_map_text()

    assert bot.should_open_global_crisis("🆘 Кризис", "waiting_next_day") is True
    assert bot.should_open_global_crisis("у меня кризис", "training") is True
    assert bot.should_open_global_crisis("проверь оффер и кризис в тексте диагностики", "await_problem_text") is False
    assert bot.should_open_global_crisis("проверь оффер и кризис в тексте диагностики", "run_analysis") is False
    assert bot.has_crisis_safety_signal("не хочу жить, есть план сегодня", "training") is True
    assert bot.has_crisis_safety_signal("паническая атака, не могу дышать", "training") is True
    assert bot.has_crisis_safety_signal("проверь оффер и кризис в тексте диагностики", "await_problem_text") is False
    assert bot.has_crisis_safety_signal("Лучше бы меня не было, я не вижу выхода", "ask_name") is True
    assert bot.safety_signal_details("Лучше бы меня не было, я не вижу выхода")["high"] is True
    assert bot.has_crisis_safety_signal("Мне страшно оставаться одному", "await_training_target") is True
    assert bot.safety_signal_details("🆘 Кризис", explicit=True)["triggered"] is True
    assert "crisis_return_blocked_until_safety_done" in BOT_SOURCE
    assert "crisis_productivity_return_deferred_until_safety_aftercare" in BOT_SOURCE
    assert 'await start_safety_interceptor(m, u, reason_text, "crisis_tool_high_risk", explicit=True)' in BOT_SOURCE
    assert 'json.dumps({"type": "crisis_aftercare"}' in BOT_SOURCE
    assert "safety_blocked_productivity_button" in BOT_SOURCE
    assert "safety_blocked_callback" in BOT_SOURCE
    assert "await handle_safety_callback(c, u, data)" in BOT_SOURCE
    assert 'await start_safety_interceptor(m, u, text, "training_main", explicit=True)' in BOT_SOURCE

    # Dynamic global-interceptor checks: crisis text/button must preempt normal states.
    with tempfile.TemporaryDirectory() as tmp:
        old_db_path = bot.DB_PATH
        bot.DB_PATH = str(Path(tmp) / "bot.db")
        try:
            await init_db(bot.DB_PATH)
            await migrate_db(bot.DB_PATH)

            uid = 42001
            onboarding_user = default_user(uid)
            onboarding_user.update({"chat_id": uid, "stage": "ask_name"})
            await save_user(onboarding_user, bot.DB_PATH)
            onboarding_msg = FakeMessage(uid, "Лучше бы меня не было, я не вижу выхода")
            await bot.main_flow(onboarding_msg)
            onboarding_after = await get_user(uid, bot.DB_PATH)
            assert bot.safety_mode(onboarding_after) == "urgent"
            assert onboarding_after["stage"] == "safety_mode"
            assert "Сейчас не режим продуктивности" in joined_answers(onboarding_msg)
            assert "Как тебя зовут" not in joined_answers(onboarding_msg)

            task_uid = 42002
            task_user = default_user(task_uid)
            task_user.update({"chat_id": task_uid, "stage": "training", "has_started_training": 1, "current_skill": "open_only"})
            await save_user(task_user, bot.DB_PATH)
            task_msg = FakeMessage(task_uid, "Мне страшно оставаться одному")
            await bot.main_flow(task_msg)
            task_after = await get_user(task_uid, bot.DB_PATH)
            assert bot.safety_mode(task_after) in {"triage", "urgent"}
            assert "Сейчас не режим продуктивности" in joined_answers(task_msg)
            assert "открыть задачу" not in joined_answers(task_msg).lower()

            button_uid = 42003
            button_user = default_user(button_uid)
            button_user.update({"chat_id": button_uid, "stage": "offer"})
            await save_user(button_user, bot.DB_PATH)
            button_msg = FakeMessage(button_uid, "🆘 Кризис")
            await bot.main_flow(button_msg)
            button_after = await get_user(button_uid, bot.DB_PATH)
            assert bot.safety_mode(button_after) == "triage"
            assert "Сейчас не режим продуктивности" in joined_answers(button_msg)

            urgent_uid = 42004
            urgent_user = default_user(urgent_uid)
            urgent_user.update({"chat_id": urgent_uid, "stage": "safety_mode", "safety_mode": "urgent", "safety_last_risk": "yes"})
            await save_user(urgent_user, bot.DB_PATH)
            old_button_msg = FakeMessage(urgent_uid, "💪 Давай действие")
            await bot.main_flow(old_button_msg)
            urgent_after = await get_user(urgent_uid, bot.DB_PATH)
            assert bot.safety_mode(urgent_after) == "urgent"
            assert "Сейчас только безопасность" in joined_answers(old_button_msg)
            assert "Навык дня" not in joined_answers(old_button_msg)

            close_uid = 42005
            close_user = default_user(close_uid)
            close_user.update({"chat_id": close_uid, "stage": "safety_mode", "safety_mode": "support", "safety_last_risk": "no", "day": 1})
            day_id = await ensure_user_day(close_user, bot.DB_PATH, calendar_date="2026-06-26", skill_id="open_only", skill_name="Открыть задачу")
            close_user["current_day_id"] = day_id
            await save_user(close_user, bot.DB_PATH)
            close_msg = FakeMessage(close_uid, "🛑 На сегодня достаточно")
            await bot.main_flow(close_msg)
            close_after = await get_user(close_uid, bot.DB_PATH)
            assert bot.safety_mode(close_after) == "none"
            assert await get_user_day_status(day_id, bot.DB_PATH) == "closed"
            assert "Никаких новых навыков сейчас" in joined_answers(close_msg)
            assert "Навык дня" not in joined_answers(close_msg)
        finally:
            bot.DB_PATH = old_db_path

    user = default_user(42)
    assert user["safety_mode"] == "none"
    assert user["safety_last_risk"] == "unknown"
    assert user["safety_contact_status"] == "not_asked"
    for field in ("safety_mode", "safety_last_risk", "safety_contact_status", "safety_resume_context"):
        assert field in USER_FIELDS

    assert bot.crisis_pattern_from_text("могу навредить себе, есть план") == "high_risk"
    assert bot.crisis_pattern_from_text("меня не понимают и я один") == "social_pain"
    assert await bot.classify_crisis_pattern("high_risk") == "high_risk"

    # New-day skill selection should avoid repeating the same launch skills.
    assert bot.select_daily_skill({"user_id": 1}, {"skill_history": ["open_only", "open_without_timer"]})["skill_id"] != "open_without_timer"
    assert bot.select_daily_skill({"user_id": 1}, {"skill_history": ["task_naming", "name_task_one_word"], "attention_escape_count": 2})["skill_id"] == "phone_away_3_min"
    assert bot.select_daily_skill({"user_id": 1}, {"skill_history": [], "energy_pattern": "low_start_energy"})["skill_id"] == "body_first"
    assert "данных пока мало" in bot.new_day_insights_text({})
    assert "залипание усиливается" not in bot.new_day_insights_text({})
    new_day_text = bot.build_new_day_intro({"user_id": 1}, {"skill_id": "phone_away_3_min", "name": "Телефон вне руки на 3 минуты"}, {})
    assert "🌱 Новый день" in new_day_text
    assert "🧩 Навык дня" in new_day_text
    assert bot.action_keyboard() is bot.kb_active_skill
    assert bot.should_route_action_request("💪 Давай действие", "💪 давай действие", {"stage": "training", "has_started_training": 1}) is True
    assert bot.should_route_action_request("💪 Дать сегодняшний навык", "💪 дать сегодняшний навык", {}) is True
    assert bot.should_route_action_request("🔁 Ещё круг", "🔁 ещё круг", {}) is True
    short_mode_buttons = [b.text for row in bot.kb_short_mode_main.keyboard for b in row]
    assert "💪 Сделать следующий шаг" in short_mode_buttons
    assert "⚡ Я застрял" in short_mode_buttons
    assert "💳 Полный режим" not in short_mode_buttons
    assert "Ограничения короткого режима" in bot.stay_free_text()
    assert bot.choose_replacement_skill({"current_skill": "open_without_timer"}, ["open_without_timer"]) != "open_without_timer"
    assert bot.day_closed_today({"today_closed": 1, "last_day_closed_at": bot.local_date_for_user({})}, {}) is True
    assert "На сегодня достаточно" in bot.enough_for_today_text()
    assert "Никаких новых навыков сейчас" in BOT_SOURCE
    metrics_text = bot.action_metrics_text({"today": {"micro_approaches": 1, "slips": 1, "returns_after_slip": 1, "step_reductions": 0}, "period": {"micro_approaches": 2, "slips": 1, "returns_after_slip": 1, "step_reductions": 1}})
    assert "микро-подходов" in metrics_text
    assert "отмеченных залипаний" in metrics_text
    assert "Это не оценка твоей продуктивности" in metrics_text
    assert "возврат после залипания +1" in bot.return_after_stuck_text()

    # Stabilization/effect buttons are still wired.
    for button_text in keyboard_texts(kb_crisis_stabilize):
        assert_source_contains(button_text, button_text, button_text.replace("ё", "е"))
    for button_text in keyboard_texts(kb_crisis_effect):
        assert bot.crisis_effect_code(button_text) in {"better", "same", "no"}

    print("[SMOKE] crisis buttons OK")


if __name__ == "__main__":
    asyncio.run(main())
