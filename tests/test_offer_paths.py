import unittest

from bot import (
    BASE_OFFER_EUR_LABEL, OFFER_CALLBACKS, offer_details_full_mode_text,
    offer_inline_keyboard, tariff_bot_text, tariff_group_text, tariff_live_text,
)


class OfferPathTests(unittest.TestCase):
    def test_main_offer_has_exactly_four_continuation_paths(self):
        rows = offer_inline_keyboard(123).inline_keyboard[:4]
        callbacks = [row[0].callback_data for row in rows]
        self.assertEqual(callbacks, [
            OFFER_CALLBACKS["stay_free"], OFFER_CALLBACKS["bot"],
            OFFER_CALLBACKS["group"], OFFER_CALLBACKS["live"],
        ])

    def test_subscription_is_founding_offer_at_configured_price(self):
        text = tariff_bot_text()
        self.assertIn(f"€{BASE_OFFER_EUR_LABEL} / месяц", text)
        self.assertIn("Founding Member", text)
        self.assertIn("Learning Engine", text)

    def test_group_and_consultation_terms_are_explicit(self):
        group = tariff_group_text()
        self.assertIn("12 недель", group)
        self.assertIn("€360", group)
        self.assertIn("€120 в месяц", group)
        self.assertIn("Иван Василюк", group)
        self.assertIn("€59", tariff_live_text())

    def test_comparison_names_all_four_paths(self):
        text = offer_details_full_mode_text()
        for label in ("Бесплатно", "Подписка", "Группа КПТ", "Индивидуальная консультация"):
            self.assertIn(label, text)


if __name__ == "__main__":
    unittest.main()
