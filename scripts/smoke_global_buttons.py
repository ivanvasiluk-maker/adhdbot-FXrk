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
        "🆘 Кризис": "crisis",
        "🧭 Моя карта": "map",
        "💪 Давай действие": "action",
        "🌙 Хватит на сегодня": "enough",
        "🌙 Закрыть день": "close_day",
        "🌙 До завтра": "tomorrow",
        "🔁 Ещё круг": "repeat",
        "🔁 Другой навык": "other_skill",
        "Ещё": "more",
        "📚 Почему это работает": "why",
        "📚 Подробнее": "details",
        "Пропустить": "skip",
    }
    states = ["ask_name", "await_training_target", "training", "day_core_stop", "offer", "safety_mode"]
    for state in states:
        for text, kind in expected.items():
            assert bot.global_button_kind(text, text.lower()) == kind, (state, text, kind)

    assert "handle_global_button(m, u, text)" in BOT_SOURCE
    assert "Сейчас день уже закрыт. Новый навык откроется завтра" in BOT_SOURCE
    assert "Пропуск записал как данные" in BOT_SOURCE
    assert "start_safety_interceptor(m, u, text, \"global_button\", explicit=True)" in BOT_SOURCE

    for key in ("marsha", "skinny", "beck"):
        trainer = TRAINERS[key]
        assert trainer.get("display_name")
        assert trainer.get("tone")
        assert trainer.get("grammatical_gender") in {"feminine", "masculine"}
        assert trainer.get("response_templates", {}).get("check_barrier")

    assert bot.trainer_template("skinny", "check_barrier") == "Сейчас проверим, что именно ломает вход."
    assert bot.trainer_template("marsha", "check_barrier") == "Давай спокойно посмотрим, что именно сейчас слишком трудно."
    assert bot.trainer_template("beck", "check_barrier") == "Проверим, какой фактор запускает избегание: оценка, неопределённость или перегруз."
    assert "проверяла" not in BOT_SOURCE.lower()

    print("[SMOKE] global buttons OK")


if __name__ == "__main__":
    main()
