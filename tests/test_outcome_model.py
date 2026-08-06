import sqlite3
import tempfile
import unittest

from core.experiment_core import BehavioralExperiment
from core.mechanism_model import MechanismHypothesis, SituationSnapshot
from core.outcome_model import (
    ExperimentOutcome, classify_failure_reason, compact_outcome_screen,
    failure_clarification_question, next_action_policy,
)
from db import (
    capture_experiment_outcome, create_behavioral_experiment,
    create_mechanism_hypothesis, create_situation_snapshot, init_db,
    transition_behavioral_experiment,
)


class OutcomeModelTests(unittest.TestCase):
    def test_three_axes_are_independent_and_failure_requires_reason(self):
        outcome = ExperimentOutcome(1, "partial", "no", "better", 70, 40, False, False, None, "too_hard")
        self.assertEqual((outcome.action_started, outcome.action_persisted, outcome.emotional_change), ("partial", "no", "better"))
        with self.assertRaisesRegex(ValueError, "failure reason"):
            ExperimentOutcome(1, "no", "not_applicable", "same", None, None, False, False, None, None)

    def test_compact_screen_contains_each_axis_once(self):
        screen = compact_outcome_screen()
        self.assertEqual(screen["fields"], ("action_started", "action_persisted", "emotional_change"))
        self.assertEqual(len(set(screen["fields"])), 3)

    def test_rules_confirm_or_override_llm_and_ask_at_most_once(self):
        self.assertEqual(classify_failure_reason({"external_blocked": True}, llm_suggestion="skill_mismatch"), "external_blocker")
        self.assertEqual(classify_failure_reason({"step_too_hard": True}, llm_suggestion="too_hard"), "too_hard")
        self.assertIsNotNone(failure_clarification_question("unknown"))
        self.assertIsNone(failure_clarification_question("unknown", already_asked=True))

    def test_worse_always_routes_to_safety(self):
        outcome = ExperimentOutcome(1, "yes", "partial", "worse", 50, 80, False, False, None, "safety_deterioration")
        self.assertEqual(next_action_policy(outcome), "safety_handoff")


class OutcomePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.file = tempfile.NamedTemporaryFile(suffix=".db")
        await init_db(self.file.name)
        situation_id = await create_situation_snapshot(self.file.name, SituationSnapshot(
            None, 9, None, "открыть письмо", "прочитать тему", "work", "start", 50, 50, "today",
        ))
        mechanism_id = await create_mechanism_hypothesis(self.file.name, MechanismHypothesis(
            None, situation_id, "evaluation_avoidance", "medium", ("пользователь избегает открыть письмо",), ("изменится ли напряжение",), (), "rules", True,
        ))
        experiment = BehavioralExperiment(
            None, 9, situation_id, "check_the_facts_light", "evaluation_avoidance", "work", 1,
            "Открой письмо и прочитай тему", "Прочитать тему", "Тема письма прочитана",
            None, None, "proposed", None, "first", "MECHANISM_MATCH", "beck", 0,
        )
        self.experiment_id = await create_behavioral_experiment(self.file.name, experiment, mechanism_hypothesis_id=mechanism_id)
        revision = await transition_behavioral_experiment(self.file.name, self.experiment_id, status="accepted", expected_revision=0)
        self.revision = await transition_behavioral_experiment(self.file.name, self.experiment_id, status="started", expected_revision=revision)
        with sqlite3.connect(self.file.name) as db:
            db.execute("INSERT INTO flow_states(user_id,current_step,revision) VALUES(9,'outcome_capture',4)")
            db.commit()

    async def asyncTearDown(self):
        self.file.close()

    async def test_worse_atomically_safety_stops_and_persists_all_axes(self):
        outcome = ExperimentOutcome(
            self.experiment_id, "partial", "no", "worse", 55, 85,
            False, False, "стало тревожнее", "safety_deterioration",
        )
        self.assertEqual(
            await capture_experiment_outcome(
                self.file.name, outcome, expected_revision=self.revision, expected_flow_revision=4,
            ),
            "safety_handoff",
        )
        with sqlite3.connect(self.file.name) as db:
            experiment = db.execute("SELECT status,decision_reason_code FROM behavioral_experiments WHERE id=?", (self.experiment_id,)).fetchone()
            stored = db.execute("SELECT action_started,action_persisted,emotional_change,failure_reason_code FROM experiment_outcomes WHERE experiment_id=?", (self.experiment_id,)).fetchone()
            flow = db.execute("SELECT current_step,resume_step,revision FROM flow_states WHERE user_id=9").fetchone()
        self.assertEqual(experiment, ("safety_stopped", "SAFETY_HANDOFF_REQUIRED"))
        self.assertEqual(stored, ("partial", "no", "worse", "safety_deterioration"))
        self.assertEqual(flow, ("safety_triage", "outcome_capture", 5))


if __name__ == "__main__":
    unittest.main()
