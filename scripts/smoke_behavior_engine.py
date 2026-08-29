import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.engine import (
    build_behavior_experiment,
    build_evening_report_from_experiments,
    build_skill_card,
    normalize_micro_skill,
)
from skills import SKILLS_DB


def main() -> None:
    skill_id = "open_only" if "open_only" in SKILLS_DB else next(iter(SKILLS_DB))
    skill = SKILLS_DB[skill_id]
    normalized = normalize_micro_skill(skill_id, skill)
    assert normalized["id"] == skill_id
    assert normalized["module"]
    assert normalized["behavioral_mechanism"]
    assert normalized["minimum_success_criterion"]
    assert "completion" in normalized["feedback_to_collect"]

    user_state = {
        "user_id": 12345,
        "today_target": "prepare presentation",
        "current_task_context": "home",
        "energy": 2,
        "stress": 4,
        "available_time_minutes": 10,
    }
    experiment = build_behavior_experiment(user_state, skill_id, skill)
    assert experiment["user_id"] == "12345"
    assert experiment["module"] == normalized["module"]
    assert experiment["skill_id"] == skill_id
    assert experiment["minimum_success_criterion"]
    assert experiment["created_at"]

    card = build_skill_card(user_state, {**skill, "skill_id": skill_id})
    assert card["experiment"]["skill_id"] == skill_id
    assert card["events"][0]["meta"]["experiment"]["skill_id"] == skill_id

    report = build_evening_report_from_experiments([
        {**experiment, "helpfulness": "helped", "completed": True, "mechanism": experiment["mechanism"]}
    ])
    assert "эксперимент" in report.lower()
    assert "молодец" not in report.lower()
    print("behavior engine smoke passed")


if __name__ == "__main__":
    main()
