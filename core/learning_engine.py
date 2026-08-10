"""PATCH-14: objective skill mastery and progressive removal of AI scaffolding."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from core.skill_schema import Skill

MasteryStatus = Literal["NEW", "LEARNING", "PRACTICING", "GENERALIZING", "MASTERED"]
ScaffoldingLevel = Literal["full", "reduced", "minimal", "none"]
MasteryEventType = Literal[
    "first_use", "success", "independent_use", "difficulty_up", "transfer", "mastered", "regression",
]


@dataclass(frozen=True)
class SkillMasteryState:
    user_id: int
    skill_id: str
    status: MasteryStatus = "NEW"
    current_difficulty: int = 1
    successful_practice_count: int = 0
    independent_use_count: int = 0
    generalized_contexts: tuple[str, ...] = ()
    failed_contexts: tuple[str, ...] = ()
    scaffolding_level: ScaffoldingLevel = "full"
    last_used_at: str = ""
    regression_flag: bool = False
    version: int = 1


@dataclass(frozen=True)
class LearningCriteria:
    minimum_successes: int
    independent_uses_for_generalizing: int = 1
    transfer_contexts_for_mastery: int = 1

    def __post_init__(self) -> None:
        if min(self.minimum_successes, self.independent_uses_for_generalizing, self.transfer_contexts_for_mastery) < 1:
            raise ValueError("Learning criteria must be positive")


@dataclass(frozen=True)
class LearningSignal:
    experiment_id: int
    context_domain: str
    attempted: bool = True
    successful: bool = False
    independent: bool = False
    used_without_prompt: bool = False
    is_new_context: bool = False
    failure_reason_code: str | None = None
    regression: bool = False
    occurred_at: str = ""


@dataclass(frozen=True)
class MasteryEvent:
    event_type: MasteryEventType
    experiment_id: int
    from_status: MasteryStatus
    to_status: MasteryStatus
    context_domain: str


@dataclass(frozen=True)
class LearningUpdate:
    state: SkillMasteryState
    events: tuple[MasteryEvent, ...]


def initial_mastery(user_id: int, skill_id: str, *, difficulty: int = 1) -> SkillMasteryState:
    if difficulty not in range(1, 6):
        raise ValueError("difficulty must be within 1..5")
    return SkillMasteryState(user_id, skill_id, current_difficulty=difficulty)


def criteria_from_skill(skill: Skill) -> LearningCriteria:
    """Use the reviewed card's objective threshold; never invent it with an LLM."""
    return LearningCriteria(minimum_successes=skill.minimum_successes)


def _event(kind: MasteryEventType, signal: LearningSignal, old: MasteryStatus, new: MasteryStatus) -> MasteryEvent:
    return MasteryEvent(kind, signal.experiment_id, old, new, signal.context_domain)


def apply_learning_signal(
    state: SkillMasteryState, signal: LearningSignal, criteria: LearningCriteria,
) -> LearningUpdate:
    """Advance only from observable attempts; failure never erases prior mastery counts."""
    if signal.experiment_id <= 0 or not signal.context_domain.strip():
        raise ValueError("A learning signal requires experiment evidence and context")
    old_status = state.status
    status: MasteryStatus = state.status
    successes = state.successful_practice_count
    independent_uses = state.independent_use_count
    generalized = list(state.generalized_contexts)
    failed = list(state.failed_contexts)
    scaffolding: ScaffoldingLevel = state.scaffolding_level
    regression_flag = state.regression_flag
    difficulty = state.current_difficulty
    events: list[MasteryEvent] = []

    if signal.regression and state.status == "MASTERED":
        status = "PRACTICING"
        scaffolding = "reduced"
        regression_flag = True
        events.append(_event("regression", signal, old_status, status))
    else:
        if signal.attempted and state.status == "NEW":
            status = "LEARNING"
            events.append(_event("first_use", signal, old_status, status))
        if signal.successful:
            successes += 1
            events.append(_event("success", signal, old_status, status))
            if successes == 1 and status != "MASTERED":
                scaffolding = "reduced"
            if successes >= criteria.minimum_successes and status != "MASTERED":
                if status == "LEARNING":
                    status = "PRACTICING"
                scaffolding = "minimal"
                if difficulty < 5:
                    difficulty += 1
                    events.append(_event("difficulty_up", signal, old_status, status))
        if signal.successful and signal.independent:
            independent_uses += 1
            events.append(_event("independent_use", signal, old_status, status))
            if independent_uses >= criteria.independent_uses_for_generalizing and status in {"LEARNING", "PRACTICING"}:
                status = "GENERALIZING"
                scaffolding = "none"
        if signal.successful and signal.independent and signal.is_new_context:
            if signal.context_domain not in generalized:
                generalized.append(signal.context_domain)
                events.append(_event("transfer", signal, old_status, "GENERALIZING"))
            if (
                len(generalized) >= criteria.transfer_contexts_for_mastery
                and signal.used_without_prompt
            ):
                status = "MASTERED"
                scaffolding = "none"
                regression_flag = False
                events.append(_event("mastered", signal, old_status, status))
        if not signal.successful and signal.failure_reason_code:
            if signal.context_domain not in failed:
                failed.append(signal.context_domain)

    updated = replace(
        state, status=status, current_difficulty=difficulty,
        successful_practice_count=successes, independent_use_count=independent_uses,
        generalized_contexts=tuple(generalized), failed_contexts=tuple(failed),
        scaffolding_level=scaffolding, last_used_at=signal.occurred_at or state.last_used_at,
        regression_flag=regression_flag, version=state.version + 1,
    )
    # Ensure every event reflects the final transition reached by this objective signal.
    normalized = tuple(replace(event, to_status=status) for event in events)
    return LearningUpdate(updated, normalized)


def scaffolding_instruction(state: SkillMasteryState, *, full: str, short: str, prompt: str) -> str:
    return {
        "full": full,
        "reduced": short,
        "minimal": prompt,
        "none": "Примени навык самостоятельно; я только зафиксирую результат.",
    }[state.scaffolding_level]


def regression_message() -> str:
    return (
        "Похоже, сейчас снова нужна небольшая опора. Это не потеря навыка и не наказание — "
        "на время вернём короткую подсказку и проверим навык в следующей похожей ситуации."
    )
