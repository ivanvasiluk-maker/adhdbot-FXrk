"""Behavioral experiment is the atomic unit of product delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

ExperimentStatus = Literal["proposed", "accepted", "started", "completed", "abandoned", "safety_stopped"]
ProgressionType = Literal["first", "repeat", "simplify", "advance", "transfer", "maintenance"]


@dataclass(frozen=True)
class BehavioralExperiment:
    id: int | None
    user_id: int
    situation_id: int
    skill_id: str
    mechanism_code: str
    context_domain: str
    difficulty_level: int
    instruction_variant: str
    target_action: str
    success_criterion: str
    started_at: str | None
    completed_at: str | None
    status: ExperimentStatus
    parent_experiment_id: int | None
    progression_type: ProgressionType
    decision_reason_code: str
    trainer_style: str
    state_revision: int

    def __post_init__(self) -> None:
        if not self.skill_id.strip() or not self.target_action.strip():
            raise ValueError("An experiment has exactly one skill and one target action")
        if not self.success_criterion.strip():
            raise ValueError("Every experiment requires an objective success criterion")
        if not 1 <= self.difficulty_level <= 5:
            raise ValueError("difficulty_level must be between 1 and 5")
        if self.progression_type != "first" and self.parent_experiment_id is None:
            raise ValueError("Repeat, transfer, and progression require a parent experiment")
        if self.progression_type == "first" and self.parent_experiment_id is not None:
            raise ValueError("A first experiment cannot have a parent")


class ExperimentCard(TypedDict):
    goal: str
    action: str
    duration: str
    completion_criterion: str
    actions: tuple[str, str]


def build_experiment_card(experiment: BehavioralExperiment, *, duration: str) -> ExperimentCard:
    if not duration.strip():
        raise ValueError("Experiment card requires a duration")
    return {
        "goal": experiment.target_action,
        "action": experiment.instruction_variant,
        "duration": duration,
        "completion_criterion": experiment.success_criterion,
        "actions": ("need_simpler", "report_result"),
    }
