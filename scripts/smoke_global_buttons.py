#!/usr/bin/env python3
"""Static/logic smoke checks for global buttons and trainer persona."""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("BOT_TOKEN", "")
os.environ.setdefault("OPENAI_API_KEY", "")

import bot  # noqa: E402
from texts import TRAINERS  # noqa: E402

BOT_SOURCE = (REPO_ROOT / "bot.py").read_text(encoding="utf-8")


def main() -> None:
    expected = {
        "🧭 Моя карта": "map",
        "💪 Давай действие": "action",
        "🧭 Давай действие": "action",
        "💪 Сделать следующий шаг": "action",
        "💪 Продолжить тренировку": "action",
        "💪 Начать тренировку": "action",
        "🌙 Хватит на сегодня": "enough",
        "🌙 Закрыть день": "close_day",
        "🌙 До завтра": "tomorrow",
        "🔁 Ещё круг": "repeat",
        "🔁 Другой навык": "other_skill",
        "🔄 Сменить навык": "change_skill",
        "🔄 Выбрать другой навык": "change_skill",
        "🤷 Не моё": "change_skill",
        "🎭 Сменить тренера": "trainer_switch",
        "🆘 Кризис прокрастинации": "stuck",
        "Ещё": "more",
        "📚 Почему это работает": "why",
        "🧠 Почему этот навык": "why_skill",
        "📚 Подробнее": "details",
        "Пропустить": "skip",
    }
    states = ["ask_name", "await_training_target", "training", "day_core_stop", "offer", "safety_mode"]
    for state in states:
        for text, kind in expected.items():
            assert bot.global_button_kind(text, text.lower()) == kind, (state, text, kind)

    assert "стыд" in bot.trainer_style_line("marsha", "stuck")
    assert "один шаг" in bot.trainer_style_line("skinny", "general")
    assert "Гипотеза" in bot.trainer_style_line("beck", "general")
    for scenario in ("stuck", "change", "map", "continue", "close", "offer", "curator"):
        assert bot.trainer_style_line("marsha", scenario)
        assert bot.trainer_style_line("skinny", scenario)
        assert bot.trainer_style_line("beck", scenario)
    assert bot.trainer_wrap({"trainer_key": "skinny"}, "Текст", "continue").startswith("Если продолжаем")

    assert "handle_global_button(m, u, text)" in BOT_SOURCE
    assert "День уже закрыт, и минимум ты выполнил" in BOT_SOURCE
    assert "Пропуск записал как данные" in BOT_SOURCE
    assert "Ок. Не будем повторять навык, который сейчас не ложится." in BOT_SOURCE
    assert "Задача, карта и прогресс сохранятся." in BOT_SOURCE
    assert bot.global_button_kind("🆘 Кризис", "🆘 кризис") == ""
    assert bot.global_button_kind("🆘 Мне небезопасно", "🆘 мне небезопасно") == ""
    assert bot.has_crisis_safety_signal("не хочу жить", "training") is False
    assert bot.should_open_global_crisis("у меня кризис", "training") is False

    for key in ("marsha", "skinny", "beck"):
        trainer = TRAINERS[key]
        assert trainer.get("display_name")
        assert trainer.get("tone")
        assert trainer.get("grammatical_gender") in {"feminine", "masculine"}
        assert trainer.get("response_templates", {}).get("check_barrier")

    assert bot.trainer_template("skinny", "check_barrier") == "Сейчас проверим, что именно ломает вход."
    assert bot.trainer_template("marsha", "check_barrier") == "Давай спокойно посмотрим, что именно сейчас слишком трудно."
    assert bot.trainer_template("beck", "check_barrier") == "Проверим, какой фактор запускает избегание: оценка, неопределённость или перегруз."
    assert bot.button_fits_current_state("🧠 Да, уточни", {"stage": "analysis_details"})
    assert bot.button_fits_current_state("💪 Нет, давай пробовать", {"stage": "analysis_details"})
    assert bot._is_analysis_clarify_yes("🧠 Да, уточни", "🧠 да, уточни")
    assert bot._is_analysis_clarify_yes("Да, уточни", "да, уточни")
    assert bot._is_analysis_clarify_no("💪 Нет, давай пробовать", "💪 нет, давай пробовать")
    assert "Что тяжелее" in bot._analysis_clarify_question({"trainer_key": "beck"}, "fear", 0)
    assert bot._analysis_clarify_keyboard("fear", 0).keyboard
    assert "Slash commands are explicit navigation/debug intents" in BOT_SOURCE
    assert "u[\"stage\"] = \"analysis_details\"" in BOT_SOURCE
    assert "проверяла" not in BOT_SOURCE.lower()

    print("[SMOKE] global buttons OK")


if __name__ == "__main__":
    main()
