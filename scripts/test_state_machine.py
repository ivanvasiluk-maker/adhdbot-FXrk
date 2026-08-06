#!/usr/bin/env python3
"""Required PATCH-01 transition, concurrency, restart, and safety tests."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.state_machine import (
    TRANSITION_TABLE, FlowState, FlowStep, InvalidTransitionError,
    SQLiteFlowStateRepository, StaleActionError, configure_repository,
    get_state, start_experiment, transition,
)


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "state.db")
        self.repo = SQLiteFlowStateRepository(self.path)
        configure_repository(self.repo)
        self.next_user = 1

    def tearDown(self):
        self.temp.cleanup()

    def state_at(self, step: FlowStep, *, experiment_id=None, resume_step=None) -> FlowState:
        user_id = self.next_user
        self.next_user += 1
        self.repo.get_flow_state(user_id)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "UPDATE flow_states SET current_step=?,active_experiment_id=?,resume_step=? WHERE user_id=?",
                (step.value, experiment_id, resume_step.value if resume_step else None, user_id),
            )
        return self.repo.get_flow_state(user_id)

    def test_complete_allowed_transition_matrix(self):
        for (step, event), expected in TRANSITION_TABLE.items():
            with self.subTest(step=step, event=event):
                state = self.state_at(step)
                self.assertEqual(transition(state.user_id, event, state.revision).current_step, expected)

    def test_stale_callback_does_not_mutate(self):
        state = get_state(50)
        changed = transition(50, "onboarding_completed", state.revision)
        with self.assertRaises(StaleActionError):
            transition(50, "situation_captured", state.revision)
        self.assertEqual(get_state(50), changed)

    def test_restart_during_active_experiment(self):
        state = self.state_at(FlowStep.EXPERIMENT_READY)
        active = start_experiment(state.user_id, 712, state.revision)
        restarted_repo = SQLiteFlowStateRepository(self.path)
        configure_repository(restarted_repo)
        self.assertEqual(restarted_repo.get_flow_state(state.user_id), active)

    def test_safety_support_requires_explicit_resume(self):
        state = self.state_at(FlowStep.EXPERIMENT_ACTIVE, experiment_id=9)
        triage = transition(state.user_id, "safety_entered", state.revision)
        self.assertEqual(triage.resume_step, FlowStep.EXPERIMENT_ACTIVE)
        with self.assertRaises(InvalidTransitionError):
            transition(state.user_id, "safety_resumed", triage.revision)
        support = transition(state.user_id, "support_requested", triage.revision)
        resumed = transition(state.user_id, "safety_resumed", support.revision)
        self.assertEqual(resumed.current_step, FlowStep.EXPERIMENT_ACTIVE)
        self.assertEqual(resumed.active_experiment_id, 9)
        self.assertIsNone(resumed.resume_step)

    def test_consultation_request_from_closed_day(self):
        state = self.state_at(FlowStep.DAY_CLOSED)
        result = transition(state.user_id, "consultation_requested", state.revision)
        self.assertEqual(result.current_step, FlowStep.DAY_CLOSED)
        self.assertEqual(result.revision, state.revision + 1)

    def test_offer_cannot_intercept_active_experiment_or_safety(self):
        for step in (FlowStep.EXPERIMENT_ACTIVE, FlowStep.SAFETY_TRIAGE, FlowStep.SAFETY_SUPPORT):
            state = self.state_at(step)
            with self.assertRaises(InvalidTransitionError):
                transition(state.user_id, "offer_opened", state.revision)


if __name__ == "__main__":
    unittest.main(verbosity=2)
