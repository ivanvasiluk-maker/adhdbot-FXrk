import unittest

from core.conclusion_engine import (
    WorkingModelState, apply_experiment, create_prediction, new_hypothesis,
    model_from_analysis, model_from_dict, render_full_working_model, render_short_conclusion,
    test_prediction as check_prediction, update_model, update_next_untested_prediction,
)


def model():
    fear = new_hypothesis("fear_of_evaluation", "STRONG_HYPOTHESIS",
                          evidence=("презентацию увидит руководство",),
                          unknown=("поможет ли разрешение на черновик",))
    overload = new_hypothesis("overload", "MODERATE_HYPOTHESIS",
                              evidence=("задача описана как большая",))
    attention = new_hypothesis("attention_switching", "UNKNOWN")
    prediction = create_prediction(
        "fear_of_evaluation",
        "Если напряжение от оценки играет основную роль, несовершенный старт облегчит продолжение.",
        "После плохого черновика пользователь продолжит исходную задачу.",
    )
    return WorkingModelState("презентация для руководства", "стопор перед первой оцениваемой версией",
                             ("важная задача", "надо сделать хорошо", "подготовка вместо старта"),
                             (fear, overload, attention), (prediction,), next_experiment="Плохой черновик")


class ConclusionEngineTests(unittest.TestCase):
    def test_short_keeps_competing_hypotheses_and_one_primary(self):
        text = render_short_conclusion(model())
        self.assertIn("Страх оценки", text)
        self.assertIn("Перегруз", text)
        self.assertEqual(text.count("основная рабочая гипотеза"), 1)

    def test_strong_success_adds_evidence_without_proof_claim(self):
        state = model()
        updated = update_model(state, state.predictions[0].prediction_id, "STRONG_SUCCESS",
                               experiment_name="Плохой черновик", result_detail="продолжил задачу")
        self.assertTrue(updated.primary.evidence_for)
        self.assertNotIn("причина доказана", render_full_working_model(updated).lower())

    def test_executed_only_does_not_promote(self):
        state = model()
        hypothesis = replace_status(state.primary, "MODERATE_HYPOTHESIS")
        updated = apply_experiment(hypothesis, state.predictions[0], "EXECUTED_ONLY",
                                   experiment_name="Плохой черновик")
        self.assertEqual(updated.status, "MODERATE_HYPOTHESIS")
        self.assertFalse(updated.evidence_for[1:])

    def test_two_unsupported_tests_can_change_primary(self):
        state = model()
        for _ in range(2):
            state = update_model(state, state.predictions[0].prediction_id, "EXECUTED_ONLY",
                                 experiment_name="Плохой черновик", result_detail="не продолжил")
        self.assertEqual(state.primary.hypothesis_id, "overload")

    def test_no_fake_percentages(self):
        self.assertNotRegex(render_short_conclusion(model()), r"\d+%")

    def test_prediction_requires_observable_outcome(self):
        with self.assertRaises(ValueError):
            create_prediction("overload", "Будет трудно", "")

    def test_prediction_status_transitions(self):
        prediction = model().predictions[0]
        self.assertEqual(check_prediction(prediction, "STRONG_SUCCESS", detail="продолжил").status, "SUPPORTED")
        self.assertEqual(check_prediction(prediction, "EXECUTED_ONLY", detail="не продолжил").status, "NOT_SUPPORTED")
        self.assertEqual(check_prediction(prediction, "FAILED", detail="не сделал").status, "INCONCLUSIVE")

    def test_full_model_uses_supplied_state(self):
        text = render_full_working_model(model())
        self.assertIn("презентация для руководства", text)
        self.assertNotIn("дедлайн", text)

    def test_no_personality_diagnosis(self):
        text = (render_short_conclusion(model()) + render_full_working_model(model())).lower()
        for phrase in ("у тебя сдвг", "у тебя расстройство", "ты перфекционист"):
            self.assertNotIn(phrase, text)

    def test_voice_intro_does_not_mutate_facts(self):
        state = model()
        skinny = render_full_working_model(state, trainer_intro="Вот что уже видно. Не диагноз — рабочая схема.")
        beck = render_full_working_model(state, trainer_intro="Данных ещё немного, но соберём рабочую модель.")
        self.assertNotEqual(skinny, beck)
        self.assertEqual(state.hypotheses, model().hypotheses)
        self.assertIn(state.situation, skinny)
        self.assertIn(state.situation, beck)

    def test_analysis_adapter_builds_competing_persistable_model(self):
        state = model_from_analysis(
            situation="презентация для руководства",
            blockage_point="стопор перед первым слайдом",
            pattern="perfectionism_visibility_fear",
            evidence=("результат увидит руководство", "бытовые задачи запускаются легче"),
            next_experiment="Плохой черновик",
        )
        restored = model_from_dict(state.as_dict())
        self.assertEqual(restored.primary.hypothesis_id, "fear_of_evaluation")
        self.assertIn("overload", {item.hypothesis_id for item in restored.hypotheses})
        self.assertEqual(restored.predictions[0].status, "UNTESTED")

    def test_real_feedback_updates_only_pending_prediction(self):
        state = model_from_dict(model().as_dict())
        updated = update_next_untested_prediction(
            state, "STRONG_SUCCESS", experiment_name="Плохой черновик",
            result_detail="После шага продолжил задачу.",
        )
        self.assertEqual(updated.predictions[0].status, "SUPPORTED")
        repeated = update_next_untested_prediction(
            updated, "EXECUTED_ONLY", experiment_name="Повторное нажатие",
            result_detail="Не должно примениться.",
        )
        self.assertEqual(repeated, updated)


def replace_status(hypothesis, status):
    from dataclasses import replace
    return replace(hypothesis, status=status)


if __name__ == "__main__":
    unittest.main()
