import inspect
import unittest
from unittest.mock import AsyncMock, patch

import bot
import flows
from db import default_user


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.answers = []

    async def answer(self, text, reply_markup=None, **kwargs):
        self.answers.append({"text": text, "reply_markup": reply_markup})


class ReleaseStabilizationTests(unittest.IsolatedAsyncioTestCase):
    def test_payment_urls_are_explicit_https_and_never_placeholders(self):
        self.assertFalse(bot.is_ready_payment_url(""))
        self.assertFalse(bot.is_ready_payment_url("https://your-payment-link"))
        self.assertFalse(bot.is_ready_payment_url("https://pay.example/test"))
        self.assertFalse(bot.is_ready_payment_url("http://payments.skiller.app/checkout"))
        self.assertTrue(bot.is_ready_payment_url("https://payments.skiller.app/checkout"))

    def test_day_one_conclusion_does_not_immediately_replace_its_keyboard(self):
        run_analysis_source = inspect.getsource(flows.run_analysis)
        self.assertNotIn("ANALYSIS_ACTION_TRANSITION_TEXT", run_analysis_source)
        bot_source = inspect.getsource(bot)
        self.assertEqual(bot_source.count("maybe_show_analysis_action_transition("), 1)

    async def test_triage_yes_enters_active_safety_mode(self):
        user = default_user(101)
        user.update({"stage": "safety_mode", "safety_mode": "triage"})
        message = FakeMessage("🆘 Да, могу быть в опасности")
        with patch.object(bot, "save_user", AsyncMock()), patch.object(bot, "log_event", AsyncMock()):
            consumed = await bot.handle_safety_mode(message, user, message.text)
        self.assertTrue(consumed)
        self.assertEqual(user["safety_mode"], "active")
        self.assertEqual(user["safety_last_risk"], "yes")
        self.assertIs(message.answers[-1]["reply_markup"], bot.kb_safety_not_safe_steps)

    async def test_bad_but_safe_reaches_stabilisation_and_keeps_aftercare_lock(self):
        user = default_user(102)
        user.update({"stage": "safety_mode", "safety_mode": "triage"})
        message = FakeMessage("✅ Я в безопасности, но мне очень плохо")
        with patch.object(bot, "save_user", AsyncMock()), patch.object(bot, "log_event", AsyncMock()):
            self.assertTrue(await bot.handle_safety_mode(message, user, message.text))
            self.assertEqual(user["safety_mode"], "bad_but_safe")
            message.text = "💧 Вода или умыться"
            self.assertTrue(await bot.handle_safety_mode(message, user, message.text))
        self.assertEqual(user["safety_mode"], "support")
        self.assertEqual(user["stage"], "safety_mode")
        self.assertIs(message.answers[-1]["reply_markup"], bot.kb_safety_aftercare)

    async def test_legacy_crisis_buttons_are_consumed(self):
        for label in bot.LEGACY_CRISIS_REDIRECT_BUTTONS:
            with self.subTest(label=label):
                user = default_user(103)
                message = FakeMessage(label)
                with patch.object(bot, "save_user", AsyncMock()), patch.object(bot, "log_event", AsyncMock()):
                    consumed = await bot.handle_legacy_crisis_redirect_button(message, user, label)
                self.assertTrue(consumed)
                self.assertEqual(user["stage"], "safety_mode")
                self.assertNotEqual(bot.safety_mode(user), "none")


if __name__ == "__main__":
    unittest.main()
