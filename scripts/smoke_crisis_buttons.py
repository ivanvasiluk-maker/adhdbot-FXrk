#!/usr/bin/env python3
"""Static/logic smoke checks for crisis keyboards and button handlers."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("BOT_TOKEN", "")
os.environ.setdefault("OPENAI_API_KEY", "")

import bot  # noqa: E402
from texts import (  # noqa: E402
    kb_crisis_mode,
    kb_crisis_stabilize,
    kb_crisis_tool_select,
    kb_crisis_effect,
    kb_crisis_action,
    kb_social_support,
    crisis_tool_text,
    social_support_prompt_text,
)

BOT_SOURCE = (REPO_ROOT / "bot.py").read_text(encoding="utf-8")


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
    assert "✅ Всё выбрал" in tool_buttons
    assert_source_contains("✅ Всё выбрал", '"✅ Всё выбрал"', '"всё выбрал"')

    selected_kb = bot.crisis_multiselect_keyboard(["attention_escape", "anxiety_loop"])
    selected_texts = keyboard_texts(selected_kb)
    assert "✅ Залип" in selected_texts
    assert "✅ Тревога" in selected_texts
    assert "⬜ Боюсь ошибки" in selected_texts

    combo = bot.combined_crisis_tool_text(["attention_escape", "anxiety_loop", "perfectionism"])
    assert "тревога → страх ошибки → уход в залипание" in combo
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
    assert "Ещё один кусок карты — опоры" in social_support_prompt_text()

    assert bot.should_open_global_crisis("🆘 Кризис", "waiting_next_day") is True
    assert bot.should_open_global_crisis("у меня кризис", "training") is True
    assert bot.should_open_global_crisis("проверь оффер и кризис в тексте диагностики", "await_problem_text") is False
    assert bot.should_open_global_crisis("проверь оффер и кризис в тексте диагностики", "run_analysis") is False

    assert bot.crisis_pattern_from_text("могу навредить себе, есть план") == "high_risk"
    assert bot.crisis_pattern_from_text("меня не понимают и я один") == "social_pain"
    assert await bot.classify_crisis_pattern("high_risk") == "high_risk"

    # Stabilization/effect buttons are still wired.
    for button_text in keyboard_texts(kb_crisis_stabilize):
        assert_source_contains(button_text, button_text, button_text.replace("ё", "е"))
    for button_text in keyboard_texts(kb_crisis_effect):
        assert bot.crisis_effect_code(button_text) in {"better", "same", "no"}

    print("[SMOKE] crisis buttons OK")


if __name__ == "__main__":
    asyncio.run(main())
