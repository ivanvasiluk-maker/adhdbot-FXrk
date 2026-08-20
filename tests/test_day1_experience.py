import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot

from core.day1_experience import (
    DAY1_ANALYTICS_EVENTS, build_day1_insight, build_day1_map, day1_insight_is_complete,
    build_day1_result_insight, day1_tomorrow_teaser,
    extract_day1_context, personalize_skill_instruction, render_day1_map, select_first_experiment,
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

    def test_map_fields_are_grounded_and_completeness_requires_evidence(self):
        context = extract_day1_context(PRESENTATION, "fear_of_evaluation", "perfectionism")
        self.assertIn("могут оценивать", context["break_point"])
        self.assertIn("поиск информации", context["alternative_behavior"])
        data = {**context, "task": "презентация"}
        self.assertTrue(day1_insight_is_complete(data, "STRONG_SUCCESS"))
        self.assertFalse(day1_insight_is_complete({"task": "презентация", "hypothesis": "версия"}, "FAILED"))

    def test_missing_behavior_is_rendered_as_unknown_not_invented(self):
        value = build_day1_map({"task": "письмо", "experiment_result": "EXECUTED_ONLY"})
        text = render_day1_map(value)
        self.assertIn("пока недостаточно данных", text)
        self.assertNotIn("подготовка или переключение", text)

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


class Day1AnalyticsIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_behavioral_win_uses_continuation_not_subjective_success(self):
        user = {
            "user_id": 1, "day": 1, "trainer_key": "marsha", "current_task_title": "презентация",
            "day1_user_input": PRESENTATION, "day1_break_point": "первая версия",
            "day1_trigger": "оценка", "day1_alternative_behavior": "поиск информации",
            "day1_grounded_hypothesis": "оценка повышает требования", "day1_intervention": "плохой слайд",
        }
        message = SimpleNamespace(answer=AsyncMock())
        reflection = SimpleNamespace(tested_principle="плохой слайд")
        logged = []

        async def capture(_uid, _stage, event, metadata, *_args):
            logged.append((event, metadata))

        with patch.object(bot, "log_event", side_effect=capture), patch.object(bot, "save_user", new=AsyncMock()):
            await bot.send_day1_completion_artifact(
                message, user, {"mechanism": "evaluation_avoidance"}, reflection,
                experiment_result="EXECUTED_ONLY", completed=True, partial=False, continued=True,
            )
        self.assertTrue(user["day1_behavioral_win"])
        self.assertIn("continued_target_task", [event for event, _ in logged])
        self.assertIn("first_experiment_completed", [event for event, _ in logged])

    async def test_unexecuted_attempt_does_not_log_completed_event(self):
        user = {"user_id": 1, "day": 1, "current_task_title": "письмо"}
        message = SimpleNamespace(answer=AsyncMock())
        reflection = SimpleNamespace(tested_principle="открыть письмо")
        logged = []

        async def capture(_uid, _stage, event, metadata, *_args):
            logged.append(event)

        with patch.object(bot, "log_event", side_effect=capture), patch.object(bot, "save_user", new=AsyncMock()):
            await bot.send_day1_completion_artifact(
                message, user, {}, reflection, experiment_result="FAILED",
                completed=False, partial=False, continued=False,
            )
        self.assertNotIn("first_experiment_completed", logged)
        self.assertIn("first_experiment_result", logged)


if __name__ == "__main__":
    unittest.main()
