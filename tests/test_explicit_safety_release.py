import unittest
from unittest.mock import AsyncMock, patch

import bot
from core.explicit_safety import explicit_safety_intent
from db import default_user


class ExplicitSafetyRelease(unittest.IsolatedAsyncioTestCase):
    def test_direct_intent_reaches_global_detector(self):
        for text in (
            "Я убью себя сегодня", "Я собираюсь повеситься",
            "Я покончу с собой", "Я хочу застрелиться",
            "I want to kill myself", "I will end my life", "Я хачу памерці",
        ):
            with self.subTest(text=text):
                self.assertTrue(bot.has_crisis_safety_signal(text, "training"))

    def test_supplement_does_not_match_ordinary_task(self):
        for text in ("У меня есть план закончить отчёт", "Я убью время за игрой", "Хочу начать отчёт"):
            self.assertFalse(explicit_safety_intent(text))

    def test_resolved_statement_does_not_mask_current_intent(self):
        for text in ("У меня есть план закончить отчёт", "Я больше не думаю о суициде"):
            self.assertFalse(bot.has_crisis_safety_signal(text, "training"))
            self.assertFalse(bot.has_red_crisis_phrase(text))
        for text in ("У меня есть план", "Я больше не думаю о суициде, но сегодня убью себя", "Не уверен, что не покончу с собой"):
            self.assertTrue(bot.has_crisis_safety_signal(text, "training"))

    async def test_intent_intercepts_before_training_or_offer(self):
        from types import SimpleNamespace
        for stage in ("training", "offer_request_form"):
            user = default_user(990027)
            user.update(stage=stage)
            message = SimpleNamespace(
                text="Я убью себя сегодня", voice=None,
                from_user=SimpleNamespace(id=990027),
            )
            with patch.object(bot, "get_user", AsyncMock(return_value=user)), \
                 patch.object(bot, "save_user", AsyncMock()), \
                 patch.object(bot, "dialogue_message", side_effect=lambda m, u: m), \
                 patch.object(bot, "start_safety_interceptor", AsyncMock()) as intercept:
                await bot.main_flow(message)
                intercept.assert_awaited_once()
