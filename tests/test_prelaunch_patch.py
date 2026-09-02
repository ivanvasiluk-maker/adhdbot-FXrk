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
        self.assertIn("👥 Хочу в группу — €240", labels)
        self.assertIn(f"👤 Хочу личную работу — от €{bot.HUMAN_SKILL_SESSION_EUR_LABEL}", labels)
        self.assertIn("Продолжить бесплатный тест", labels)
        self.assertNotIn("Другие форматы поддержки", labels)
        self.assertFalse(any("Оплатить" in label for label in labels))

        with patch.object(bot, "ENABLE_PAYMENTS", True), patch.object(
            bot, "ENABLE_PAID_PLAN", True,
        ), patch.object(bot, "PAYMENT_URL", "https://pay.stripe.com/skiller-full"):
            enabled_labels = [
                button.text
                for row in bot.offer_inline_keyboard(1).inline_keyboard
                for button in row
            ]
        self.assertIn("👥 Хочу в группу — €240", enabled_labels)
        self.assertFalse(any("Оплатить" in label for label in enabled_labels))

    def test_manual_beta_offer_advertises_product_without_live_checkout(self):
        with patch.object(bot, "ENABLE_PAYMENTS", False), patch.object(
            bot, "ENABLE_GROUP_OFFER", False,
        ), patch.object(bot, "ENABLE_HUMAN_OFFER", False):
            text = bot.short_offer_text()
            labels = [button.text for row in bot.offer_details_inline_keyboard(1).inline_keyboard for button in row]
        self.assertIn("Сам тест SKILLER", text)
        self.assertNotIn("€", text)
        self.assertNotIn("Группа навыков", text)
        self.assertNotIn("С человеком", labels)
        self.assertEqual(labels, ["Продолжить бесплатный тест", "↩️ Назад"])

    def test_day1_completion_can_unlock_offer_after_value_report(self):
        user = bot.default_user(1)
        user.update({
            "day": 1, "day_closed": 1, "today_closed": 1,
            "last_day_closed_at": bot.local_date_for_user(user),
            "free_mode": 0, "full_mode": 0,
        })
        profile = self.profile()
        profile.update({
            "personalized_insight_exists": True, "value_report_seen_at": "2026-08-22T10:00:00+00:00",
            "personal_working_model": {**profile["personal_working_model"], "evidence_count": 1},
        })
        with patch.object(bot, "offer_recently_limited", return_value=False):
            self.assertFalse(bot.can_show_offer(user, profile))
            with patch.object(bot, "FREE_BETA_ACCESS", False):
                self.assertTrue(bot.can_show_offer(user, profile))

    def test_offer_is_due_at_day_three_close_and_weekly_for_active_user(self):
        now = bot.dt.datetime(2026, 8, 22, 20, 30, tzinfo=bot.dt.timezone.utc)
        user = bot.default_user(1)
        user.update({
            "day": 3, "day_closed": 1, "today_closed": 1,
            "last_day_closed_at": bot.local_date_for_user(user),
            "free_mode": 0, "full_mode": 0,
        })
        self.assertFalse(bot.scheduled_offer_due(user, {}, now=now))
        with patch.object(bot, "FREE_BETA_ACCESS", False):
            self.assertTrue(bot.scheduled_offer_due(user, {}, now=now))
        user.update({"day": 10, "last_offer_shown_at": (now - bot.dt.timedelta(days=7)).isoformat(), "last_active": now.timestamp()})
        self.assertFalse(bot.scheduled_offer_due(user, {}, now=now))
        with patch.object(bot, "FREE_BETA_ACCESS", False):
            self.assertTrue(bot.scheduled_offer_due(user, {}, now=now))
        user["last_active"] = (now - bot.dt.timedelta(days=8)).timestamp()
        self.assertFalse(bot.scheduled_offer_due(user, {}, now=now))

    def test_reminder_windows_start_at_eight(self):
        self.assertEqual(bot.MORNING_REMINDER_WINDOW, (8, 0, 9, 0))
        self.assertEqual(bot.EVENING_REMINDER_WINDOW, (20, 0, 21, 0))

    def test_global_proactive_limit_is_shared_by_all_reminder_types(self):
        user = bot.default_user(1)
        today = "2026-08-26"
        for _ in range(bot.MAX_PROACTIVE_PER_DAY):
            bot._increment_proactive_count(user, today)
        self.assertTrue(bot._proactive_limit_reached(user, today))
        self.assertEqual(bot._proactive_count_today(user, today), 2)

    def test_offer_leads_with_personal_conclusion_and_solution_route(self):
        user = bot.default_user(1)
        user["current_task_title"] = "закончить презентацию"
        profile = self.profile()
        profile.update({"last_successful_skill": "bad_draft", "action_done_count": 3})
        summary = bot.build_profile_map_summary(user, profile)
        text = bot.offer_screen_text(user, summary, profile)
        self.assertIn("Краткое заключение", text)
        self.assertIn("Главный узел", text)
        self.assertIn("Что вижу", text)
        self.assertIn("Что будем делать", text)
        self.assertIn("Что надо развивать", text)
        self.assertIn("START → STAY → RETURN", text)
        self.assertIn("Группа навыков", text)
        self.assertIn("€240", text)
        self.assertIn("Личная работа", text)
        self.assertIn(f"от €{bot.HUMAN_SKILL_SESSION_EUR_LABEL}", text)
        self.assertIn("тест SKILLER пока остаётся бесплатным", text)

    def test_day_one_skill_names_the_problem_and_solution_route(self):
        user = bot.default_user(1)
        user.update({"day": 1, "current_task_title": "закончить презентацию"})
        profile = self.profile()
        skill = dict(bot.SKILLS_DB["bad_draft"])
        skill.setdefault("skill_id", "bad_draft")
        text = bot.new_day_skill_card_text(skill, user, profile)
        for section in (
            "Навык дня — для твоей ситуации", "закончить презентацию",
            "Что вижу", "Что будем делать", "Что развиваем",
            "START", "STAY", "RETURN", "После ответа я обновлю карту",
        ):
            self.assertIn(section, text)
        self.assertIn("Цель не качество", text)
        self.assertNotIn("Даём другой вход, чтобы не крутить один и тот же навык", text)

    def test_every_visible_reply_button_is_registered_as_navigation(self):
        unregistered = {}
        for name, value in vars(bot).items():
            if isinstance(value, bot.ReplyKeyboardMarkup):
                missing = [
                    button.text for row in value.keyboard for button in row
                    if not bot.is_known_reply_button(button.text)
                ]
                if missing:
                    unregistered[name] = missing
        self.assertEqual(unregistered, {})

    async def test_day_one_map_is_visible_before_first_action(self):
        user = bot.default_user(501)
        user.update({
            "day": 1, "done_count": 0, "stage": "training",
            "current_task_title": "закончить презентацию",
        })
        message = Message("🧭 Моя карта")
        answer = AsyncMock()
        with patch.object(bot, "get_user_profile", new=AsyncMock(return_value=self.profile())), patch.object(
            bot, "build_skill_map_data", new=AsyncMock(return_value={})
        ), patch.object(bot, "save_user", new=AsyncMock()), patch.object(
            bot, "log_event", new=AsyncMock()
        ), patch.object(bot, "answer_with_keyboard", new=answer):
            await bot.send_user_map(message, user, "persistent_button")
        rendered = answer.await_args.args[2]
        self.assertIn("Карта дня", rendered)
        self.assertIn("Что вижу сейчас", rendered)
        self.assertIn("Что будем делать", rendered)
        self.assertIn("Что развиваем", rendered)
        self.assertNotIn("Карту покажу после первого действия", rendered)

    def test_short_map_prefers_specific_hypothesis_over_placeholder(self):
        user = bot.default_user(1)
        user["current_task_title"] = "подготовить презентацию"
        profile = self.profile()
        profile["main_hypothesis"] = "страх ошибки или оценки"
        profile["personal_working_model"]["recurring_barriers"] = {"барьер уточняется": 5}
        text = bot.short_daily_map_text(profile, {}, user)
        self.assertIn("страх ошибки или оценки", text)
        self.assertNotIn("барьер уточняется", text)

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
