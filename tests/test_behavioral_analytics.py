import tempfile
import unittest

from core.behavioral_analytics import (
    BehavioralAnalyticsEvent, build_kpis, safe_sheet_payload,
)
from db import get_behavioral_kpis, init_db, record_behavioral_analytics_event
from sheets_sync import (
    behavioral_analytics_to_sheet_row, event_to_sheet_row, payment_to_sheet_row,
    sanitize_event_data, user_to_sheet_row,
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


if __name__ == "__main__":
    unittest.main()
