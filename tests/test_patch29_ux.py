import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bot


def keyboard_texts(markup):
    return {button.text for row in markup.keyboard for button in row}


def inline_callbacks(markup):
    return {
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    }


class Patch29UxTests(unittest.IsolatedAsyncioTestCase):
    def test_morning_buttons_match_router_and_evening_starts_by_button(self):
        self.assertLessEqual(
            keyboard_texts(bot.kb_morning_checkin),
            set(bot.MORNING_STATE_SKILL_MAP) | set(bot.LEGACY_MORNING_STATE_ALIASES),
        )
        self.assertEqual(keyboard_texts(bot.kb_evening_checkin), {
            "🌙 Подвести итоги дня", "🌙 Закрыть без разбора",
        })
        self.assertIn("Как ты сегодня", bot.morning_checkin_text())

    def test_every_post_exercise_menu_exposes_skill_and_trainer_switches(self):
        for markup in (
            bot.kb_post_action_reflection,
            bot.kb_experiment_completed,
            bot.kb_simplified_skill_after_effect,
            bot.kb_extra_microstep_done,
            bot.kb_success_no_extra,
            bot.kb_success_limit,
            bot.kb_stuck_aftercare,
        ):
            labels = keyboard_texts(markup)
            self.assertIn("🔄 Сменить навык", labels)
            self.assertIn("🎭 Сменить тренера", labels)

    async def test_map_is_global_even_while_diagnostic_prompt_is_active(self):
        user = bot.default_user(29001)
        user["stage"] = "await_problem_text"
        message = AsyncMock()
        with patch.object(bot, "log_event", AsyncMock()), patch.object(
            bot, "send_user_map", AsyncMock()
        ) as send_map:
            handled = await bot.handle_global_button(message, user, "🧭 Посмотреть карту")
        self.assertTrue(handled)
        send_map.assert_awaited_once()

    def test_new_day_keeps_hypotheses_and_direction(self):
        text = bot.new_day_context_header({"action_done_count": 1})
        self.assertIn("рабочие гипотезы", text)
        self.assertIn("не начинаем с нуля", text)
        self.assertIn("приблизимся к рабочему решению", text)
        self.assertNotIn("не делаем выводов", text)

    def test_day_close_always_has_a_specific_insight(self):
        insight = bot.day_review_insight_text({
            "function": "stay",
            "barrier": "телефон перехватил внимание",
            "state": "напряжённо",
        })
        self.assertIn("STAY", insight)
        self.assertIn("телефон", insight)
        self.assertIn("Завтра не начинаем заново", insight)

    async def test_voice_transcription_downloads_telegram_ogg_and_returns_text(self):
        message = SimpleNamespace(
            voice=SimpleNamespace(file_id="voice-id", file_unique_id="voice-unique"),
            from_user=SimpleNamespace(id=29002),
            bot=SimpleNamespace(
                get_file=AsyncMock(return_value=SimpleNamespace(file_path="voice/file.ogg")),
                download_file=AsyncMock(return_value=io.BytesIO(b"fake-ogg")),
            ),
        )
        create = MagicMock(return_value=SimpleNamespace(text="исправь вывод: дело в тревоге"))
        fake_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
        with patch.object(bot, "client", fake_client), patch.object(bot, "log_event", AsyncMock()):
            result = await bot.whisper_transcribe(message)
        self.assertEqual(result, "исправь вывод: дело в тревоге")
        self.assertEqual(create.call_args.kwargs["model"], bot.OPENAI_WHISPER_MODEL)
        self.assertTrue(create.call_args.kwargs["file"].name.endswith(".ogg"))

    async def test_voice_correction_stage_is_supported(self):
        message = SimpleNamespace(
            voice=SimpleNamespace(file_id="voice-id"),
            answer=AsyncMock(),
        )
        user = {"user_id": 29003, "stage": "personal_model_correction"}
        with patch.object(
            bot, "whisper_transcribe", AsyncMock(return_value="главный стопор — страх ошибки")
        ), patch.object(bot, "log_event", AsyncMock()):
            result = await bot.transcribe_voice_for_current_prompt(message, user)
        self.assertEqual(result, "главный стопор — страх ошибки")

    async def test_manual_offer_shows_real_price_before_free_beta_disclosure(self):
        user = bot.default_user(29004)
        show = AsyncMock()
        with patch.object(bot, "FREE_BETA_ACCESS", True), patch.object(
            bot, "log_event", AsyncMock()
        ), patch.object(bot, "show_day3_offer", show):
            await bot.force_show_offer(AsyncMock(), user, "test")
        self.assertEqual(show.await_args.kwargs["mode"], "manual_beta_intent")
        self.assertIn(f"€{bot.BASE_OFFER_EUR_LABEL}/мес", bot.short_offer_text())
        self.assertNotIn("доступен бесплатно", bot.short_offer_text())
        self.assertIn(
            bot.OFFER_CALLBACKS["beta_purchase_intent"],
            inline_callbacks(bot.offer_inline_keyboard(user["user_id"])),
        )

    async def test_beta_payment_click_records_intent_without_access_mutation(self):
        user = bot.default_user(29005)
        before = {key: user.get(key) for key in (
            "payment_status", "access_status", "full_mode", "free_mode",
        )}
        callback = SimpleNamespace(
            from_user=SimpleNamespace(id=user["user_id"]),
            data=bot.OFFER_CALLBACKS["beta_purchase_intent"],
            message=SimpleNamespace(answer=AsyncMock()),
            answer=AsyncMock(),
        )
        log = AsyncMock()
        with patch.object(bot, "FREE_BETA_ACCESS", True), patch.object(
            bot, "get_user", AsyncMock(return_value=user)
        ), patch.object(bot, "save_user", AsyncMock()), patch.object(
            bot, "log_event", log
        ), patch.object(bot, "handle_safety_callback", AsyncMock(return_value=False)):
            await bot.on_offer_callbacks(callback)
        after = {key: user.get(key) for key in before}
        self.assertEqual(after, before)
        self.assertTrue(any(
            call.args[2] == "beta_purchase_intent_recorded"
            for call in log.await_args_list
        ))
        answer = callback.message.answer.await_args.args[0]
        self.assertIn("списания не будет", answer)
        self.assertIn("доступен тебе бесплатно", answer)


if __name__ == "__main__":
    unittest.main()
