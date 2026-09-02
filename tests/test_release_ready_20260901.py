import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import bot
from core.trainer_voice import VoiceContent, render_message
from db import (
    get_user, get_user_profile, init_db, log_event, migrate_db, record_action_event,
    save_user, sync_user_state_aliases, update_user_profile,
)


class _User:
    id = 71001
    username = "release_tester"
    first_name = "Release"


class _Message:
    def __init__(self, text=""):
        self.from_user = _User()
        self.chat = type("Chat", (), {"id": self.from_user.id})()
        self.text = text
        self.voice = None
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((str(text), kwargs))


class ReleaseReadyTests(unittest.IsolatedAsyncioTestCase):
    def test_dispatcher_serializes_rapid_updates_per_user(self):
        dispatcher = bot.build_dispatcher()
        self.assertEqual(
            dispatcher.fsm.events_isolation.__class__.__name__, "SimpleEventIsolation",
        )

    def test_internal_codes_and_markdown_aliases_never_reach_user(self):
        self.assertEqual(bot.public_enum_text("unclear_instruction"), "первое действие было непонятно")
        self.assertEqual(bot.public_enum_text("unclear*instruction*"), "первое действие было непонятно")
        self.assertEqual(bot.public_enum_text("phone*away*3*min"), "Телефон вне руки на 3 минуты")
        self.assertEqual(bot.public_enum_text("unknown_machine_token"), "гипотеза ещё проверяется")
        rendered = bot._development_check_text({"label": "unclear_instruction", "evidence_count": 1})
        self.assertNotIn("{", rendered)
        self.assertNotIn("unclear_", rendered)

    def test_confidence_requires_repeated_noncontradictory_evidence(self):
        self.assertEqual(bot.model_confidence_text({}, 10), "средняя")
        self.assertEqual(bot.model_confidence_text({"skills": [{"helpful_count": 1}]}, 1), "низкая")
        self.assertEqual(
            bot.model_confidence_text({"skills": [{"helpful_count": 3, "stuck_count": 1}]}, 5),
            "средняя",
        )
        self.assertEqual(bot.model_confidence_text({"skills": [{"helpful_count": 3}]}, 4), "высокая")

    def test_not_helped_and_continued_are_stored_as_separate_facts(self):
        self.assertEqual(bot.effect_status_from_minimal_feedback("not_helped", True), "neutral")
        rendered = render_message(
            "beck",
            VoiceContent(
                "experiment_result", result="EXECUTED_ONLY", target_function="START",
                facts={"after_action": "continued_target_task", "effect": "did_not_help"},
            ),
        ).text
        self.assertIn("продолжение задачи есть", rendered)
        self.assertIn("не подтверждена", rendered)
        self.assertNotIn("нужного продолжения нет", rendered)

    def test_default_reminders_are_one_evening_and_hard_capped_at_two(self):
        self.assertEqual(bot.default_user(1)["reminder_mode"], "evening_only")
        self.assertEqual(bot.MAX_PROACTIVE_PER_DAY, 2)

    def test_open_beta_unlocks_bot_but_sells_group_and_personal_support(self):
        user = bot.default_user(99001)
        self.assertEqual(user["payment_status"], "beta_free")
        self.assertEqual(user["full_mode"], 1)
        self.assertEqual(user["free_mode"], 1)
        self.assertTrue(bot.is_paid(user))
        self.assertFalse(bot.can_show_offer(user, {}))
        self.assertFalse(bot.paid_plan_available())
        labels = [button.text for row in bot.offer_inline_keyboard(user["user_id"]).inline_keyboard for button in row]
        self.assertIn("👥 Хочу в группу — €240", labels)
        self.assertIn(f"👤 Хочу личную работу — от €{bot.HUMAN_SKILL_SESSION_EUR_LABEL}", labels)
        self.assertFalse(any("оплат" in label.lower() for label in labels))

    def test_open_beta_upgrades_existing_unpaid_state_without_reset(self):
        user = bot.default_user(99002)
        user.update({"payment_status": "trial", "access_status": "trial", "full_mode": 0, "free_mode": 0, "done_count": 7})
        normalized = sync_user_state_aliases(user)
        self.assertEqual(normalized["payment_status"], "beta_free")
        self.assertEqual(normalized["access_status"], "beta_free")
        self.assertEqual(normalized["full_mode"], 1)
        self.assertEqual(normalized["done_count"], 7)

    def test_paid_offer_has_honest_manual_verification_path(self):
        with patch.object(bot, "FREE_BETA_ACCESS", False), patch.object(
            bot, "PAYMENT_MONTH_URL", "https://buy.stripe.com/real-link",
        ):
            buttons = [button for row in bot.tariff_bot_inline_keyboard(1).inline_keyboard for button in row]
        claim = [button for button in buttons if button.callback_data == bot.OFFER_CALLBACKS["payment_claim"]]
        self.assertEqual(len(claim), 1)
        self.assertIn("проверить", claim[0].text.lower())

    async def test_honest_counts_use_post_action_outcomes(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            await migrate_db(file.name)
            user = bot.default_user(71001)
            user["current_day_id"] = "release-day"
            await record_action_event(
                user["user_id"], file.name, "skill_result_reported", day_id="release-day",
                metadata={"after_action": "continued_target_task"},
            )
            await record_action_event(
                user["user_id"], file.name, "skill_result_reported", day_id="release-day",
                metadata={"after_action": "stopped_after_step"},
            )
            await record_action_event(user["user_id"], file.name, "returned_after_slip", day_id="release-day")
            with patch.object(bot, "DB_PATH", file.name):
                counts = await bot.get_honest_day_counts(user)
        self.assertEqual(counts["attempts_today"], 2)
        self.assertEqual(counts["continued_actions_today"], 1)
        self.assertEqual(counts["stopped_after_step_today"], 1)
        self.assertEqual(counts["returns_today"], 1)

    async def test_restart_confirmation_fully_erases_user_and_owns_router_message(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            await migrate_db(file.name)
            user = bot.default_user(_User.id)
            user.update({
                "name": "Иван",
                "first_start_date": "2026-08-01",
                "has_started_training": 1,
                "done_count": 9,
                "stage": "restart_onboarding_confirm",
                "analysis_json": '{"_skiller_session":{"state":"DAY1_CLARIFY"}}',
            })
            await save_user(user, file.name)
            await update_user_profile(
                _User.id, {"main_hypothesis": "старый вывод"}, file.name, source="restart_test",
            )
            await log_event(_User.id, "old_event", {}, db_path=file.name)
            message = _Message("Да, начать всё заново")
            with patch.object(bot, "DB_PATH", file.name):
                await bot.main_flow(message)
                fresh = await get_user(_User.id, file.name)
                profile = await get_user_profile(_User.id, file.name)

            self.assertEqual(fresh["stage"], "ask_name")
            self.assertFalse(fresh.get("name"))
            self.assertFalse(fresh.get("first_start_date"))
            self.assertEqual(int(fresh.get("done_count") or 0), 0)
            self.assertNotIn("main_hypothesis", profile)
            self.assertTrue(any("полностью удалён" in text for text, _ in message.answers))

    async def test_day_close_collects_dbt_style_card_before_conclusion(self):
        user = bot.default_user(_User.id)
        user.update({"day": 1, "current_day_id": "day-review-1", "stage": "training"})
        message = _Message()
        answer = AsyncMock()
        with patch.object(bot, "save_user", new=AsyncMock()), patch.object(
            bot, "answer_with_keyboard", new=answer,
        ), patch.object(bot, "record_profile_signal", new=AsyncMock()) as record_signal, patch.object(
            bot, "mark_day_closed", new=AsyncMock(),
        ), patch.object(bot, "log_event", new=AsyncMock()), patch.object(
            bot, "get_user_profile", new=AsyncMock(return_value={}),
        ), patch.object(bot, "scheduled_offer_due", return_value=False), patch.object(
            bot, "can_show_offer", return_value=False,
        ), patch.object(bot, "day_close_metrics_text", new=AsyncMock(return_value="Предварительное заключение за день")):
            await bot.start_day_review(message, user, "test")
            self.assertEqual(user["stage"], "day_review_function")
            await bot.handle_day_review(message, user, "STAY — начал и остановился")
            self.assertEqual(user["stage"], "day_review_barrier")
            await bot.handle_day_review(message, user, "Телефон / YouTube")
            self.assertEqual(user["stage"], "day_review_state")
            await bot.handle_day_review(message, user, "Напряжённо")

        review = record_signal.await_args.args[2]["last_day_review"]
        self.assertEqual(review["function"], "stay")
        self.assertIn("телефон", review["barrier"])
        self.assertEqual(review["state"], "напряжённо")
        self.assertEqual(user["stage"], "day_core_stop")
        self.assertEqual(answer.await_args.args[2], "Предварительное заключение за день")


if __name__ == "__main__":
    unittest.main()
