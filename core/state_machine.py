"""Durable, optimistic-concurrency flow state machine.

All product-flow mutations belong here.  ``stage`` and ``day`` are legacy UI
mirrors and deliberately do not participate in transition decisions.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Protocol


class FlowStep(str, Enum):
    ONBOARDING = "onboarding"
    SITUATION_CAPTURE = "situation_capture"
    MECHANISM_CONFIRMATION = "mechanism_confirmation"
    EXPERIMENT_READY = "experiment_ready"
    EXPERIMENT_ACTIVE = "experiment_active"
    OUTCOME_CAPTURE = "outcome_capture"
    NEXT_STEP_DECISION = "next_step_decision"
    DAY_CLOSED = "day_closed"
    SAFETY_TRIAGE = "safety_triage"
    SAFETY_SUPPORT = "safety_support"
    OFFER = "offer"


@dataclass(frozen=True)
class FlowState:
    user_id: int
    current_step: FlowStep
    active_experiment_id: int | None
    resume_step: FlowStep | None
    revision: int


class StateMachineError(RuntimeError):
    pass


class StaleActionError(StateMachineError):
    """The action was rendered for an older state revision."""


class InvalidTransitionError(StateMachineError):
    pass


class FlowStateRepository(Protocol):
    def get_flow_state(self, user_id: int) -> FlowState: ...
    def atomic_update(self, old: FlowState, new: FlowState) -> FlowState: ...


# Explicit self-transitions represent non-blocking surfaces such as maps and a
# consultation request. They increment revision without stealing the flow.
TRANSITION_TABLE: dict[tuple[FlowStep, str], FlowStep] = {
    (FlowStep.ONBOARDING, "onboarding_completed"): FlowStep.SITUATION_CAPTURE,
    (FlowStep.SITUATION_CAPTURE, "situation_captured"): FlowStep.MECHANISM_CONFIRMATION,
    (FlowStep.MECHANISM_CONFIRMATION, "mechanism_confirmed"): FlowStep.EXPERIMENT_READY,
    (FlowStep.MECHANISM_CONFIRMATION, "clarification_answered"): FlowStep.EXPERIMENT_READY,
    (FlowStep.EXPERIMENT_ACTIVE, "experiment_finished"): FlowStep.OUTCOME_CAPTURE,
    (FlowStep.OUTCOME_CAPTURE, "outcome_captured"): FlowStep.NEXT_STEP_DECISION,
    (FlowStep.NEXT_STEP_DECISION, "start_another"): FlowStep.SITUATION_CAPTURE,
    (FlowStep.NEXT_STEP_DECISION, "close_day"): FlowStep.DAY_CLOSED,
    (FlowStep.DAY_CLOSED, "start_day"): FlowStep.SITUATION_CAPTURE,
    (FlowStep.DAY_CLOSED, "consultation_requested"): FlowStep.DAY_CLOSED,
    (FlowStep.DAY_CLOSED, "map_opened"): FlowStep.DAY_CLOSED,
    (FlowStep.DAY_CLOSED, "journal_opened"): FlowStep.DAY_CLOSED,
    (FlowStep.DAY_CLOSED, "evening_review_opened"): FlowStep.DAY_CLOSED,
    (FlowStep.OFFER, "offer_closed"): FlowStep.SITUATION_CAPTURE,
    (FlowStep.SAFETY_TRIAGE, "support_requested"): FlowStep.SAFETY_SUPPORT,
}


class SQLiteFlowStateRepository:
    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path))
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS flow_states (
                user_id INTEGER PRIMARY KEY,
                current_step TEXT NOT NULL,
                active_experiment_id INTEGER,
                resume_step TEXT,
                revision INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)

    def get_flow_state(self, user_id: int) -> FlowState:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO flow_states(user_id,current_step,revision) VALUES(?,?,0)",
                (user_id, FlowStep.ONBOARDING.value),
            )
            row = db.execute(
                "SELECT current_step,active_experiment_id,resume_step,revision FROM flow_states WHERE user_id=?",
                (user_id,),
            ).fetchone()
        assert row is not None
        return FlowState(user_id, FlowStep(row[0]), row[1], FlowStep(row[2]) if row[2] else None, row[3])

    def atomic_update(self, old: FlowState, new: FlowState) -> FlowState:
        with self._lock, self._connect() as db:
            cursor = db.execute(
                """UPDATE flow_states SET current_step=?,active_experiment_id=?,resume_step=?,
                   revision=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND revision=?""",
                (new.current_step.value, new.active_experiment_id,
                 new.resume_step.value if new.resume_step else None, new.revision,
                 old.user_id, old.revision),
            )
            if cursor.rowcount != 1:
                raise StaleActionError("State changed while the action was being applied")
            # The legacy columns are write-only mirrors. No transition reads
            # them; keeping the write inside this adapter prevents divergence.
            has_users = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
            ).fetchone()
            if has_users:
                db.execute(
                    "UPDATE users SET current_step=?,stage=? WHERE user_id=?",
                    (new.current_step.value, new.current_step.value, old.user_id),
                )
        return new


_repo: FlowStateRepository | None = None


def configure_repository(repo: FlowStateRepository) -> None:
    global _repo
    _repo = repo


def get_state(user_id: int) -> FlowState:
    if _repo is None:
        raise RuntimeError("Flow state repository is not configured")
    return _repo.get_flow_state(user_id)


def transition(user_id: int, event: str, expected_revision: int) -> FlowState:
    """Apply one legal event with an optimistic revision check."""
    if _repo is None:
        raise RuntimeError("Flow state repository is not configured")
    state = _repo.get_flow_state(user_id)
    if state.revision != expected_revision:
        raise StaleActionError("This button is stale; return to the current step safely")

    if event == "safety_entered":
        if state.current_step in {FlowStep.SAFETY_TRIAGE, FlowStep.SAFETY_SUPPORT}:
            raise InvalidTransitionError("Safety is already active")
        next_step, resume_step = FlowStep.SAFETY_TRIAGE, state.current_step
    elif event == "safety_resumed":
        if state.current_step != FlowStep.SAFETY_SUPPORT or state.resume_step is None:
            raise InvalidTransitionError("Explicit safety support exit is required")
        next_step, resume_step = state.resume_step, None
    elif event == "offer_opened":
        if state.current_step in {FlowStep.SAFETY_TRIAGE, FlowStep.SAFETY_SUPPORT, FlowStep.EXPERIMENT_ACTIVE}:
            raise InvalidTransitionError("Offer cannot interrupt safety or an active experiment")
        next_step, resume_step = FlowStep.OFFER, state.current_step
    elif event == "offer_closed" and state.current_step == FlowStep.OFFER and state.resume_step:
        next_step, resume_step = state.resume_step, None
    else:
        try:
            next_step = TRANSITION_TABLE[(state.current_step, event)]
        except KeyError as exc:
            raise InvalidTransitionError(f"Event {event!r} is invalid in {state.current_step.value}") from exc
        resume_step = state.resume_step

    experiment_id = state.active_experiment_id
    if event == "experiment_started":
        raise InvalidTransitionError("Use start_experiment() so an entity id is mandatory")
    if event in {"outcome_captured", "start_another", "close_day"}:
        experiment_id = None
    return _repo.atomic_update(state, FlowState(user_id, next_step, experiment_id, resume_step, state.revision + 1))


def start_experiment(user_id: int, experiment_id: int, expected_revision: int) -> FlowState:
    state = get_state(user_id)
    if state.revision != expected_revision:
        raise StaleActionError("This button is stale; return to the current step safely")
    if state.current_step != FlowStep.EXPERIMENT_READY or experiment_id <= 0:
        raise InvalidTransitionError("An experiment can start only from experiment_ready with an id")
    # Replacing the id here prevents a new experiment inheriting old outcome state.
    new = FlowState(user_id, FlowStep.EXPERIMENT_ACTIVE, experiment_id, None, state.revision + 1)
    return _repo.atomic_update(state, new)


def callback_data(action: str, entity_id: int | str, state_revision: int) -> str:
    """Encode the mandatory action/entity/revision callback contract."""
    if not action or ":" in action or ":" in str(entity_id):
        raise ValueError("Callback parts must be non-empty and may not contain ':'")
    return f"fsm:{action}:{entity_id}:{state_revision}"


def parse_callback_data(value: str) -> tuple[str, str, int]:
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != "fsm" or not parts[1] or not parts[2]:
        raise ValueError("Invalid state-machine callback")
    return parts[1], parts[2], int(parts[3])
