import unittest
from unittest.mock import AsyncMock, patch

import bot


class Patch35FiveBetaFixesTests(unittest.IsolatedAsyncioTestCase):
    async def test_evening_answers_are_used_in_immediate_conclusion(self):
        user = bot.default_user(35001)
        user.update({"current_day_id": "patch35-day", "trainer_key": "beck"})
        review = {
            "function": "start",
            "barrier": "скука или отсутствие быстрой отдачи",
            "state": "спокойно или устойчиво",
        }
        counts = {
            "attempts_today": 0, "continued_actions_today": 0,
            "stopped_after_step_today": 0, "returns_today": 0,
        }
        with patch.object(bot, "get_honest_day_counts", new=AsyncMock(return_value=counts)), patch.object(
            bot, "get_user_profile", new=AsyncMock(return_value={})
        ), patch.object(bot, "build_skill_map_data", new=AsyncMock(return_value={"skills": []})):
            text = await bot.day_close_metrics_text(user, review)
        self.assertIn("START — вход в задачу", text)
        self.assertIn("скука или отсутствие быстрой отдачи", text)
        self.assertIn("спокойно или устойчиво", text)
        self.assertNotIn("Состояние: не отмечено", text)

    def test_skill_effect_is_split_between_start_and_stay(self):
        profile = {"last_skill_feedback": {
            "skill_id": "phone_away_3_min", "completed": True,
            "helpfulness": "some", "continued_after_skill": False,
        }}
        text = bot.day1_profile_card_text(bot.default_user(35002), profile, 1)
        self.assertIn("START — «Телефон вне руки на 3 минуты» помог начать", text)
        self.assertIn("STAY — после старта продолжить не удалось", text)
        self.assertNotIn("Пока не помогало", text)

    def test_public_copy_never_exposes_technical_skill_ids(self):
        self.assertEqual(bot.public_enum_text("entry_small_step"), "Маленький видимый шаг")
        self.assertEqual(bot._skill_label("unknown_internal_skill", "unknown_internal_skill"), "этот навык")
        text = bot.skill_map_lines({"skills": [{"skill_id": "unknown_internal_skill", "status": "proposed"}]})
        self.assertNotIn("unknown_internal_skill", text)

    def test_offer_back_menu_is_short_and_has_no_full_mode(self):
        text = bot.offer_menu_text()
        self.assertLess(len(text), 240)
        self.assertNotIn("Полный режим", text)
        self.assertNotIn("Краткое заключение", text)

    def test_ordinary_stuck_and_safety_are_distinct(self):
        self.assertIn("⚡ Сильно застрял", bot.PROCRASTINATION_CRISIS_BUTTONS)
        self.assertFalse(bot.crisis_safety_check("Всё слишком большое").get("high_risk"))
        self.assertTrue(bot.crisis_safety_check("я могу навредить себе прямо сейчас").get("high_risk"))


if __name__ == "__main__":
    unittest.main()
