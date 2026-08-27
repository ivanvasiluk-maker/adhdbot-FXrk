"""Offline regressions for deterministic dialog routing (no Telegram/OpenAI calls)."""
import json
import unittest
from unittest.mock import ANY, AsyncMock, patch

import bot
import flows


class FakeMessage:
    def __init__(self, text=""):
        self.answers = []
        self.text = text
        self.voice = None
        self.from_user = type("User", (), {"id": 71})()
        self.chat = type("Chat", (), {"id": 71})()

    async def answer(self, text, **kwargs):
        self.answers.append(text)


class FakeCallback:
    def __init__(self, data):
        self.data = data
        self.from_user = type("User", (), {"id": 71})()
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))


class DialogRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_barrier_choice_advances_and_second_press_is_stale(self):
        user = bot.default_user(71)
        user.update({
            "stage": "analysis_need_more",
            "analysis_json": json.dumps({"bucket": "mixed", "input_signal_summary": {}}),
        })
        message = FakeMessage()
        with patch.object(bot, "save_user", new=AsyncMock()), patch.object(
            bot, "answer_with_keyboard", new=AsyncMock()
        ) as answer, patch.object(bot, "run_analysis", new=AsyncMock()) as llm:
            await bot.continue_analysis_from_barrier(message, user, "😵 Перегруз")
            self.assertEqual(user["stage"], "confirm_analysis")
            self.assertEqual(json.loads(user["analysis_json"])["selected_barrier"], "overload")
            self.assertNotIn("Что чаще ломает вход", answer.await_args.args[2])
            llm.assert_not_awaited()
            # The same reply-keyboard label is no longer accepted by this state.
            self.assertNotEqual(user["stage"], "analysis_need_more")

    async def test_versioned_barrier_callback_rejects_old_screen(self):
        user = bot.default_user(71)
        user.update({
            "stage": "awaiting_barrier_choice", "current_screen_id": "analysis_live",
            "analysis_json": json.dumps({"analysis_id": "analysis_live", "bucket": "mixed"}),
        })
        current = FakeCallback("barrier:analysis_live:overload")
        stale = FakeCallback("barrier:analysis_old:overload")
        with patch.object(bot, "get_user", new=AsyncMock(return_value=user)), patch.object(
            bot, "save_user", new=AsyncMock()
        ), patch.object(bot, "claim_callback_once", new=AsyncMock(return_value=True)), patch.object(
            bot, "answer_with_keyboard", new=AsyncMock()
        ), patch.object(bot, "reject_stale_callback", new=AsyncMock()) as reject, patch.object(
            bot, "run_analysis", new=AsyncMock()
        ) as llm:
            await bot.on_barrier_callback(current)
            self.assertEqual(user["stage"], "confirm_analysis")
            await bot.on_barrier_callback(stale)
        reject.assert_awaited_once()
        llm.assert_not_awaited()

    def test_barrier_keyboard_carries_analysis_id(self):
        markup = flows.barrier_choice_keyboard("analysis_123")
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertEqual(len(callbacks), 5)
        self.assertTrue(all(value.startswith("barrier:analysis_123:") for value in callbacks))

    def test_new_local_day_clears_only_daily_routing_state(self):
        user = bot.default_user(72)
        user.update({
            "day_date": "2026-08-21", "last_day_closed_at": "2026-08-21T20:00:00+00:00",
            "day_closed": 1, "today_closed": 1, "active_experiment": {"id": 3},
            "current_action_context": "experiment", "profile_json": {"learned": True},
            "skill_history": ["open_only"],
        })
        with patch.object(bot, "local_date_for_user", return_value="2026-08-22"):
            self.assertTrue(bot.reset_daily_state_for_local_date(user))
        self.assertFalse(bot.day_closed_today(user))
        self.assertIsNone(user["active_experiment"])
        self.assertEqual(user["profile_json"], {"learned": True})
        self.assertEqual(user["skill_history"], ["open_only"])

    def test_simplification_keeps_same_skill_and_reduces_minimum(self):
        self.assertEqual(bot.simplified_step("Открыть и начать за 60 секунд"), "только открыть за 10 секунд")
        self.assertEqual(bot.simplified_step("Написать фразу и сделать 3 действия"), "написать 1–3 слова и сделать 1 действие")

    def test_finish_and_full_map_are_commands(self):
        self.assertEqual(bot.global_button_kind("🌙 Завершить", "завершить"), "close_day")
        self.assertEqual(bot.global_button_kind("📖 Полная карта", "📖 полная карта"), "map")
        self.assertTrue(bot.is_known_reply_button("📖 Полная карта"))

    async def test_full_map_routes_to_existing_full_report_builder(self):
        user = bot.default_user(74)
        with patch.object(bot, "log_event", new=AsyncMock()), patch.object(
            bot, "send_user_map", new=AsyncMock()
        ) as send_map, patch.object(bot, "run_analysis", new=AsyncMock()) as llm:
            handled = await bot.handle_global_button(FakeMessage(), user, "📖 Полная карта")
        self.assertTrue(handled)
        send_map.assert_awaited_once_with(ANY, user, "full_map")
        llm.assert_not_awaited()

    def test_action_context_is_cleared_when_action_closes(self):
        user = bot.default_user(75)
        user["current_action_context"] = "crisis_stabilization"
        bot.set_current_state(user, bot.STATE_PAUSED, close_action=True)
        self.assertIsNone(user["current_action_context"])

    def test_same_action_id_is_counted_as_one_attempt(self):
        user = bot.default_user(76)
        user.update({"current_action_id": "act_same", "day": 1})
        bot.record_skill_attempt_start(user, "open_only")
        bot.record_skill_attempt_start(user, "open_only", source="screen_reopened")
        attempts = bot.user_skill_attempts(user)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["action_id"], "act_same")

    async def test_crisis_done_uses_crisis_continuation_not_experiment(self):
        user = bot.default_user(71)
        user.update({
            "stage": "crisis_action_await", "current_action_context": "crisis_stabilization",
            "pending_crisis_pattern": "overload", "day_date": bot.local_date_for_user(user),
        })
        message = FakeMessage("✅ Сделал")
        harmless = AsyncMock()
        with patch.object(bot, "get_user", new=AsyncMock(return_value=user)), patch.object(
            bot, "save_user", new=AsyncMock()
        ), patch.object(bot, "log_event", new=harmless), patch.object(
            bot, "get_user_profile", new=AsyncMock(return_value={})
        ), patch.object(bot, "record_return_after_stuck_if_needed", new=AsyncMock(return_value=False)), patch.object(
            bot, "handle_safety_mode", new=AsyncMock(return_value=False)
        ), patch.object(bot, "handle_admin_command", new=AsyncMock(return_value=False)), patch.object(
            bot, "handle_user_command", new=AsyncMock(return_value=False)
        ), patch.object(bot, "handle_reactivation_reply", new=AsyncMock(return_value=False)), patch.object(
            bot, "handle_analysis_clarification_answer", new=AsyncMock(return_value=False)
        ), patch.object(bot, "handle_skill_result_feedback", new=AsyncMock(return_value=False)), patch.object(
            bot, "handle_feedback_response", new=AsyncMock(return_value=False)
        ), patch.object(bot, "handle_full_mode_buttons", new=AsyncMock(return_value=False)), patch.object(
            bot, "answer_with_keyboard", new=AsyncMock()
        ) as answer, patch.object(bot, "run_analysis", new=AsyncMock()) as llm:
            await bot.main_flow(message)
        self.assertEqual(user["stage"], "crisis_effect_await")
        self.assertIn("crisis_effect", answer.await_args.args[4])
        llm.assert_not_awaited()

    async def test_crisis_minimum_screen_is_sent_once(self):
        user = bot.default_user(71)
        user["day_date"] = bot.local_date_for_user(user)
        message = FakeMessage()
        with patch.object(bot, "get_user_profile", new=AsyncMock(return_value={})), patch.object(
            bot, "record_profile_signal", new=AsyncMock()
        ), patch.object(bot, "log_event", new=AsyncMock()), patch.object(
            bot, "save_user", new=AsyncMock()
        ):
            await bot.send_crisis_tool(message, user, "слишком много задач и перегруз")
        self.assertEqual(message.answers.count("Сделай минимум и отметь результат."), 1)

    async def test_navigation_labels_in_text_prompt_make_zero_llm_calls(self):
        labels = ["Продолжить", "Назад", "Выбрать позже", "Посмотреть карту", "Что я сегодня понял", "Дать короткий навык"]
        llm = AsyncMock()
        with patch.object(bot, "run_analysis", new=llm), patch.object(
            bot, "save_user", new=AsyncMock()
        ), patch.object(bot, "show_context_fallback", new=AsyncMock()):
            for label in labels:
                user = bot.default_user(71)
                user.update({"stage": "await_problem_text", "day_date": bot.local_date_for_user(user)})
                with patch.object(bot, "get_user", new=AsyncMock(return_value=user)):
                    await bot.main_flow(FakeMessage(label))
        llm.assert_not_awaited()

    async def test_one_situation_plus_ten_buttons_runs_analysis_once(self):
        user = bot.default_user(71)
        user.update({"stage": "await_problem_text", "day_date": bot.local_date_for_user(user)})
        analysis = AsyncMock()
        buttons = [
            "Продолжить", "Назад", "Выбрать позже", "Посмотреть карту",
            "Что я сегодня понял", "Дать короткий навык", "✅ Сделал", "↘️ Нужно проще",
            "🟡 Не получилось", "🌙 Завершить",
        ]
        with patch.object(bot, "get_user", new=AsyncMock(return_value=user)), patch.object(
            bot, "save_user", new=AsyncMock()
        ), patch.object(bot, "run_analysis", new=analysis), patch.object(
            bot, "handle_safety_mode", new=AsyncMock(return_value=False)
        ), patch.object(bot, "handle_admin_command", new=AsyncMock(return_value=False)), patch.object(
            bot, "handle_user_command", new=AsyncMock(return_value=False)
        ), patch.object(bot, "handle_reactivation_reply", new=AsyncMock(return_value=False)), patch.object(
            bot, "show_context_fallback", new=AsyncMock()
        ), patch.object(bot, "send_user_map", new=AsyncMock()), patch.object(
            bot, "close_day_from_global_button", new=AsyncMock()
        ):
            await bot.main_flow(FakeMessage("Не могу начать отчёт и ухожу в телефон"))
            for label in buttons:
                await bot.main_flow(FakeMessage(label))
        self.assertEqual(analysis.await_count, 1)

    def test_internal_enum_names_are_sanitized(self):
        rendered = bot.public_enum_text("overload + scroll_autopilot + fear_of_error")
        self.assertEqual(rendered, "перегруз + автоматически ухожу в быстрые стимулы + страх ошибки")

    async def test_correction_updates_saved_conclusion_without_analysis(self):
        user = bot.default_user(73)
        user["analysis_json"] = json.dumps({"selected_barrier": "overload"})
        with patch.object(bot, "save_user", new=AsyncMock()), patch.object(
            bot, "answer_with_keyboard", new=AsyncMock()
        ), patch.object(bot, "run_analysis", new=AsyncMock()) as llm:
            await bot.apply_conclusion_correction(FakeMessage(), user, "Дело скорее в усталости")
        saved = json.loads(user["analysis_json"])
        self.assertEqual(saved["short_conclusion"], "Дело скорее в усталости")
        self.assertEqual(saved["selected_barrier"], "overload")
        llm.assert_not_awaited()

    def test_navigation_and_barrier_labels_are_never_substantive_text(self):
        labels = [
            *bot.BARRIER_BUTTONS, "🌙 Завершить", "Продолжить", "↘️ Нужно проще",
            "✅ Сделал", "📖 Полная карта", "✏️ Исправить вывод",
            "📚 Почему это работает", "🤷 Не моё", "🔁 Ещё круг",
        ]
        for label in labels:
            with self.subTest(label=label):
                self.assertFalse(bot.closed_day_substantive_message(label), label)


if __name__ == "__main__":
    unittest.main()
