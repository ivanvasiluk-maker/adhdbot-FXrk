"""PATCH-08: deterministic policy for exactly one post-experiment action."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from core.outcome_model import ExperimentOutcome

POLICY_VERSION = "post-experiment-v1"


class NextAction(str, Enum):
    REPEAT = "repeat"
    SIMPLIFY = "simplify"
    REPLACE = "replace"
    ADVANCE = "advance"
    TRANSFER = "transfer"
    MAINTAIN = "maintain"
    STOP = "stop"
    SAFETY = "safety"


@dataclass(frozen=True)
class SkillMastery:
    status: Literal["NEW", "PRACTICING", "MASTERED", "GENERALIZING"] = "NEW"
    successes_in_context: int = 0
    independent_successes: int = 0
    minimum_successes: int = 2
    current_problem_resolved: bool = False
    maintenance_due: bool = False


@dataclass(frozen=True)
class RecentHistory:
    repetitions: int = 0
    same_context_successes: int = 0
    eligible_transfer_contexts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExperimentSignature:
    skill_id: str
    difficulty: int
    context_domain: str
    instruction_variant: str
    target_action: str


@dataclass(frozen=True)
class DecisionInput:
    outcome: ExperimentOutcome
    skill_mastery: SkillMastery
    recent_history: RecentHistory
    mechanism_confidence: Literal["low", "medium", "high"]
    current_experiment: ExperimentSignature


@dataclass(frozen=True)
class DecisionOutput:
    action: NextAction
    reason_code: str
    next_skill_id: str | None = None
    next_difficulty: int | None = None
    next_context: str | None = None
    required_change: Literal[
        "none", "task_or_variant", "difficulty_or_variant", "rerank",
        "difficulty_or_scaffolding", "context",
    ] = "none"
    policy_version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        if not self.reason_code.strip():
            raise ValueError("Every post-experiment decision requires a reason_code")
        if self.next_difficulty is not None and self.next_difficulty not in range(1, 6):
            raise ValueError("next_difficulty must be within 1..5")
        if not self.policy_version.strip():
            raise ValueError("Every post-experiment decision requires a policy version")


def decide_next_action(data: DecisionInput) -> DecisionOutput:
    """Apply ordered V1 rules; no LLM output is accepted by this boundary."""
    outcome = data.outcome
    mastery = data.skill_mastery
    history = data.recent_history
    current = data.current_experiment

    if outcome.requires_safety_handoff or outcome.failure_reason_code == "safety_deterioration":
        return DecisionOutput(NextAction.SAFETY, "SAFETY_DETERIORATION")

    if outcome.action_started == "no" and outcome.failure_reason_code == "too_hard":
        return DecisionOutput(
            NextAction.SIMPLIFY, "NO_START_TOO_HARD", current.skill_id,
            max(1, current.difficulty - 1), current.context_domain, "difficulty_or_variant",
        )

    if outcome.action_started == "no" and outcome.failure_reason_code in {"wrong_mechanism", "skill_mismatch"}:
        reason = "MECHANISM_DISCONFIRMED" if outcome.failure_reason_code == "wrong_mechanism" else "SKILL_MISMATCH"
        return DecisionOutput(NextAction.REPLACE, reason, required_change="rerank")

    if mastery.status == "MASTERED" and mastery.current_problem_resolved:
        if mastery.maintenance_due:
            return DecisionOutput(
                NextAction.MAINTAIN, "MASTERED_MAINTENANCE_DUE", current.skill_id,
                current.difficulty, current.context_domain, "task_or_variant",
            )
        return DecisionOutput(NextAction.STOP, "MASTERED_PROBLEM_RESOLVED")

    successful = outcome.success_criterion_met or outcome.action_started in {"yes", "partial"}
    if successful and outcome.independent_use and history.eligible_transfer_contexts:
        return DecisionOutput(
            NextAction.TRANSFER, "INDEPENDENT_SUCCESS_TRANSFER_READY", current.skill_id,
            current.difficulty, sorted(history.eligible_transfer_contexts)[0], "context",
        )

    repetitions_below_criterion = history.repetitions < mastery.minimum_successes
    if successful and not outcome.independent_use and repetitions_below_criterion:
        return DecisionOutput(
            NextAction.REPEAT, "SUCCESS_NEEDS_INDEPENDENT_REPETITION", current.skill_id,
            current.difficulty, current.context_domain, "task_or_variant",
        )

    stable_same_context = (
        successful and history.same_context_successes >= mastery.minimum_successes
    )
    if stable_same_context:
        return DecisionOutput(
            NextAction.ADVANCE, "STABLE_SUCCESS_ADVANCE", current.skill_id,
            min(5, current.difficulty + 1), current.context_domain, "difficulty_or_scaffolding",
        )

    if successful:
        return DecisionOutput(
            NextAction.REPEAT, "SUCCESS_REQUIRES_MORE_EVIDENCE", current.skill_id,
            current.difficulty, current.context_domain, "task_or_variant",
        )

    return DecisionOutput(NextAction.REPLACE, "FAILED_EXPERIMENT_RERANK", required_change="rerank")


def validate_followup_experiment(
    decision: DecisionOutput, current: ExperimentSignature, followup: ExperimentSignature,
) -> None:
    """Reject an automatic clone and enforce the mutation required by policy."""
    if decision.action in {NextAction.SAFETY, NextAction.STOP}:
        raise ValueError("Terminal/safety decisions cannot create a follow-up experiment")
    if followup == current:
        raise ValueError("IDENTICAL_EXPERIMENT_REPLAY_FORBIDDEN")
    if decision.next_skill_id is not None and followup.skill_id != decision.next_skill_id:
        raise ValueError("FOLLOWUP_SKILL_DOES_NOT_MATCH_DECISION")
    if decision.next_difficulty is not None and followup.difficulty != decision.next_difficulty:
        raise ValueError("FOLLOWUP_DIFFICULTY_DOES_NOT_MATCH_DECISION")
    if decision.next_context is not None and followup.context_domain != decision.next_context:
        raise ValueError("FOLLOWUP_CONTEXT_DOES_NOT_MATCH_DECISION")
    if decision.required_change == "task_or_variant" and (
        followup.instruction_variant == current.instruction_variant
        and followup.target_action == current.target_action
    ):
        raise ValueError("REPEAT_REQUIRES_CHANGED_TASK_OR_VARIANT")
    if decision.required_change == "difficulty_or_variant" and (
        followup.difficulty == current.difficulty
        and followup.instruction_variant == current.instruction_variant
    ):
        raise ValueError("SIMPLIFY_REQUIRES_DIFFICULTY_OR_VARIANT_CHANGE")
    if decision.required_change == "rerank" and followup.skill_id == current.skill_id:
        raise ValueError("REPLACE_REQUIRES_RERANKED_SKILL")
    if decision.required_change == "context" and followup.context_domain == current.context_domain:
        raise ValueError("TRANSFER_REQUIRES_NEW_CONTEXT")
    if decision.required_change == "difficulty_or_scaffolding" and (
        followup.difficulty == current.difficulty
        and followup.instruction_variant == current.instruction_variant
    ):
        raise ValueError("ADVANCE_REQUIRES_DIFFICULTY_OR_SCAFFOLDING_CHANGE")
