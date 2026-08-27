import unittest
import tempfile
from unittest.mock import AsyncMock, patch

import bot
from core.post_action_feedback import ReflectionContext, build_post_action_reflection
from db import get_user, get_user_profile, init_db, migrate_db, save_user


class FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class FakeMessage:
    def __init__(self, user_id, text):
        self.from_user = FakeUser(user_id)
        self.text = text
        self.voice = None
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


class PostActionFeedbackTests(unittest.TestCase):
    def test_success_names_observed_behavior_and_creates_one_specific_anchor(self):
        result = build_post_action_reflection(ReflectionContext(
            "позвонить эксперту", "evaluation_avoidance", "Проверить прогноз",
            "открыть контакт и написать первую фразу", True, False, "helped", True,
        ))
        text = result.render()
        self.assertIn("открыть контакт", text)
        self.assertIn("Сегодня заметили:", text)
        self.assertEqual(text.count("Запомнить:"), 1)
        self.assertNotIn("Отличная работа", text)
        self.assertNotIn("Маленькие шаги ведут", text)

    def test_failure_interprets_reason_without_blame_or_identical_retry(self):
        result = build_post_action_reflection(ReflectionContext(
            "открыть отчёт", "unclear_instruction", "Открыть только файл",
            "открыть файл", False, False, "not_helped", False,
        ))
        self.assertIn("первое действие осталось неясным", result.interpretation)
        self.assertIn("не повторить то же самое", result.interpretation)
        self.assertIn("результат проверки, а не оценка тебя", result.reaction)

    def test_failure_keyboard_has_four_hypotheses_and_other(self):
        user = bot.default_user(1)
        labels = [button.text for row in bot.post_action_reason_keyboard(user).keyboard for button in row]
        self.assertEqual(len(labels), 5)
        self.assertEqual(labels[-1], "Другая причина")

    def test_substantive_message_beats_completed_day_but_buttons_do_not(self):
        self.assertTrue(bot.closed_day_substantive_message("Снова не могу позвонить клиенту"))
        self.assertFalse(bot.closed_day_substantive_message("🎯 Разобрать ещё одну ситуацию"))
        self.assertFalse(bot.closed_day_substantive_message("/start"))

    def test_completed_day_menu_has_required_open_actions(self):
        labels = [button.text for row in bot.kb_completed_day_open.keyboard for button in row]
        self.assertEqual(labels, [
            "🎯 Разобрать ещё одну ситуацию", "⚡ Дать короткий навык",
            "🧠 Что я сегодня понял", "✏️ Исправить вывод",
            "🌙 На сегодня хватит",
        ])
        self.assertNotIn("📚 Материал по моей ситуации", labels)


class NeverDeadEndIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_feedback_flow_persists_one_anchor_before_next_step(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            await migrate_db(file.name)
            user = bot.default_user(902)
            user.update({
                "stage": "training", "current_skill": "open_only", "daily_skill_id": "open_only",
                "daily_skill_name": "Открыть задачу", "current_task_title": "написать клиенту",
                "current_next_physical_step": "открыть чат клиента", "current_day_id": "902:1",
            })
            bot.mark_action_card_active(user)
            await save_user(user, file.name)
            old_path = bot.DB_PATH
            bot.DB_PATH = file.name
            messages = [
                FakeMessage(902, "✅ Сделал"), FakeMessage(902, "Да"),
                FakeMessage(902, "Помогло"), FakeMessage(902, "Продолжил задачу"),
            ]
            try:
                for message in messages:
                    await bot.main_flow(message)
            finally:
                bot.DB_PATH = old_path
            stored = await get_user(902, file.name)
            profile = await get_user_profile(902, file.name)
            self.assertEqual(stored["stage"], "post_action_reflection")
            self.assertIn("открыть чат клиента", profile["last_memory_anchor"])
            rendered = "\n".join(messages[-1].answers)
            self.assertEqual(rendered.count("Запомнить:"), 1)
            self.assertNotIn("Отличная работа", rendered)

    async def test_substantive_message_after_completion_starts_additional_analysis(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            await migrate_db(file.name)
            user = bot.default_user(901)
            user.update({
                "stage": "day_core_stop", "day_closed": 1, "today_closed": 1,
                "daily_training_completed": 1, "interaction_allowed": 1,
                "last_day_closed_at": bot.local_date_for_user(user),
            })
            await save_user(user, file.name)
            old_path = bot.DB_PATH
            bot.DB_PATH = file.name
            message = FakeMessage(901, "Не могу заставить себя написать клиенту")
            try:
                with patch.object(bot, "run_analysis", new=AsyncMock()) as analysis:
                    await bot.main_flow(message)
                    analysis.assert_awaited_once()
                    self.assertEqual(analysis.await_args.args[2], message.text)
            finally:
                bot.DB_PATH = old_path
            stored = await get_user(901, file.name)
            self.assertEqual(stored["daily_training_completed"], 1)
            self.assertEqual(stored["interaction_allowed"], 1)
            self.assertEqual(stored["stage"], "closed_day_additional_analysis")


if __name__ == "__main__":
    unittest.main()
