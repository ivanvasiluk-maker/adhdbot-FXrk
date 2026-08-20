import unittest

from core.day1_experience import (
    DAY1_ANALYTICS_EVENTS, build_day1_insight, build_day1_map,
    build_day1_result_insight, day1_tomorrow_teaser,
    personalize_skill_instruction, render_day1_map, select_first_experiment,
)


PRESENTATION = (
    "Мне нужно подготовить презентацию для работы. Срок через четыре дня, уже неделю не могу начать. "
    "Когда открываю файл, думаю, что сначала нужно больше информации и хорошая структура. "
    "Её увидит руководство, боюсь выглядеть некомпетентным. В итоге делаю всё ночью перед дедлайном."
)


class Day1ExperienceTests(unittest.TestCase):
    def test_presentation_insight_is_grounded_and_short(self):
        text = build_day1_insight(PRESENTATION, "fear_of_evaluation", "perfectionism")
        self.assertIn("давлением срока", text)
        self.assertIn("могут оценивать", text)
        self.assertIn("поиск информации или структуры", text)
        self.assertIn("пока гипотеза", text)
        self.assertLessEqual(len([part for part in text.split(".") if part.strip()]), 5)
        for invented in ("диагноз", "травм", "твой мозг", "корень проблемы"):
            self.assertNotIn(invented, text.lower())

    def test_first_experiment_directly_tests_mechanism(self):
        available = ["open_only", "bad_draft", "one_visible_step", "phone_away_3_min"]
        self.assertEqual(select_first_experiment("fear_of_evaluation", available), "bad_draft")
        self.assertEqual(select_first_experiment("overload", available), "one_visible_step")
        self.assertEqual(select_first_experiment("attention_drift", available), "phone_away_3_min")

    def test_presentation_instruction_is_specific_and_substantive(self):
        instruction = personalize_skill_instruction({"skill_id": "bad_draft"}, PRESENTATION, distress=35)
        self.assertIn("Открой презентацию", instruction)
        self.assertIn("3 минуты", instruction)
        self.assertIn("банальный первый слайд", instruction)
        self.assertNotIn("важное дело", instruction)

    def test_high_distress_downscales_without_changing_skill(self):
        instruction = personalize_skill_instruction({"skill_id": "bad_draft"}, "подготовить презентацию", distress=85)
        self.assertIn("30 секунд", instruction)
        self.assertIn("банальный первый слайд", instruction)

    def test_result_insight_uses_before_intervention_after_and_confidence(self):
        text = build_day1_result_insight(
            before="презентация не двигалась неделю", intervention="разрешили плохой слайд",
            result="STRONG_SUCCESS", mechanism="страх оценки",
        )
        self.assertIn("презентация не двигалась неделю", text)
        self.assertIn("разрешили плохой слайд", text)
        self.assertIn("продолжил целевую задачу", text)
        self.assertIn("только одна попытка", text)

    def test_failed_map_is_informative_without_false_confirmation(self):
        value = build_day1_map({"task": "презентация", "hypothesis": "страх оценки",
                                "intervention": "плохой слайд", "experiment_result": "FAILED"})
        text = render_day1_map(value)
        self.assertIn("⚪ гипотеза пока не подтвердилась", text)
        self.assertIn("действие пока не получилось", text)
        self.assertNotIn("первая подтверждающая", text)
        self.assertIn("Ещё не знаем", text)

    def test_success_map_and_teasers_create_start_to_stay_curiosity(self):
        value = build_day1_map({"task": "презентация", "hypothesis": "страх оценки",
                                "intervention": "плохой слайд", "experiment_result": "STRONG_SUCCESS"})
        text = render_day1_map(value)
        self.assertIn("🧭 Твоя первая карта", text)
        self.assertIn("продолжил целевую задачу", text)
        self.assertIn("🟡 первая подтверждающая попытка", text)
        for trainer in ("skinny", "marsha", "beck"):
            teaser = day1_tomorrow_teaser(trainer, "STRONG_SUCCESS")
            self.assertTrue("удерж" in teaser.lower() or "stay" in teaser.lower() or "выпад" in teaser.lower())

    def test_required_analytics_funnel_is_declared(self):
        self.assertEqual(DAY1_ANALYTICS_EVENTS, (
            "day1_diagnosis_completed", "first_experiment_started", "first_experiment_completed",
            "first_experiment_result", "continued_target_task", "day1_map_viewed",
            "day1_finished", "returned_day2",
        ))


if __name__ == "__main__":
    unittest.main()
