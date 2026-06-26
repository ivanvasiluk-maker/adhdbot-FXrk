#!/usr/bin/env python3
"""Smoke checks for user-map event provenance and cautious rendering."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from db import render_short_user_map, user_model_event, profile_contradiction_prompt  # noqa: E402


def main() -> None:
    uid = 515151

    offered_profile = {
        "user_model_events": [
            user_model_event(uid, "reported", "Залипаю в Telegram", confidence=1.0),
            user_model_event(uid, "intervention_offered", "", source_skill_id="draft_mode", confidence=0.7),
        ]
    }
    offered_map = render_short_user_map(offered_profile)
    assert "Что пока нельзя утверждать" in offered_map, offered_map
    assert "что «Плохой черновик на 2 минуты» помогает" in offered_map, offered_map
    assert "Пока похоже, что этот шаг помогает" not in offered_map, offered_map
    assert "draft_mode" not in offered_map and "openwithouttimer" not in offered_map and "small_step" not in offered_map, offered_map

    attempted_profile = {
        "user_model_events": [
            user_model_event(uid, "intervention_offered", "", source_skill_id="open_without_timer"),
            user_model_event(uid, "intervention_attempted", "", source_skill_id="open_without_timer"),
        ]
    }
    attempted_map = render_short_user_map(attempted_profile)
    assert "попробовал(а) «Открыть без таймера»; эффекта пока не знаем" in attempted_map, attempted_map
    assert "Пока похоже, что этот шаг помогает: «Открыть без таймера»" not in attempted_map, attempted_map

    helpful_profile = {
        "user_model_events": [
            user_model_event(uid, "intervention_attempted", "", source_skill_id="bad_draft"),
            user_model_event(uid, "intervention_confirmed_helpful", "", source_skill_id="bad_draft", confidence=0.8),
        ]
    }
    helpful_map = render_short_user_map(helpful_profile)
    assert "Пока похоже, что этот шаг помогает: «Плохой черновик на 2 минуты»" in helpful_map, helpful_map

    contradiction_profile = {
        "user_model_events": [
            user_model_event(uid, "reported", "Страшно, что люди увидят недоделанную работу", confidence=1.0),
            user_model_event(uid, "reported", "Не вижу смысла", confidence=1.0),
        ],
        "main_hypothesis": "страх оценки может быть важнее, чем отсутствие смысла",
    }
    contradiction_map = render_short_user_map(contradiction_profile)
    assert "Что чаще возникает ПЕРЕД тем, как ты уходишь в Telegram?" in contradiction_map, contradiction_map
    assert "страх оценки — главный узел" not in contradiction_map.lower(), contradiction_map
    assert profile_contradiction_prompt(contradiction_profile), contradiction_map

    print("[SMOKE] user map events OK")


if __name__ == "__main__":
    main()
