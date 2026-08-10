import unittest

from core.outcome_model import ExperimentOutcome
from core.post_experiment_policy import (
    DecisionInput, DecisionOutput, ExperimentSignature, NextAction, RecentHistory, SkillMastery,
    decide_next_action, validate_followup_experiment,
)


CURRENT = ExperimentSignature("open_only", 2, "work", "open the file", "start report")


def outcome(*, started="yes", persisted="yes", emotional="same", success=True, independent=False, reason=None):
    return ExperimentOutcome(1, started, persisted, emotional, 50, 40, success, independent, None, reason)


def decision(result, *, mastery=None, history=None):
    return decide_next_action(DecisionInput(
        result, mastery or SkillMastery(), history or RecentHistory(), "medium", CURRENT,
    ))


class PostExperimentPolicyTests(unittest.TestCase):
    def test_reason_code_is_mandatory(self):
        with self.assertRaisesRegex(ValueError, "reason_code"):
            DecisionOutput(NextAction.REPEAT, "")

    def test_worse_goes_to_safety(self):
        selected = decision(outcome(emotional="worse", success=False, reason="safety_deterioration"))
        self.assertEqual(selected.action, NextAction.SAFETY)
        self.assertEqual(selected.reason_code, "SAFETY_DETERIORATION")

    def test_no_start_too_hard_simplifies_same_skill(self):
        selected = decision(outcome(started="no", persisted="not_applicable", success=False, reason="too_hard"))
        self.assertEqual(selected.action, NextAction.SIMPLIFY)
        self.assertEqual(selected.next_skill_id, CURRENT.skill_id)
        self.assertEqual(selected.next_difficulty, 1)

    def test_wrong_mechanism_replaces_and_reranks(self):
        selected = decision(outcome(started="no", persisted="not_applicable", success=False, reason="wrong_mechanism"))
        self.assertEqual(selected.action, NextAction.REPLACE)
        self.assertEqual(selected.required_change, "rerank")

    def test_non_independent_success_repeats_with_changed_variant(self):
        selected = decision(outcome(independent=False), history=RecentHistory(repetitions=0))
        self.assertEqual(selected.action, NextAction.REPEAT)
        with self.assertRaisesRegex(ValueError, "IDENTICAL_EXPERIMENT"):
            validate_followup_experiment(selected, CURRENT, CURRENT)
        changed = ExperimentSignature("open_only", 2, "work", "open and name the file", "start report")
        validate_followup_experiment(selected, CURRENT, changed)

    def test_stable_success_advances(self):
        selected = decision(
            outcome(independent=False),
            mastery=SkillMastery(minimum_successes=2),
            history=RecentHistory(repetitions=2, same_context_successes=2),
        )
        self.assertEqual(selected.action, NextAction.ADVANCE)
        self.assertEqual(selected.next_difficulty, 3)

    def test_independent_success_transfers_to_deterministic_context(self):
        selected = decision(
            outcome(independent=True),
            history=RecentHistory(eligible_transfer_contexts=("study", "home")),
        )
        self.assertEqual(selected.action, NextAction.TRANSFER)
        self.assertEqual(selected.next_context, "home")

    def test_mastered_resolved_stops_instead_of_creating_activity(self):
        selected = decision(
            outcome(independent=True),
            mastery=SkillMastery(status="MASTERED", current_problem_resolved=True),
        )
        self.assertEqual(selected.action, NextAction.STOP)
        self.assertIsNone(selected.next_skill_id)


if __name__ == "__main__":
    unittest.main()
