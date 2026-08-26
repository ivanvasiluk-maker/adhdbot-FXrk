import unittest
from unittest.mock import patch

import bot
from bot import (
    BASE_OFFER_EUR_LABEL, OFFER_CALLBACKS, offer_details_full_mode_text,
    offer_inline_keyboard, tariff_bot_text, tariff_group_text, tariff_live_text,
)


class OfferPathTests(unittest.TestCase):
    def test_main_offer_has_exactly_four_continuation_paths(self):
        with patch.object(bot, "PAYMENT_URL", "https://pay.skiller.example.org/subscribe"), patch.object(bot, "ENABLE_PAYMENTS", True):
            rows = offer_inline_keyboard(123).inline_keyboard[:4]
        callbacks = [row[0].callback_data for row in rows]
        self.assertEqual(callbacks, [
            OFFER_CALLBACKS["stay_free"], OFFER_CALLBACKS["bot"],
            OFFER_CALLBACKS["group"], OFFER_CALLBACKS["live"],
        ])

    def test_subscription_path_fails_closed_without_real_payment_url(self):
        with patch.object(bot, "PAYMENT_URL", "https://your-payment-link"), patch.object(bot, "PAYMENT_MONTH_URL", ""):
            callbacks = [row[0].callback_data for row in offer_inline_keyboard(123).inline_keyboard]
            self.assertNotIn(OFFER_CALLBACKS["bot"], callbacks)
            self.assertEqual(bot.payment_month_url(), "")

    def test_subscription_is_founding_offer_at_configured_price(self):
        text = tariff_bot_text()
        self.assertIn(f"€{BASE_OFFER_EUR_LABEL} / месяц", text)
        self.assertIn("Founding Member", text)
        self.assertIn("Learning Engine", text)

    def test_group_and_consultation_terms_are_explicit(self):
        group = tariff_group_text()
        self.assertIn("12 недель", group)
        self.assertIn("€240–288", group)
        self.assertIn("€80–96 в месяц", group)
        self.assertIn("Иван Василюк", group)
        self.assertIn("от €39", tariff_live_text())
        self.assertIn("45–60 минут", tariff_live_text())

    def test_comparison_names_all_four_paths(self):
        text = offer_details_full_mode_text()
        for label in ("Бесплатно", "Подписка", "Группа навыков", "Потренировать навык с человеком"):
            self.assertIn(label, text)


if __name__ == "__main__":
    unittest.main()
