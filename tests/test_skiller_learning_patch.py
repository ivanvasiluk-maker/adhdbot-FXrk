import unittest

from core.learning_engine import (
    ExperimentEvidence,
    choose_next_skill,
    classify_experiment_result,
    recommended_target_function,
    skill_effectiveness,
)
from core.mechanism_model import prioritize_mechanisms


class ResultClassificationTests(unittest.TestCase):
    def test_strong_success_when_helped_and_continued(self):
        self.assertEqual(classify_experiment_result(
            completed=True, subjective_effect="helped", after_action="continued_target_task"), "STRONG_SUCCESS")

    def test_strong_success_when_helped_a_little_and_continued(self):
        self.assertEqual(classify_experiment_result(
            completed=True, subjective_effect="a_little", after_action="continued_target_task"), "STRONG_SUCCESS")

    def test_weak_success_when_stopped_after_effective_step(self):
        self.assertEqual(classify_experiment_result(
            completed=True, subjective_effect="a_little", after_action="stopped_after_step"), "WEAK_SUCCESS")

    def test_executed_only_when_switched(self):
        self.assertEqual(classify_experiment_result(
            completed=True, subjective_effect="a_little", after_action="did_something_else"), "EXECUTED_ONLY")

    def test_executed_only_when_did_not_help(self):
        self.assertEqual(classify_experiment_result(
            completed=True, subjective_effect="did_not_help", after_action="did_something_else"), "EXECUTED_ONLY")

    def test_unknown_after_action_is_unknown(self):
        self.assertEqual(classify_experiment_result(
            completed=True, after_action="unknown"), "UNKNOWN")

    def test_not_completed_is_failed(self):
        self.assertEqual(classify_experiment_result(completed=False), "FAILED")


class RecommendationPolicyTests(unittest.TestCase):
    skills = {"body_first": "EMOTION_REGULATION", "bad_draft": "START", "one_tab_focus": "STAY"}

    def test_does_not_repeat_body_first_immediately(self):
        history = [ExperimentEvidence("body_first", True, "helped", "continued_target_task", "EMOTION_REGULATION")]
        self.assertNotEqual(choose_next_skill(self.skills, history), "body_first")

    def test_did_not_help_blocks_skill_for_five_other_experiments(self):
        history = [ExperimentEvidence("body_first", True, "did_not_help", "did_something_else", "EMOTION_REGULATION")]
        for index in range(4):
            history.append(ExperimentEvidence(f"other_{index}", True, "helped", "continued_target_task", "START"))
        skills = {"body_first": "START", "fresh": "START"}
        self.assertEqual(choose_next_skill(skills, history), "fresh")
        history.append(ExperimentEvidence("other_5", True, "helped", "continued_target_task", "START"))
        self.assertEqual(choose_next_skill({"body_first": "START"}, history), "body_first")

    def test_user_selected_mechanism_has_priority(self):
        priority = prioritize_mechanisms(
            user_selected_mechanism="overload", model_inferred_mechanism="fear_of_evaluation")
        self.assertEqual(priority.primary, "overload")
        self.assertEqual(priority.secondary, "fear_of_evaluation")

    def test_repeated_loss_after_start_prioritizes_stay(self):
        history = [
            ExperimentEvidence("bad_draft", True, "helped", "continued_target_task", "START"),
            ExperimentEvidence("open_only", True, "a_little", "stopped_after_step", "START"),
            ExperimentEvidence("visible_step", True, "a_little", "did_something_else", "START"),
        ]
        self.assertEqual(recommended_target_function(history), "STAY")
        self.assertEqual(choose_next_skill(self.skills, history), "one_tab_focus")

    def test_effectiveness_does_not_count_execution_as_success(self):
        history = [
            ExperimentEvidence("bad_draft", True, "helped", "continued_target_task"),
            ExperimentEvidence("bad_draft", True, "did_not_help", "did_something_else"),
        ]
        stats = skill_effectiveness(history, "bad_draft")
        self.assertEqual((stats.attempts, stats.strong_successes, stats.executed_only), (2, 1, 1))


if __name__ == "__main__":
    unittest.main()
