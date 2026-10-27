"""PATCH-79: free bot -> voluntary group application, including failure exits."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from db import get_user
from test_dialogue_ux_patch import Message
import test_funnel_consultation as fixtures


class FunnelReleaseReview(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.FunnelConsultation.asyncSetUp
    asyncTearDown = fixtures.FunnelConsultation.asyncTearDown
    user = fixtures.FunnelConsultation.user
    seed = fixtures.FunnelConsultation.seed
    callback = fixtures.FunnelConsultation.callback

    async def test_restart_during_onboarding_replays_step_instead_of_registered_menu(self):
        for stage in ('await_trainer', 'ask_address', 'notification_consent',
                      'privacy_consent', 'privacy_declined', 'await_input_mode'):
            await self.user(stage=stage, name='Иван', first_start_date=None,
                            profile_completed=0, diagnostic_completed=0, has_started_training=0)
            m = Message(self.uid, '/start')
            await bot.cmd_start(m)
            self.assertEqual((await get_user(self.uid, self.path))['stage'], stage)
            self.assertTrue(m.answers)
            self.assertNotIn('уже начали', str(m.answers))
            if stage != 'privacy_declined':
                self.assertIsNotNone(m.answers[-1][1].get('reply_markup'))
    async def test_arbitrary_application_text_is_not_parsed_as_markdown(self):
        u = await self.seed()
        u['dialogue_context'] = json.dumps({'application_request_id': 'plain-lead-79'})
        m = Message(self.uid)
        async def telegram_send(*args, **kwargs):
            # Production Bot defaults to Markdown. A single '[' or '_' in
            # a real request must not make Telegram reject the notification.
            if kwargs.get('parse_mode', 'Markdown') is not None:
                raise ValueError('Telegram cannot parse entities')
        m.bot = SimpleNamespace(send_message=AsyncMock(side_effect=telegram_send))
        with patch('core.lead_email.email_configured', return_value=False):
            self.assertTrue(await bot.notify_offer_request(m, u, 'group', 'Отчёт [черновик и @test_user'))
        self.assertIn('Отчёт [черновик', m.bot.send_message.call_args.args[1])

    async def test_cancel_without_return_snapshot_has_an_actionable_menu(self):
        u = await self.user(stage='offer_request_form', dialogue_context='{}')
        m = Message(self.uid, '/cancel')
        self.assertTrue(await bot.handle_application_input(m, u, m.text))
        self.assertEqual((await get_user(self.uid, self.path))['stage'], bot.OFFER_MENU_STAGE)
        callbacks = [bot.split_versioned_callback(b.callback_data)[0] for _, kw in m.answers
                     for row in getattr(kw.get('reply_markup'), 'inline_keyboard', []) for b in row]
        self.assertIn(bot.OFFER_CALLBACKS['continue_training'], callbacks)

    async def test_group_journey_receipt_and_return_preserve_original_task(self):
        u = await self.seed()
        m = Message(self.uid)
        await bot.maybe_show_help_offer(m, u, substantive=True)
        await self.callback(bot.OFFER_CALLBACKS['group'], m)
        self.assertIn('€240', m.answers[-1][0])
        self.assertIn('20 часов', m.answers[-1][0])
        await self.callback(bot.OFFER_CALLBACKS['request_group'], m)
        request = Message(self.uid, 'Хочу в группу. Вечером, Вильнюс.')
        request.bot = SimpleNamespace(send_message=AsyncMock())
        with patch('core.lead_email.email_configured', return_value=False):
            await bot.main_flow(request)
        request.bot.send_message.assert_awaited_once()
        self.assertIn('отправил заявку', request.answers[-1][0])
        restored = await self.callback(bot.OFFER_CALLBACKS['continue_training'], m)
        self.assertEqual(restored['stage'], 'post_action_reflection')
        self.assertEqual(restored['today_target'], 'Делать бот')
