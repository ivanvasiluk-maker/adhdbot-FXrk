import unittest

import bot
from core.learning_engine import classify_experiment_result
from core.post_action_feedback import ReflectionContext, build_post_action_reflection


class Patch37BetaFeedbackAndOffersTests(unittest.TestCase):
    def test_negative_effect_is_not_saved_as_recommendation(self):
        result = build_post_action_reflection(ReflectionContext(
            "презентация", "distracted", "Одна вкладка", "оставить одну вкладку",
            True, False, "not_helped", False,
        ))
        self.assertEqual(result.memory_anchor, "Этот навык пока не сохраняем. Проверим другой механизм")
        self.assertNotIn("начни с действия", result.memory_anchor)

    def test_partial_helped_and_continued_is_not_failure(self):
        self.assertEqual(
            classify_experiment_result(
                completed=True,
                subjective_effect="helped",
                after_action="continued_target_task",
            ),
            "STRONG_SUCCESS",
        )

    def test_weak_start_routes_next_experiment_to_stay(self):
        user = bot.default_user(37001)
        user["skill_attempts"] = [{
            "skill_id": "visible_next_step",
            "result": "partial",
            "effect": "some",
            "experiment_result": "WEAK_SUCCESS",
            "after_action": "stopped_after_step",
        }]
        self.assertTrue(bot.should_switch_to_consolidation(user))
        self.assertEqual(bot.skill_target_function("consolidation_hold_3min"), "STAY")

    def test_specific_bot_advertising_task_is_extracted(self):
        context = bot.extract_task_context_from_text("Не могу сесть за рекламу бота")
        self.assertEqual(context["current_task_name"], "реклама бота")

    def test_trainers_use_distinct_strategies(self):
        text = bot.trainer_mode_preview_text("marsha", 0, {})
        self.assertIn("уменьшает давление", text)
        self.assertIn("чёткий финиш", text)
        self.assertIn("отдельно проверяет", text)
        self.assertNotIn("Как будет звучать этот же разбор", text)

    def test_offer_copy_has_current_formats(self):
        self.assertIn("€200", bot.tariff_live_text())
        self.assertIn("задания каждый день", bot.tariff_live_text())
        self.assertIn("задания каждый день", bot.tariff_group_text())


if __name__ == "__main__":
    unittest.main()
