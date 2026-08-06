import unittest

from core.mechanism_model import (
    MechanismHypothesis, SituationSnapshot, can_start_without_clarification,
    clarification_question, hypothesis_from_structured_features, render_hypothesis,
    select_skill_for_mechanism, validate_ranking_features,
)


class MechanismModelTests(unittest.TestCase):
    def test_unknowns_and_user_evidence_are_rendered_without_diagnosis(self):
        situation = SituationSnapshot(None, 1, None, "отчёт не начат", "открыть документ", "work", "start", 70, 30, "today")
        hypothesis = MechanismHypothesis(None, 2, "unclear_next_action", "low", ("не назван первый шаг",), ("открыт ли документ",), ("Документ уже открыт?",))
        text = render_hypothesis(situation, hypothesis)
        self.assertIn("Ты сообщил", text)
        self.assertIn("Пока неизвестно", text)
        self.assertIn("не диагнозом", text)

    def test_low_confidence_allows_at_most_one_question(self):
        with self.assertRaises(ValueError):
            MechanismHypothesis(None, 1, "overwhelm", "low", (), (), ("one?", "two?"))

    def test_diagnosis_is_never_a_ranking_key(self):
        with self.assertRaises(ValueError):
            validate_ranking_features({"diagnosis": "ADHD", "mechanism_code": "attention_drift"})

    def test_skill_selection_uses_mechanism_not_day_or_diagnosis(self):
        hypothesis = MechanismHypothesis(None, 1, "attention_drift", "medium", ("переключился на телефон",), ("поможет ли убрать телефон",))
        self.assertEqual(select_skill_for_mechanism(hypothesis, {"phone_far_3min", "open_only"}), "phone_far_3min")

    def test_low_confidence_asks_only_one_question_when_skill_classes_diverge(self):
        hypothesis = MechanismHypothesis(None, 1, "overwhelm", "low", ("назвал задачу комом",), ("неясен размер шага",), ("Можно открыть только файл?",))
        self.assertEqual(clarification_question(hypothesis, ("attention_drift",)), "Можно открыть только файл?")
        self.assertTrue(can_start_without_clarification(hypothesis, safety_risk=False))

    def test_structured_llm_output_rejects_diagnosis_and_requires_evidence(self):
        with self.assertRaises(ValueError):
            hypothesis_from_structured_features(1, {"mechanism_code": "overwhelm", "diagnosis": "ADHD", "evidence": ["сложно"]})
        with self.assertRaisesRegex(ValueError, "evidence"):
            hypothesis_from_structured_features(1, {"mechanism_code": "overwhelm", "confidence": "low"})


if __name__ == "__main__":
    unittest.main()
