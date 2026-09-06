import io
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import bot
import aiosqlite
from db import init_db, migrate_db, save_user


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
    async def test_known_legacy_qa_code_cannot_enable_test_mode(self):
        user = bot.default_user(28990)
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=user["user_id"]),
            answer=AsyncMock(),
        )
        with patch.object(bot, "TEST_CHEAT_CODE", ""), patch.object(
            bot, "log_event", AsyncMock()
        ):
            handled = await bot.handle_user_command(message, user, "/testmode_on skiller_test")
        self.assertTrue(handled)
        self.assertEqual(user.get("is_test_user"), 0)
        self.assertIn("Код не подошёл", message.answer.await_args.args[0])

    def test_privacy_notice_discloses_processing_and_user_controls(self):
        notice = bot.privacy_notice_text()
        for required in ("OpenAI", "/privacy", "/reset_me", "18 лет", "не диагностика"):
            self.assertIn(required, notice)
        self.assertEqual(keyboard_texts(bot.kb_privacy_consent), {
            "✅ Согласен(на), продолжить", "❌ Не согласен(на)",
        })

    async def test_privacy_consent_is_recorded_before_diagnosis(self):
        user = bot.default_user(28991)
        user["stage"] = "privacy_consent"
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=user["user_id"]),
            chat=SimpleNamespace(id=user["user_id"]),
            text="✅ Согласен(на), продолжить",
            voice=None,
            answer=AsyncMock(),
        )
        saved_profile = {}

        async def update_profile(_uid, patch_data, _db_path):
            saved_profile.update(patch_data)
            return dict(saved_profile)

        with patch.object(bot, "get_user", AsyncMock(return_value=user)), patch.object(
            bot, "save_user", AsyncMock()
        ), patch.object(bot, "update_user_profile", update_profile), patch.object(
            bot, "log_event", AsyncMock()
        ):
            await bot.main_flow(message)
        self.assertEqual(user["stage"], "trainer_intro")
        self.assertTrue(saved_profile["privacy_consent"])
        self.assertTrue(saved_profile["privacy_consent_at"])

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

    def test_short_map_deduplicates_same_helpful_skill(self):
        user = bot.default_user(29010)
        profile = {
            "personal_working_model": {
                "helpful_interventions": {"Сделать следующий шаг видимым": 2},
                "evidence_count": 2,
            },
        }
        skill_map = {"skills": [{"skill_id": "visible_next_step", "status": "confirmed"}]}
        rendered = bot.short_daily_map_text(profile, skill_map, user)
        self.assertEqual(rendered.lower().count("— сделать следующий шаг видимым"), 1)

    def test_feedback_anchor_uses_active_experiment_not_previous_skill(self):
        user = bot.default_user(29011)
        user["current_skill"] = "bad_draft"
        user["active_attempt"] = {
            "skill_id": "visible_next_step",
            "current_skill_id": "visible_next_step",
            "minimum_action": "Оставь одну видимую подсказку.",
        }
        feedback = bot.minimal_feedback_base(user, source="action_done")
        self.assertEqual(feedback["skill_id"], "visible_next_step")
        reflection = bot.build_user_post_action_reflection(
            user,
            {**feedback, "completed": True, "helpfulness": "helped", "continued_after_skill": True},
            {},
        )
        self.assertIn("видимую подсказку", reflection.memory_anchor)
        self.assertNotIn("плох", reflection.memory_anchor.lower())

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

    async def test_manual_offer_sells_group_and_personal_work_while_bot_is_free(self):
        user = bot.default_user(29004)
        show = AsyncMock()
        with patch.object(bot, "FREE_BETA_ACCESS", True), patch.object(
            bot, "log_event", AsyncMock()
        ), patch.object(bot, "show_day3_offer", show):
            await bot.force_show_offer(AsyncMock(), user, "test")
        self.assertEqual(show.await_args.kwargs["mode"], "manual_sales")
        self.assertIn("€240", bot.short_offer_text())
        self.assertIn(f"€{bot.HUMAN_SKILL_SESSION_EUR_LABEL} в месяц", bot.short_offer_text())
        callbacks = inline_callbacks(bot.offer_inline_keyboard(user["user_id"]))
        self.assertIn(bot.OFFER_CALLBACKS["group"], callbacks)
        self.assertIn(bot.OFFER_CALLBACKS["live"], callbacks)
        self.assertNotIn(bot.OFFER_CALLBACKS["beta_purchase_intent"], callbacks)

    async def test_start_does_not_disclose_free_beta_before_payment_click(self):
        user = bot.default_user(29008)
        user.update({"first_start_date": "2026-09-01", "has_started_training": 1})
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=user["user_id"]),
            chat=SimpleNamespace(id=user["user_id"]),
            answer=AsyncMock(),
        )
        with patch.object(bot, "FREE_BETA_ACCESS", True), patch.object(
            bot, "get_user", AsyncMock(return_value=user)
        ), patch.object(bot, "save_user", AsyncMock()), patch.object(
            bot, "get_user_profile", AsyncMock(return_value={})
        ), patch.object(bot, "log_event", AsyncMock()):
            await bot.cmd_start(message)
        rendered = "\n".join(call.args[0] for call in message.answer.await_args_list)
        self.assertIn("Вы уже начали работу со Skiller", rendered)
        self.assertNotIn("beta", rendered.lower())
        self.assertNotIn("бесплат", rendered.lower())

    async def test_automatic_day3_offer_sells_support_without_charging_for_bot(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            old_db_path = bot.DB_PATH
            bot.DB_PATH = file.name
            try:
                await init_db(file.name)
                await migrate_db(file.name)
                user = bot.default_user(29009)
                user.update({"day": 3, "stage": "day_core_stop"})
                await save_user(user, file.name)
                message = SimpleNamespace(answer=AsyncMock())
                await bot.show_day3_offer(message, user, "day3", mode="auto")
            finally:
                bot.DB_PATH = old_db_path
        rendered = "\n".join(call.args[0] for call in message.answer.await_args_list)
        self.assertIn("Группа навыков", rendered)
        self.assertIn("€240", rendered)
        self.assertIn("Личная терапия", rendered)
        self.assertIn("тест SKILLER пока остаётся бесплатным", rendered)
        self.assertNotIn("Оплатить", rendered)
        self.assertEqual(user.get("offer_mode"), "auto")
        self.assertTrue(user.get("last_offer_shown_at"))

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

    async def test_full_reset_deletes_child_evidence_without_touching_other_user(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            await migrate_db(file.name)
            await save_user(bot.default_user(29006), file.name)
            await save_user(bot.default_user(29007), file.name)
            async with aiosqlite.connect(file.name) as db:
                situation = await db.execute(
                    """INSERT INTO situation_snapshots
                    (user_id,created_at,task_summary,desired_action,context_domain,action_phase,
                     emotion_intensity_0_100,energy_0_100,urgency,raw_text_ref)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (29006, "2026-09-01", "отчёт", "открыть", "work", "start", 60, 40, "medium", "private note"),
                )
                situation_id = int(situation.lastrowid)
                other = await db.execute(
                    """INSERT INTO situation_snapshots
                    (user_id,created_at,task_summary,desired_action,context_domain,action_phase,
                     emotion_intensity_0_100,energy_0_100,urgency,raw_text_ref)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (29007, "2026-09-01", "другая", "открыть", "work", "start", 20, 80, "low", "keep"),
                )
                other_situation_id = int(other.lastrowid)
                mechanism = await db.execute(
                    """INSERT INTO mechanism_hypotheses
                    (situation_id,mechanism_code,confidence,evidence_json,unknowns_json,
                     disconfirming_questions_json,source,confirmed_by_user)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (situation_id, "evaluation_avoidance", "medium", "[]", "[]", "[]", "rules", 0),
                )
                mechanism_id = int(mechanism.lastrowid)
                experiment = await db.execute(
                    """INSERT INTO behavioral_experiments
                    (user_id,situation_id,mechanism_hypothesis_id,skill_id,mechanism_code,
                     context_domain,difficulty_level,instruction_variant,target_action,success_criterion,
                     status,progression_type,decision_reason_code,trainer_style,state_revision)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (29006, situation_id, mechanism_id, "bad_draft", "evaluation_avoidance",
                     "work", 1, "one line", "write", "opened", "completed", "first", "initial", "beck", 1),
                )
                experiment_id = int(experiment.lastrowid)
                outcome = await db.execute(
                    """INSERT INTO behavioral_experiment_outcomes
                    (experiment_id,criterion_met,observed_result,created_at) VALUES (?,?,?,?)""",
                    (experiment_id, 0, "private observed result", "2026-09-01"),
                )
                outcome_id = int(outcome.lastrowid)
                await db.execute(
                    """INSERT INTO experiment_outcomes
                    (experiment_id,action_started,action_persisted,emotional_change,
                     success_criterion_met,independent_use,user_note_short,failure_reason_code,captured_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (experiment_id, "yes", "no", "same", 0, 0, "private user note", "too_hard", "2026-09-01"),
                )
                await db.execute(
                    """INSERT INTO behavioral_experiment_decisions
                    (experiment_id,outcome_id,decision,reason_code,created_at)
                    VALUES (?,?,?,?,?)""",
                    (experiment_id, outcome_id, "simplify", "too_hard", "2026-09-01"),
                )
                await db.execute(
                    """INSERT INTO legacy_migration_links
                    (source_table,source_id,target_type,target_id,migrated_at)
                    VALUES (?,?,?,?,?)""",
                    ("skill_attempts", "private-source", "behavioral_experiment", experiment_id, "2026-09-01"),
                )
                await db.commit()

            with patch.object(bot, "DB_PATH", file.name):
                await bot.reset_current_user(29006, 29006)

            async with aiosqlite.connect(file.name) as db:
                for table in (
                    "mechanism_hypotheses", "behavioral_experiments",
                    "behavioral_experiment_outcomes", "experiment_outcomes",
                    "behavioral_experiment_decisions", "legacy_migration_links",
                ):
                    count = (await (await db.execute(f"SELECT COUNT(*) FROM {table}")).fetchone())[0]
                    self.assertEqual(count, 0, table)
                kept = (await (await db.execute(
                    "SELECT COUNT(*) FROM situation_snapshots WHERE id=? AND user_id=?",
                    (other_situation_id, 29007),
                )).fetchone())[0]
                self.assertEqual(kept, 1)


if __name__ == "__main__":
    unittest.main()
