import unittest

import bot
from core.learning_engine import (
    ExperimentEvidence,
    choose_next_skill,
    classify_experiment_result,
    correction_intent,
    derive_functional_state,
    experiment_allowance,
    milestone_summary,
    primary_hypothesis,
    recommended_target_function,
    skill_effectiveness,
    skill_cooldown_remaining,
    skill_blocked_in_model,
    update_hypothesis_scores,
    update_learning_model,
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


class ConsistentLearningModelTests(unittest.TestCase):
    def test_scenario_a_start_green_stay_red_routes_stay(self):
        history = [
            ExperimentEvidence(f"start_{i}", True, "helped", "stopped_after_step", "START")
            for i in range(3)
        ]
        state = derive_functional_state(history)
        self.assertEqual((state.start, state.stay, state.primary_problem), ("green", "red", "STAY"))
        self.assertEqual(choose_next_skill({"open": "START", "focus": "STAY"}, history), "focus")

    def test_scenario_b_state_relief_is_not_task_success(self):
        evidence = ExperimentEvidence("breath", True, "helped", "stopped_after_step", "STAY")
        self.assertEqual((evidence.normalized_state_effect, evidence.normalized_task_effect), ("positive", "none"))
        self.assertNotEqual(evidence.result, "STRONG_SUCCESS")

    def test_scenario_c_two_task_failures_trigger_cooldown(self):
        history = [
            ExperimentEvidence("breath", True, "helped", "did_something_else", "STAY"),
            ExperimentEvidence("breath", True, "helped", "stopped_after_step", "STAY"),
        ]
        self.assertEqual(skill_cooldown_remaining(history, "breath"), 4)
        self.assertEqual(choose_next_skill({"breath": "STAY", "focus": "STAY"}, history), "focus")

    def test_changed_skill_does_not_immediately_return(self):
        history = [ExperimentEvidence("breath", None, explicitly_changed=True)]
        self.assertEqual(choose_next_skill({"breath": "START", "draft": "START"}, history), "draft")

    def test_scenario_d_correction_intents(self):
        for text in ("всё ок", "да", "точно", "верно", "в точку", "норм", "согласен", "именно так"):
            self.assertEqual(correction_intent(text), "confirm")
        self.assertEqual(correction_intent("нет"), "reject")
        self.assertEqual(correction_intent("дело не в перегрузе, а в страхе ошибки"), "correct")

    def test_scenario_e_negative_return_cannot_be_green(self):
        history = [ExperimentEvidence("return", True, target_function="RETURN", returned_after_distraction=False)]
        state = derive_functional_state(history)
        self.assertEqual((state.return_, state.primary_problem), ("red", "RETURN"))
        self.assertEqual(recommended_target_function(history), "RETURN")

    def test_scenario_f_repeated_hypothesis_wins_and_old_one_decays(self):
        scores = update_hypothesis_scores({}, [
            "страх ошибки", "боюсь оценки", "страх ошибки", "перегруз", "непонятен следующий шаг",
        ])
        self.assertEqual(primary_hypothesis(scores), "fear_of_failure")
        self.assertGreater(scores["fear_of_failure"], scores["overload"])

    def test_scenario_g_two_plus_one_is_hard_limit(self):
        self.assertEqual(experiment_allowance(1), "main")
        self.assertEqual(experiment_allowance(2), "voluntary")
        self.assertEqual(experiment_allowance(2, 1), "closed")
        self.assertEqual(experiment_allowance(2, 1, developer_mode=True), "main")

    def test_milestones_only_use_observed_skills(self):
        history = [ExperimentEvidence("breath", True, "helped", "did_something_else")]
        self.assertIsNone(milestone_summary(2, history))
        summary = milestone_summary(3, history, {"breath": "Длинный выдох"})
        self.assertIn("Длинный выдох", summary)
        self.assertNotIn("телефон", summary.lower())

    def test_learning_model_persists_effects_status_and_cooldown(self):
        model = {}
        for _ in range(2):
            model = update_learning_model(
                model,
                ExperimentEvidence("breath", True, "helped", "stopped_after_step", "STAY"),
                observations=["страх ошибки"], day="2026-09-12",
            )
        self.assertEqual(model["experiments"][-1]["state_effect"], "positive")
        self.assertEqual(model["experiments"][-1]["task_effect"], "none")
        self.assertEqual(model["functional_state"]["STAY"], "red")
        self.assertEqual(model["primary_hypothesis"], "fear_of_failure")
        self.assertTrue(skill_blocked_in_model(model, "breath"))

    def test_learning_model_defaults_keep_legacy_profiles_compatible(self):
        model = update_learning_model(
            {"unrelated_legacy_field": "kept"},
            ExperimentEvidence("draft", True, "helped", "continued_target_task"),
        )
        self.assertEqual(model["unrelated_legacy_field"], "kept")
        self.assertEqual(model["functional_state"]["START"], "green")

    def test_persisted_return_failure_reaches_day_card_without_contradiction(self):
        model = update_learning_model(
            {}, ExperimentEvidence("return", True, target_function="RETURN", returned_after_distraction=False),
            day="2026-09-12",
        )
        card = bot.day1_profile_card_text(
            {"day": 1}, {"learning_model": model, "last_day_review": {"function": "return"}}, 1,
        )
        self.assertIn("RETURN     🔴", card)
        self.assertNotIn("RETURN     🟢", card)

    def test_profile_card_explains_state_relief_without_task_effect(self):
        feedback = {"skill_id": "breath", "completed": True, "helpfulness": "helped",
                    "continued_after_skill": False, "state_effect": "positive", "task_effect": "none"}
        card = bot.day1_profile_card_text({"day": 1}, {"last_skill_feedback": feedback}, 1)
        self.assertIn("помог снизить напряжение", card)
        self.assertIn("не помог продолжить целевое действие", card)


if __name__ == "__main__":
    unittest.main()
