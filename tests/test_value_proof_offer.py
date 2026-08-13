import unittest
from decimal import Decimal

from core.engine import should_show_offer
from core.product_config import assert_production_payment_safety
from core.value_proof_offer import (
    PersonalValueReport, ValueProof, evaluate_value_proof, render_base_unlock_offer,
    render_no_value_review, render_personal_value_report,
)


class ValueProofOfferTests(unittest.TestCase):
    def proof(self, **changes):
        values = dict(
            completed_experiments=2, successful_or_partial=1,
            personalized_insight_exists=True, user_has_seen_value_report=True,
            safety_active=False, current_day=3,
        )
        values.update(changes)
        return ValueProof(**values)

    def test_offer_requires_every_value_proof_condition(self):
        self.assertTrue(evaluate_value_proof(self.proof()).eligible)
        for change in (
            {"completed_experiments": 1}, {"successful_or_partial": 0},
            {"personalized_insight_exists": False}, {"user_has_seen_value_report": False},
            {"safety_active": True}, {"current_day": 2},
        ):
            with self.subTest(change=change):
                self.assertFalse(evaluate_value_proof(self.proof(**change)).eligible)

    def test_day_three_alone_does_not_trigger_engine_offer(self):
        self.assertFalse(should_show_offer({"day": 3}))
        self.assertTrue(should_show_offer({
            "day": 3, "completed_experiments": 2, "successful_or_partial": 1,
            "personalized_insight_exists": True, "value_report_seen_at": "2026-08-06",
        }))

    def test_report_shows_personal_facts_before_offer(self):
        text = render_personal_value_report(PersonalValueReport(
            "слишком большой первый шаг", "открыть документ", "получилось начать",
            "вход через маленький шаг",
        ))
        self.assertIn("Что мешало: слишком большой первый шаг", text)
        self.assertIn("Какой эксперимент проверили: открыть документ", text)
        self.assertIn("Что изменилось: получилось начать", text)
        self.assertIn("Что разумно закрепить: вход через маленький шаг", text)

    def test_price_is_injected_without_code_change(self):
        self.assertIn("€7.25", render_base_unlock_offer(price=Decimal("7.25")))

    def test_no_value_path_offers_free_review_not_sale(self):
        text = render_no_value_review()
        self.assertIn("бесплатно пересмотреть механизм", text)
        self.assertNotIn("€", text)

    def test_payment_accept_any_is_forbidden_in_production(self):
        with self.assertRaisesRegex(RuntimeError, "forbidden"):
            assert_production_payment_safety(payment_accept_any=True, app_env="production")
        assert_production_payment_safety(payment_accept_any=True, app_env="test")


if __name__ == "__main__":
    unittest.main()
