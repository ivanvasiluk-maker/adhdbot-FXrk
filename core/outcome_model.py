"""Structured outcome capture and policy-owned failure classification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

Ternary = Literal["yes", "partial", "no"]
Persistence = Literal["yes", "partial", "no", "not_applicable"]
EmotionalChange = Literal["better", "same", "worse", "unknown"]
FailureReason = Literal[
    "too_hard", "wrong_mechanism", "unclear_instruction",
    "insufficient_repetition", "wrong_timing", "external_blocker",
    "safety_deterioration", "skill_mismatch", "unknown",
]

FAILURE_REASON_CODES = frozenset({
    "too_hard", "wrong_mechanism", "unclear_instruction",
    "insufficient_repetition", "wrong_timing", "external_blocker",
    "safety_deterioration", "skill_mismatch", "unknown",
})


@dataclass(frozen=True)
class ExperimentOutcome:
    experiment_id: int
    action_started: Ternary
    action_persisted: Persistence
    emotional_change: EmotionalChange
    before_intensity_0_100: int | None
    after_intensity_0_100: int | None
    success_criterion_met: bool
    independent_use: bool
    user_note_short: str | None
    failure_reason_code: FailureReason | None
    captured_at: str | None = None

    def __post_init__(self) -> None:
        if self.experiment_id <= 0:
            raise ValueError("experiment_id must be positive")
        for name in ("before_intensity_0_100", "after_intensity_0_100"):
            value = getattr(self, name)
            if value is not None and not 0 <= value <= 100:
                raise ValueError(f"{name} must be between 0 and 100")
        if not self.success_criterion_met and self.failure_reason_code is None:
            raise ValueError("Every unsuccessful experiment requires a failure reason or explicit unknown")
        if self.failure_reason_code is not None and self.failure_reason_code not in FAILURE_REASON_CODES:
            raise ValueError("Unknown failure reason")
        if self.user_note_short is not None and len(self.user_note_short) > 280:
            raise ValueError("user_note_short must not exceed 280 characters")

    @property
    def requires_safety_handoff(self) -> bool:
        return self.emotional_change == "worse"


class OutcomeScreen(TypedDict):
    title: str
    fields: tuple[str, str, str]
    submit_action: str


def compact_outcome_screen() -> OutcomeScreen:
    """One result screen for the three independent axes; no duplicate prompts."""
    return {
        "title": "Что изменилось после попытки?",
        "fields": ("action_started", "action_persisted", "emotional_change"),
        "submit_action": "capture_outcome",
    }


def classify_failure_reason(signals: dict, *, llm_suggestion: str | None = None) -> FailureReason:
    """Rules own the decision; an LLM suggestion is accepted only when evidenced."""
    if signals.get("emotional_change") == "worse" or signals.get("safety_risk"):
        return "safety_deterioration"
    rules: tuple[tuple[str, str], ...] = (
        ("instruction_was_unclear", "unclear_instruction"),
        ("external_blocked", "external_blocker"),
        ("mechanism_disconfirmed", "wrong_mechanism"),
        ("step_too_hard", "too_hard"),
        ("skill_did_not_fit", "skill_mismatch"),
        ("timing_was_wrong", "wrong_timing"),
        ("needs_more_trials", "insufficient_repetition"),
    )
    matched = {reason for flag, reason in rules if signals.get(flag) is True}
    if llm_suggestion in matched and llm_suggestion in FAILURE_REASON_CODES:
        return llm_suggestion  # policy confirmation, not an LLM decision
    return next((reason for _flag, reason in rules if reason in matched), "unknown")  # type: ignore[return-value]


def failure_clarification_question(reason: FailureReason, *, already_asked: bool = False) -> str | None:
    """Return no more than one high-information clarification."""
    if already_asked or reason != "unknown":
        return None
    return "Что было главным: шаг слишком трудный, инструкция неясная или помешало что-то внешнее?"


def next_action_policy(outcome: ExperimentOutcome) -> str:
    """Worsening never repeats or advances productivity automatically."""
    if outcome.requires_safety_handoff:
        return "safety_handoff"
    if outcome.success_criterion_met:
        return "decide_progression"
    return "review_failure_reason"
