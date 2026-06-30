#!/usr/bin/env python3
"""Smoke checks for clean short user-map rendering."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from db import render_short_user_map, user_model_event, profile_contradiction_prompt  # noqa: E402


BLOCKS = [
    "Что ты описал",
    "Что мы пока предполагаем",
    "Что уже проверили",
    "Что проверим следующим",
]


def assert_clean_short_map(text: str) -> None:
    for block in BLOCKS:
        assert block in text, text
    assert "Что пока нельзя утверждать" not in text, text
    assert "Следующий короткий тест" not in text, text
    forbidden = [
        "shametoaction",
        "entrysmallstep",
        "onevisiblestep",
        "draft_mode",
        "openwithouttimer",
        "open_without_timer",
        "small_step",
        "ume:",
    ]
    lowered = text.lower()
    for item in forbidden:
        assert item not in lowered, text


def main() -> None:
    uid = 515151

    offered_profile = {
        "user_model_events": [
            user_model_event(uid, "reported", "Залипаю в Telegram", confidence=1.0),
            user_model_event(uid, "intervention_offered", "", source_skill_id="draft_mode", confidence=0.7),
        ]
    }
    offered_map = render_short_user_map(offered_profile)
    assert_clean_short_map(offered_map)
    assert "Залипаю в Telegram" in offered_map, offered_map
    assert "Плохой черновик на 2 минуты: данных пока мало" in offered_map, offered_map
    assert "Пока похоже, что этот шаг помогает" not in offered_map, offered_map

    attempted_profile = {
        "user_model_events": [
            user_model_event(uid, "intervention_offered", "", source_skill_id="open_without_timer"),
            user_model_event(uid, "intervention_attempted", "", source_skill_id="open_without_timer"),
        ]
    }
    attempted_map = render_short_user_map(attempted_profile)
    assert_clean_short_map(attempted_map)
    assert "Открыть без таймера: данных пока мало" in attempted_map, attempted_map
    assert "Пока похоже, что этот шаг помогает: «Открыть без таймера»" not in attempted_map, attempted_map

    helpful_profile = {
        "user_model_events": [
            user_model_event(uid, "intervention_attempted", "", source_skill_id="bad_draft"),
            user_model_event(uid, "intervention_confirmed_helpful", "", source_skill_id="bad_draft", confidence=0.8),
        ]
    }
    helpful_map = render_short_user_map(helpful_profile)
    assert_clean_short_map(helpful_map)
    assert "Плохой черновик на 2 минуты: помог" in helpful_map, helpful_map

    technical_profile = {
        "user_model_events": [
            user_model_event(uid, "reported", "shametoaction", confidence=1.0),
            user_model_event(uid, "hypothesis", "entrysmallstep", confidence=0.5),
            user_model_event(uid, "intervention_offered", "", source_skill_id="onevisiblestep"),
        ]
    }
    technical_map = render_short_user_map(technical_profile)
    assert_clean_short_map(technical_map)

    contradiction_profile = {
        "user_model_events": [
            user_model_event(uid, "reported", "Страшно, что люди увидят недоделанную работу", confidence=1.0),
            user_model_event(uid, "reported", "Не вижу смысла", confidence=1.0),
        ],
        "main_hypothesis": "страх оценки может быть важнее, чем отсутствие смысла",
    }
    contradiction_map = render_short_user_map(contradiction_profile)
    assert_clean_short_map(contradiction_map)
    assert "Что чаще возникает ПЕРЕД тем, как ты уходишь в Telegram?" in contradiction_map, contradiction_map
    assert "страх оценки — главный узел" not in contradiction_map.lower(), contradiction_map
    assert profile_contradiction_prompt(contradiction_profile), contradiction_map

    print("[SMOKE] user map events OK")


if __name__ == "__main__":
    main()
