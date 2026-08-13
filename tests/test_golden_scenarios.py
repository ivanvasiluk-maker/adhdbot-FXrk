"""Offline golden fixtures for the evidence-first personalization path."""

import unittest

from core.learning_engine import LearningCriteria, LearningSignal, apply_learning_signal, initial_mastery
from core.outcome_model import ExperimentOutcome
from core.post_experiment_policy import (
    DecisionInput, ExperimentSignature, NextAction, RecentHistory, SkillMastery,
    decide_next_action, validate_followup_experiment,
)
from core.ranking_engine import PersonalSkillState, RankingInput, choose_skill
from core.skill_schema import Skill
from core.value_proof_offer import ValueProof, evaluate_value_proof


def card(skill_id, mechanism, *, difficulty=(1, 2, 3)):
    return Skill(
        skill_id, 2, "Проверить прогноз" if skill_id == "cbt_check_prediction" else "Безопасный шаг",
        skill_id, "CBT", (mechanism,), ("start",), ("work", "health", "relationships"),
        ("acute_crisis",), (), (), (), ("safe_fallback",), tuple(difficulty),
        "Один короткий шаг", "Проверить прогноз одним действием", "Совершено одно проверяемое действие",
        ("Что изменилось?",), "2 успеха и самостоятельный перенос", 2, "on_similar_mechanism",
        ("health", "relationships"), "CBT_BEHAVIORAL_EXPERIMENTS_01", "production",
        {"marsha": "Бережно проверим", "skinny": "Один тест", "beck": "Проверим прогноз"},
    )


CURRENT = ExperimentSignature(
    "cbt_check_prediction", 2, "work", "Проверить прогноз одним действием", "позвонить эксперту",
)


def output(outcome, *, mastery=None, history=None):
    return decide_next_action(DecisionInput(
        outcome, mastery or SkillMastery(), history or RecentHistory(), "high", CURRENT,
    ))


class GoldenScenariosTests(unittest.TestCase):
    def test_expert_call_success_is_saved_and_reused_with_less_scaffolding(self):
        skills = [card("safe_fallback", "overwhelm"), card("cbt_check_prediction", "evaluation_avoidance")]
        data = RankingInput(
            {"evaluation_avoidance": 1.0, "overwhelm": 0.0}, "start", "work", 2, "beck",
        )
        first, _ = choose_skill(skills, data)
        self.assertEqual(first.selected_skill_id, "cbt_check_prediction")
        self.assertIn("MECHANISM_MATCH", first.reason_codes)

        state = apply_learning_signal(
            initial_mastery(1, first.selected_skill_id, difficulty=2),
            LearningSignal(1, "work", successful=True), LearningCriteria(2),
        ).state
        self.assertEqual(state.scaffolding_level, "reduced")
        repeated, _ = choose_skill(skills, RankingInput(
            data.mechanism_probabilities, "start", "work", 2, "beck",
            personal_states={first.selected_skill_id: PersonalSkillState(
                first.selected_skill_id, mastery_status="LEARNING", effectiveness_band="working",
                last_result_successful=True,
            )},
        ))
        self.assertEqual(repeated.selected_skill_id, first.selected_skill_id)
        self.assertIn("REUSE_WORKING_SKILL", repeated.reason_codes)

    def test_textless_success_counts_independent_then_transfers_and_masters(self):
        criteria = LearningCriteria(2)
        state = initial_mastery(1, "cbt_check_prediction")
        state = apply_learning_signal(state, LearningSignal(1, "work", successful=True), criteria).state
        state = apply_learning_signal(state, LearningSignal(2, "work", successful=True), criteria).state
        state = apply_learning_signal(
            state, LearningSignal(3, "work", successful=True, independent=True), criteria,
        ).state
        self.assertEqual(state.independent_use_count, 1)
        self.assertEqual(state.status, "GENERALIZING")
        mastered = apply_learning_signal(
            state, LearningSignal(
                4, "health", successful=True, independent=True, used_without_prompt=True, is_new_context=True,
            ), criteria,
        ).state
        self.assertEqual(mastered.status, "MASTERED")
        self.assertEqual(mastered.generalized_contexts, ("health",))

    def test_failure_routes_have_reason_codes_and_never_clone(self):
        too_hard = ExperimentOutcome(1, "no", "not_applicable", "same", 50, 50, False, False, None, "too_hard")
        simplify = output(too_hard)
        self.assertEqual((simplify.action, simplify.reason_code), (NextAction.SIMPLIFY, "NO_START_TOO_HARD"))
        with self.assertRaisesRegex(ValueError, "IDENTICAL"):
            validate_followup_experiment(simplify, CURRENT, CURRENT)

        wrong = ExperimentOutcome(1, "no", "not_applicable", "same", 50, 50, False, False, None, "wrong_mechanism")
        replace = output(wrong)
        self.assertEqual((replace.action, replace.reason_code), (NextAction.REPLACE, "MECHANISM_DISCONFIRMED"))

        worse = ExperimentOutcome(1, "partial", "no", "worse", 50, 80, False, False, None, "safety_deterioration")
        safety = output(worse)
        self.assertEqual((safety.action, safety.reason_code), (NextAction.SAFETY, "SAFETY_DETERIORATION"))
        with self.assertRaisesRegex(ValueError, "cannot create"):
            validate_followup_experiment(safety, CURRENT, CURRENT)

    def test_offer_requires_value_report_and_every_eligibility_fact(self):
        before = ValueProof(2, 1, True, False, False, 3)
        self.assertFalse(evaluate_value_proof(before).eligible)
        after = ValueProof(2, 1, True, True, False, 3)
        decision = evaluate_value_proof(after)
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.reason_codes, ("VALUE_PROOF_CONFIRMED",))

    def test_ten_day_learning_scenario_does_not_inflate_messages_after_mastery(self):
        state = initial_mastery(1, "cbt_check_prediction")
        criteria = LearningCriteria(2)
        statuses = []
        for day in range(1, 11):
            new_context = day == 4
            state = apply_learning_signal(state, LearningSignal(
                day, "health" if new_context else "work", successful=True,
                independent=day >= 3, used_without_prompt=day >= 3, is_new_context=new_context,
            ), criteria).state
            statuses.append(state.status)
        self.assertEqual(statuses[3], "MASTERED")
        self.assertTrue(all(status == "MASTERED" for status in statuses[3:]))
        self.assertEqual(state.scaffolding_level, "none")


if __name__ == "__main__":
    unittest.main()
