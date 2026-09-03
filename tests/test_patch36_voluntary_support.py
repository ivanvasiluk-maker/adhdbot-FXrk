import unittest
from unittest.mock import patch

import bot


class Patch36VoluntarySupportTests(unittest.TestCase):
    def labels(self):
        return [button.text for row in bot.offer_inline_keyboard(1).inline_keyboard for button in row]

    def test_link_alone_enables_voluntary_monthly_support(self):
        with patch.object(bot, "VOLUNTARY_SUPPORT_URL", "https://buy.stripe.com/skiller-support"):
            self.assertTrue(bot.voluntary_support_available())
            self.assertIn("💚 Поддержать SKILLER — €4,99/мес", self.labels())

    def test_missing_or_unsafe_link_hides_payment_button(self):
        for value in ("", "http://buy.stripe.com/test", "https://example.com/payment"):
            with self.subTest(value=value), patch.object(bot, "VOLUNTARY_SUPPORT_URL", value):
                self.assertFalse(bot.voluntary_support_available())
                self.assertFalse(any("Поддержать SKILLER" in label for label in self.labels()))

    def test_support_does_not_enable_paid_mode_or_disable_free_beta(self):
        with patch.object(bot, "VOLUNTARY_SUPPORT_URL", "https://buy.stripe.com/skiller-support"):
            self.assertTrue(bot.FREE_BETA_ACCESS)
            self.assertFalse(bot.paid_plan_available())


if __name__ == "__main__":
    unittest.main()
