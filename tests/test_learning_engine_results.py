import unittest
from unittest.mock import AsyncMock, patch

import bot
from core.post_action_feedback import ReflectionContext, build_post_action_reflection

from core.learning_engine import (
    choose_next_skill,
    classify_experiment_result,
    prioritize_mechanisms,
    recommended_target_function,
    skill_effectiveness,
    target_function_for_skill,
)


class ExperimentResultTests(unittest.TestCase):
    def test_classification_matrix(self):
        cases = (
            (True, "helped", "continued_target_task", "STRONG_SUCCESS"),
            (True, "a_little", "continued_target_task", "STRONG_SUCCESS"),
            (True, "a_little", "stopped_after_step", "WEAK_SUCCESS"),
            (True, "a_little", "did_something_else", "EXECUTED_ONLY"),
            (True, "did_not_help", "did_something_else", "EXECUTED_ONLY"),
            (True, None, "unknown", "UNKNOWN"),
            (False, "helped", "continued_target_task", "FAILED"),
        )
        for completed, effect, after, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(classify_experiment_result(
                    completed=completed, subjective_effect=effect, after_action=after,
                ), expected)

    def test_effectiveness_does_not_count_execution_as_success(self):
        stats = skill_effectiveness([
            {"skill_id": "bad_draft", "experiment_result": "STRONG_SUCCESS"},
            {"skill_id": "bad_draft", "experiment_result": "STRONG_SUCCESS"},
            {"skill_id": "bad_draft", "experiment_result": "EXECUTED_ONLY"},
        ])["bad_draft"]
        self.assertEqual(stats, {"attempts": 3, "strong_successes": 2, "weak_successes": 0,
                                 "executed_only": 1, "failures": 0, "unknown": 0})

    def test_recent_body_first_is_not_repeated(self):
        candidates = ({"skill_id": "body_first", "target_function": "START"},
                      {"skill_id": "bad_draft", "target_function": "START"})
        selected = choose_next_skill(candidates, [{"skill_id": "body_first", "experiment_result": "STRONG_SUCCESS"}])
        self.assertEqual(selected["skill_id"], "bad_draft")

    def test_did_not_help_blocks_five_other_experiments(self):
        candidates = ({"skill_id": "body_first", "target_function": "START"},
                      {"skill_id": "fresh", "target_function": "START"})
        history = [{"skill_id": "body_first", "experiment_result": "EXECUTED_ONLY", "subjective_effect": "did_not_help"}]
        history += [{"skill_id": f"other_{i}", "experiment_result": "UNKNOWN"} for i in range(4)]
        self.assertEqual(choose_next_skill(candidates, history)["skill_id"], "fresh")

    def test_user_mechanism_has_priority(self):
        self.assertEqual(prioritize_mechanisms("overload", "fear_of_evaluation"),
                         ("overload", "fear_of_evaluation"))

    def test_start_losses_switch_target_to_stay(self):
        history = [
            {"skill_id": "bad_draft", "target_function": "START", "experiment_result": "STRONG_SUCCESS", "after_action": "continued_target_task"},
            {"skill_id": "open", "target_function": "START", "experiment_result": "WEAK_SUCCESS", "after_action": "stopped_after_step"},
            {"skill_id": "visible", "target_function": "START", "experiment_result": "EXECUTED_ONLY", "after_action": "did_something_else"},
        ]
        self.assertEqual(recommended_target_function(history), "STAY")
        candidates = ({"skill_id": "new_start", "target_function": "START"},
                      {"skill_id": "one_tab", "target_function": "STAY"})
        self.assertEqual(choose_next_skill(candidates, history)["skill_id"], "one_tab")

    def test_production_target_function_map(self):
        self.assertEqual(target_function_for_skill("bad_draft"), "START")
        self.assertEqual(target_function_for_skill("one_tab_focus"), "STAY")
        self.assertEqual(target_function_for_skill("restart_after_slip"), "RETURN")
        self.assertEqual(target_function_for_skill("body_first"), "EMOTION_REGULATION")

    def test_real_repetition_gate_honors_five_experiment_cooldown(self):
        attempts = [{"skill_id": "body_first", "experiment_result": "EXECUTED_ONLY", "effect": "not_helped"}]
        attempts += [{"skill_id": f"other_{i}", "experiment_result": "UNKNOWN"} for i in range(4)]
        self.assertTrue(bot.should_block_skill_for_repetition({"skill_attempts": attempts}, "body_first"))

    def test_offer_does_not_call_execution_helpful(self):
        summary = {"skill_map": {"skills": [{
            "skill_id": "body_first", "title": "Сначала тело, потом задача",
            "completed_count": 3, "helpful_count": 0, "strong_successes": 0,
            "executed_only": 3,
        }]}}
        self.assertEqual(bot._offer_skill_fact(summary, helpful=True), "пока явного лидера нет")
        self.assertIn("Сначала тело, потом задача", bot._offer_skill_fact(summary, helpful=False))

    def test_executed_only_summary_never_uses_worked_heading(self):
        reflection = build_post_action_reflection(ReflectionContext(
            situation="отчёт", barrier="distracted", skill_title="Телефон вне руки",
            tested_action="убрать телефон", completed=True, partial=False,
            helpfulness="some", continued=False, after_action="did_something_else",
        )).render()
        self.assertNotIn("Сработало", reflection)
        self.assertIn("Что проверяли", reflection)
        self.assertIn("переключился на другую задачу", reflection)


class MechanismPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_mechanism_remains_primary_over_inference(self):
        user = {"user_id": 1, "active_attempt": {}}
        with patch.object(bot, "save_user", new=AsyncMock()):
            await bot.remember_user_mechanism(user, "overload", user_selected=True)
            await bot.remember_user_mechanism(user, "fear_of_error", user_selected=False)
        self.assertEqual(user["primary_mechanism"], "overload")
        self.assertEqual(user["secondary_mechanism"], "fear_of_error")
        self.assertEqual(user["active_attempt"]["current_mechanism"], "overload")


if __name__ == "__main__":
    unittest.main()
