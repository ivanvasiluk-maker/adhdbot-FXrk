import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import aiosqlite

from core.behavioral_analytics import (
    BehavioralAnalyticsEvent, build_kpis, safe_sheet_payload,
)
from db import (
    get_behavioral_kpis, init_db, record_action_event,
    record_behavioral_analytics_event,
)
from sheets_sync import (
    action_event_to_sheet_row, behavioral_analytics_to_sheet_row,
    event_to_sheet_row, payment_to_sheet_row, sanitize_event_data,
    sync_action_events, sync_new_user_snapshots, user_to_sheet_row,
)


class BehavioralAnalyticsUnitTests(unittest.TestCase):
    def test_action_start_and_independent_use_are_separate_kpis(self):
        kpis = build_kpis({
            "started_experiments": 4, "action_started": 3, "completed_experiments": 2,
            "independent_uses": 1,
        })
        self.assertEqual(kpis["action_start_rate"], 0.75)
        self.assertEqual(kpis["independent_use_rate"], 0.5)

    def test_sheet_payload_has_only_ids_taxonomy_counts_and_versions(self):
        event = BehavioralAnalyticsEvent(
            "action_started", 123, situation_id=4, experiment_id=5, skill_id="open_only",
            mechanism_code="overwhelm", context_domain="work", outcome_label="partial",
            policy_version="policy-2", ranking_version="rank-3", skill_version=4,
        )
        payload = safe_sheet_payload(event, anonymous_user_id="anonymous")
        self.assertEqual(payload["anonymous_user_id"], "anonymous")
        self.assertNotIn("user_id", payload)
        self.assertFalse(any(key in payload for key in ("text", "transcript", "crisis_text", "prompt")))
        row = behavioral_analytics_to_sheet_row(event.__dict__, secret_salt="private-salt")
        self.assertNotIn(123, row)
        self.assertIn("policy-2", row)
        self.assertIn("rank-3", row)

    def test_free_text_like_taxonomy_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "short taxonomy"):
            BehavioralAnalyticsEvent("action_started", 1, skill_id="private story\nsecond line")
        with self.assertRaisesRegex(ValueError, "short taxonomy"):
            BehavioralAnalyticsEvent("action_started", 1, mechanism_code="моя личная история")

    def test_legacy_serializers_mask_identity_and_free_text(self):
        clean = sanitize_event_data({
            "reason": "Мне очень плохо и это моя личная история",
            "telegram_username": "private_user",
            "pattern": "overwhelm",
        })
        self.assertEqual(clean["reason"], "[masked]")
        self.assertEqual(clean["telegram_username"], "[masked]")
        self.assertEqual(clean["pattern"], "overwhelm")
        event = {"user_id": 123, "event": "action_started", "meta": {"pattern": "overwhelm"}}
        user = {"user_id": 123, "username": "private_user", "name": "Private Name"}
        for row in (event_to_sheet_row(event, user), payment_to_sheet_row(event, user), user_to_sheet_row(user)):
            self.assertNotIn(123, row)
            self.assertNotIn("private_user", row)
            self.assertNotIn("Private Name", row)

    def test_action_export_keeps_taxonomy_but_drops_private_content(self):
        event = {
            "id": 7,
            "user_id": 123456789,
            "day_id": "day-2",
            "attempt_id": 9,
            "event_type": "skill_result_reported",
            "skill_id": "open_only",
            "task_id": "private-task-text",
            "created_at": "2026-09-06T12:00:00+00:00",
            "metadata": '{"result_status":"done","effect":"started_task",'
                        '"reason":"too_hard","source":"skill_feedback",'
                        '"stage":"skill_done","user_text":"private story",'
                        '"button":"Мой личный ответ"}',
        }
        row = action_event_to_sheet_row(
            event, {"name": "Private Name", "username": "private_user"},
            secret_salt="private-salt",
        )
        self.assertIn("done", row)
        self.assertIn("started_task", row)
        self.assertIn("too_hard", row)
        for private_value in (
            123456789, "private-task-text", "private story",
            "Мой личный ответ", "Private Name", "private_user",
        ):
            self.assertNotIn(private_value, row)


class BehavioralAnalyticsPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_funnel_events_and_reproducibility_versions_are_queryable(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            events = (
                BehavioralAnalyticsEvent("experiment_started", 1, experiment_id=1),
                BehavioralAnalyticsEvent("experiment_started", 1, experiment_id=2),
                BehavioralAnalyticsEvent("action_started", 1, experiment_id=1, outcome_label="yes"),
                BehavioralAnalyticsEvent("experiment_completed", 1, experiment_id=1, outcome_label="better"),
                BehavioralAnalyticsEvent("independent_use", 1, experiment_id=1, outcome_label="yes"),
                BehavioralAnalyticsEvent("value_report_viewed", 1),
                BehavioralAnalyticsEvent("offer_shown", 1),
                BehavioralAnalyticsEvent("purchase_confirmed", 1),
            )
            for event in events:
                await record_behavioral_analytics_event(file.name, event)
            result = await get_behavioral_kpis(file.name)
            self.assertEqual(result["kpis"]["action_start_rate"], 0.5)
            self.assertEqual(result["kpis"]["independent_use_rate"], 1.0)
            self.assertEqual(result["kpis"]["value_report_to_offer_rate"], 1.0)
            self.assertEqual(result["kpis"]["offer_to_verified_purchase_rate"], 1.0)


    async def test_new_users_are_exported_once_without_telegram_identity(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            async with aiosqlite.connect(file.name) as db:
                await db.execute(
                    "INSERT INTO users(user_id, created_at, last_active, day, is_test_user) VALUES(?,?,?,?,?)",
                    (123456789, 1720000000, 1720000100, 2, 0),
                )
                await db.commit()

            with patch("sheets_sync.ANALYTICS_ID_SALT", "private-salt"), patch(
                "sheets_sync.post_rows", new_callable=AsyncMock, return_value=(True, '{"ok":true}')
            ) as post:
                first = await sync_new_user_snapshots(file.name, 50)
                second = await sync_new_user_snapshots(file.name, 50)

            self.assertEqual(first["synced"], 1)
            self.assertEqual(second["synced"], 0)
            post.assert_awaited_once()
            rows = post.await_args.args[0]
            self.assertEqual(post.await_args.kwargs["sheet"], "users")
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(rows[0]), 5)
            self.assertNotIn(123456789, rows[0])

    async def test_action_export_retries_then_marks_the_event_once(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            async with aiosqlite.connect(file.name) as db:
                await db.execute(
                    "INSERT INTO users(user_id, day, stage, trainer_key) VALUES(?,?,?,?)",
                    (123456789, 3, "skill_feedback", "marsha"),
                )
                await db.commit()
            await record_action_event(
                123456789, file.name, "skill_result_reported",
                day_id="day-3", attempt_id=11, skill_id="open_only",
                metadata={
                    "result_status": "done", "effect": "started_task",
                    "user_text": "must never leave sqlite",
                },
            )

            with patch("sheets_sync.ANALYTICS_ID_SALT", "private-salt"), patch(
                "sheets_sync.post_rows", new_callable=AsyncMock,
                side_effect=[(False, "temporary error"), (True, '{"ok":true}')],
            ) as post:
                failed = await sync_action_events(file.name, 50)
                synced = await sync_action_events(file.name, 50)
                repeated = await sync_action_events(file.name, 50)

            self.assertEqual(failed["failed"], 1)
            self.assertEqual(synced["synced"], 1)
            self.assertEqual(repeated["synced"], 0)
            self.assertEqual(post.await_count, 2)
            self.assertEqual(post.await_args.kwargs["sheet"], "skill_results")
            row = post.await_args.args[0][0]
            self.assertNotIn(123456789, row)
            self.assertNotIn("must never leave sqlite", row)

    async def test_sensitive_crisis_events_are_not_exported(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            await record_action_event(
                1, file.name, "crisis_started",
                metadata={"user_text": "sensitive crisis context"},
            )
            with patch("sheets_sync.ANALYTICS_ID_SALT", "private-salt"), patch(
                "sheets_sync.post_rows", new_callable=AsyncMock,
            ) as post:
                result = await sync_action_events(file.name, 50)
            self.assertEqual(result["synced"], 0)
            post.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
