"""Production boundary joining outcome, learning and next-action policy."""

from __future__ import annotations

import json
from dataclasses import dataclass

import aiosqlite

from core.learning_engine import LearningCriteria, LearningSignal, LearningUpdate
from core.outcome_model import ExperimentOutcome
from core.post_experiment_policy import (
    DecisionInput, DecisionOutput, ExperimentSignature, RecentHistory, SkillMastery,
    decide_next_action,
)
from core.skill_schema import Skill


@dataclass(frozen=True)
class ProcessedOutcome:
    decision: DecisionOutput
    learning: LearningUpdate


async def process_experiment_outcome(
    db_path: str, *, outcome: ExperimentOutcome, skill: Skill,
    expected_revision: int, expected_flow_revision: int | None = None,
    mechanism_confidence: str = "medium",
) -> ProcessedOutcome:
    """Persist one outcome and derive exactly one evidence-linked deterministic decision."""
    from db import (
        apply_skill_mastery_signal, capture_experiment_outcome,
        record_behavioral_outcome_and_decision,
    )

    if skill.id == "" or skill.id is None:
        raise ValueError("A reviewed skill is required")
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        experiment = await (await db.execute(
            """SELECT user_id,skill_id,context_domain,difficulty_level,instruction_variant,
                      target_action,success_criterion
               FROM behavioral_experiments WHERE id=?""", (outcome.experiment_id,),
        )).fetchone()
    if not experiment or str(experiment["skill_id"]) != skill.id:
        raise ValueError("Outcome skill must match its experiment and reviewed card")

    await capture_experiment_outcome(
        db_path, outcome, expected_revision=expected_revision,
        expected_flow_revision=expected_flow_revision,
    )

    snapshot = await _policy_snapshot(
        db_path, user_id=int(experiment["user_id"]), skill=skill,
        context_domain=str(experiment["context_domain"]),
    )
    signal = LearningSignal(
        outcome.experiment_id, str(experiment["context_domain"]),
        successful=bool(outcome.success_criterion_met),
        independent=outcome.independent_use, used_without_prompt=outcome.independent_use,
        is_new_context=str(experiment["context_domain"]) not in snapshot["known_contexts"],
        failure_reason_code=outcome.failure_reason_code,
        regression=outcome.emotional_change == "worse" and snapshot["status"] == "MASTERED",
        occurred_at=outcome.captured_at or "",
    )
    learning = await apply_skill_mastery_signal(
        db_path, user_id=int(experiment["user_id"]), skill_id=skill.id, signal=signal,
        criteria=LearningCriteria(skill.minimum_successes),
        initial_difficulty=int(experiment["difficulty_level"]),
    )
    decision = decide_next_action(DecisionInput(
        outcome,
        SkillMastery(
            status=learning.state.status,
            successes_in_context=snapshot["same_context_successes"] + int(signal.successful),
            independent_successes=learning.state.independent_use_count,
            minimum_successes=skill.minimum_successes,
            current_problem_resolved=bool(outcome.success_criterion_met and learning.state.status == "MASTERED"),
            maintenance_due=False,
        ),
        RecentHistory(
            repetitions=snapshot["repetitions"] + 1,
            same_context_successes=snapshot["same_context_successes"] + int(signal.successful),
            eligible_transfer_contexts=tuple(
                value for value in skill.generalization_contexts
                if value not in learning.state.generalized_contexts
            ),
        ),
        mechanism_confidence if mechanism_confidence in {"low", "medium", "high"} else "medium",
        ExperimentSignature(
            skill.id, int(experiment["difficulty_level"]), str(experiment["context_domain"]),
            str(experiment["instruction_variant"]), str(experiment["target_action"]),
        ),
    ))
    await record_behavioral_outcome_and_decision(
        db_path, outcome.experiment_id,
        criterion_met=outcome.success_criterion_met,
        observed_result=_observed_result(outcome), decision=decision.action.value,
        reason_code=decision.reason_code,
        policy_version=decision.policy_version, ranking_version="ranking-v1",
        skill_version=f"{skill.version}.0.0",
    )
    return ProcessedOutcome(decision, learning)


async def _policy_snapshot(db_path: str, *, user_id: int, skill: Skill, context_domain: str) -> dict:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        mastery = await (await db.execute(
            "SELECT status,generalized_contexts_json FROM skill_mastery WHERE user_id=? AND skill_id=?",
            (user_id, skill.id),
        )).fetchone()
        counts = await (await db.execute(
            """SELECT COUNT(*) AS repetitions,
                      COALESCE(SUM(CASE WHEN b.context_domain=? AND
                        o.success_criterion_met=1 THEN 1 ELSE 0 END),0)
                      AS same_context_successes
               FROM behavioral_experiments b LEFT JOIN experiment_outcomes o ON o.experiment_id=b.id
               WHERE b.user_id=? AND b.skill_id=?""",
            (context_domain, user_id, skill.id),
        )).fetchone()
    return {
        "status": str(mastery["status"]) if mastery else "NEW",
        "known_contexts": set(json.loads(mastery["generalized_contexts_json"])) if mastery else set(),
        "repetitions": max(0, int(counts["repetitions"] or 0) - 1),
        "same_context_successes": max(0, int(counts["same_context_successes"] or 0) - 1),
    }


def _observed_result(outcome: ExperimentOutcome) -> str:
    return (
        f"action_started={outcome.action_started};action_persisted={outcome.action_persisted};"
        f"emotional_change={outcome.emotional_change};criterion_met={int(outcome.success_criterion_met)}"
    )
