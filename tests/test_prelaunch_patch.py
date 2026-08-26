import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import bot


class User:
    id = 501
    username = "tester"
    first_name = "Test"


class Message:
    def __init__(self, text=""):
        self.text = text
        self.from_user = User()
        self.chat = type("Chat", (), {"id": 501})()
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((str(text), kwargs))


class Callback:
    def __init__(self, data):
        self.data = data
        self.from_user = User()
        self.message = Message()
        self.answered = False

    async def answer(self, *args, **kwargs):
        self.answered = True


class PrelaunchPatchTests(unittest.IsolatedAsyncioTestCase):
    def profile(self):
        return {
            "main_hypothesis": "overload",
            "attention_pattern": "scroll_autopilot",
            "last_skill_completed": True,
            "last_continued_after_skill": False,
            "personal_working_model": {
                "recurring_barriers": {"перегруз": 2},
                "helpful_interventions": {"Открыть без таймера": 1},
                "unhelpful_interventions": {"Таймер на 3 минуты": 1},
                "evidence_count": 2,
            },
        }

    def test_day1_insight_is_grounded_and_falsifiable(self):
        text, prediction = bot.day1_insight_text(bot.default_user(1), self.profile())
        self.assertIn("Текущая рабочая модель", text)
        self.assertIn("Что мы пока не знаем", text)
        self.assertIn("🔮 Проверим прогноз", text)
        self.assertIn("первые минуты", prediction)
        self.assertNotIn("scroll_autopilot", text)

    def test_day1_insight_does_not_invent_positive_signal(self):
        profile = self.profile()
        profile["personal_working_model"]["helpful_interventions"] = {}
        text, _ = bot.day1_insight_text(bot.default_user(1), profile)
        self.assertIn("полезный сигнал пока не подтверждён", text)
        self.assertNotIn("Первый полезный сигнал:", text)

    def test_day1_profile_and_full_map_have_required_sections(self):
        user = bot.default_user(1)
        user["skill_attempts"] = [{"action_id": "one"}, {"action_id": "two"}]
        profile = self.profile()
        card = bot.day1_profile_card_text(user, profile, 2)
        full_map = bot.render_prelaunch_full_map(user, profile, {},)
        for section in ("START", "STAY", "RETURN", "Экспериментов", "Уверенность модели"):
            self.assertIn(section, card)
        for section in ("Что сейчас чаще ломается", "Что уже помогало", "Что пока неизвестно", "Следующий эксперимент", "Данных собрано"):
            self.assertIn(section, full_map)
        self.assertNotIn("scroll_autopilot", full_map)

    def test_offer_ladder_uses_prelaunch_entry_prices(self):
        labels = [button.text for row in bot.offer_inline_keyboard(1).inline_keyboard for button in row]
        self.assertIn("🟢 Продолжить бесплатно", labels)
        self.assertFalse(any("€4.99/мес" in label for label in labels))
        self.assertTrue(any("€20–24" in label for label in labels))
        self.assertTrue(any("от €39" in label for label in labels))

        with patch.object(bot, "ENABLE_PAYMENTS", True), patch.object(
            bot, "ENABLE_PAID_PLAN", True,
        ), patch.object(bot, "PAYMENT_URL", "https://pay.stripe.com/skiller-full"):
            enabled_labels = [
                button.text
                for row in bot.offer_inline_keyboard(1).inline_keyboard
                for button in row
            ]
        self.assertTrue(any("€4.99/мес" in label for label in enabled_labels))

    def test_day1_completion_can_unlock_offer_after_value_report(self):
        user = bot.default_user(1)
        user.update({"day": 1, "day_closed": 1, "today_closed": 1, "last_day_closed_at": bot.local_date_for_user(user)})
        profile = self.profile()
        profile.update({
            "personalized_insight_exists": True, "value_report_seen_at": "2026-08-22T10:00:00+00:00",
            "personal_working_model": {**profile["personal_working_model"], "evidence_count": 1},
        })
        with patch.object(bot, "offer_recently_limited", return_value=False):
            self.assertTrue(bot.can_show_offer(user, profile))

    def test_offer_is_due_at_day_three_close_and_weekly_for_active_user(self):
        now = bot.dt.datetime(2026, 8, 22, 20, 30, tzinfo=bot.dt.timezone.utc)
        user = bot.default_user(1)
        user.update({"day": 3, "day_closed": 1, "today_closed": 1, "last_day_closed_at": bot.local_date_for_user(user)})
        self.assertTrue(bot.scheduled_offer_due(user, {}, now=now))
        user.update({"day": 10, "last_offer_shown_at": (now - bot.dt.timedelta(days=7)).isoformat(), "last_active": now.timestamp()})
        self.assertTrue(bot.scheduled_offer_due(user, {}, now=now))
        user["last_active"] = (now - bot.dt.timedelta(days=8)).timestamp()
        self.assertFalse(bot.scheduled_offer_due(user, {}, now=now))

    def test_reminder_windows_start_at_eight(self):
        self.assertEqual(bot.MORNING_REMINDER_WINDOW, (8, 0, 9, 0))
        self.assertEqual(bot.EVENING_REMINDER_WINDOW, (20, 0, 21, 0))

    def test_lead_metadata_reuses_telegram_contact(self):
        meta = bot.lead_metadata({"user_id": 501, "name": "Known"}, User(), "Нужна практика старта", "human")
        self.assertEqual(meta["telegram_user_id"], 501)
        self.assertEqual(meta["contact"], "@tester")
        self.assertEqual(meta["source"], "skiller")
        self.assertEqual(meta["offer_type"], "human")
        self.assertTrue(meta["timestamp"])

    async def test_admin_menu_is_restricted(self):
        user = bot.default_user(501)
        message = Message("/admin")
        with patch.object(bot, "is_admin", return_value=False):
            handled = await bot.handle_admin_command(message, user, "/admin")
        self.assertTrue(handled)
        self.assertIn("недоступна", message.answers[0][0])

    async def test_admin_menu_contains_prelaunch_shortcuts(self):
        user = bot.default_user(501)
        message = Message("/admin")
        with patch.object(bot, "is_admin", return_value=True):
            handled = await bot.handle_admin_command(message, user, "/admin")
        self.assertTrue(handled)
        markup = message.answers[0][1]["reply_markup"]
        labels = [button.text for row in markup.keyboard for button in row]
        self.assertIn("🎯 Jump to Day 1 insight", labels)
        self.assertIn("🧪 Create fake successful experiment", labels)
        self.assertIn("💰 Show offer", labels)

    async def test_prediction_check_is_deterministic(self):
        user = bot.default_user(501)
        user["day_date"] = bot.local_date_for_user(user)
        callback = Callback("prediction:prediction_1:yes")
        update = AsyncMock()
        with patch.object(bot, "get_user", new=AsyncMock(return_value=user)), patch.object(
            bot, "get_user_profile", new=AsyncMock(return_value={
                "day1_prediction_id": "prediction_1", "day1_prediction_status": "UNTESTED",
            })
        ), patch.object(bot, "update_user_profile", new=update), patch.object(
            bot, "log_event", new=AsyncMock()
        ), patch.object(bot, "run_analysis", new=AsyncMock()) as llm:
            await bot.on_prediction_callback(callback)
        self.assertEqual(update.await_args.args[1]["day1_prediction_status"], "SUPPORTED")
        self.assertTrue(callback.answered)
        llm.assert_not_awaited()

    def test_staging_and_production_examples_are_isolated(self):
        staging = Path(".env.staging.example").read_text()
        production = Path(".env.production.example").read_text()
        self.assertIn("APP_ENV=staging", staging)
        self.assertIn("skiller-staging.db", staging)
        self.assertIn("APP_ENV=production", production)
        self.assertIn("skiller-production.db", production)
        self.assertIn("PAYMENT_ACCEPT_ANY=0", production)
        self.assertNotEqual(staging, production)


if __name__ == "__main__":
    unittest.main()
