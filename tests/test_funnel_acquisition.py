"""PATCH-80: campaign -> existing funnel events, without extra user steps."""
import json
import unittest
from unittest.mock import AsyncMock, patch

import aiosqlite
import bot
from core.acquisition import start_source
from sheets_sync import JOURNEY_EVENT_EXPORT_TYPES, journey_event_to_sheet_row
from test_dialogue_ux_patch import Message
import test_launch_fixes_20261011 as fixtures


class Acquisition(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LaunchJourneys.asyncSetUp
    asyncTearDown = fixtures.LaunchJourneys.asyncTearDown
    user = fixtures.LaunchJourneys.user

    def test_only_explicit_bounded_campaign_tokens_are_accepted(self):
        self.assertEqual(start_source('/start src_video01'), 'src_video01')
        self.assertEqual(start_source('/start@SkillerBot src_video-02'), 'src_video-02')
        for text in ('/start', '/start обычная история', '/start email@example.com',
                     '/start src_' + 'a'*61, '/start src_video extra', '/help src_video'):
            self.assertIsNone(start_source(text))

    async def test_campaign_is_saved_on_actual_start_without_losing_onboarding_step(self):
        await self.user(stage='await_trainer', name='Иван')
        for token in ('src_video01', 'src_video02'):
            m = Message(self.uid, '/start ' + token)
            await bot.cmd_start(m)
        async with aiosqlite.connect(self.path) as db:
            rows = await (await db.execute(
                "SELECT user_id,event_data FROM events WHERE event_name='acquisition_source_opened' ORDER BY id"
            )).fetchall()
        self.assertEqual([json.loads(row[1])['source'] for row in rows], ['src_video01', 'src_video02'])
        self.assertTrue(all(row[0] == self.uid for row in rows))
        self.assertEqual((await bot.get_user(self.uid, self.path))['stage'], 'await_trainer')

    async def test_unlabelled_payload_does_not_record_campaign(self):
        await self.user(stage='await_trainer', name='Иван')
        with patch.object(bot, 'log_event', AsyncMock()) as log:
            await bot.cmd_start(Message(self.uid, '/start sensitive-text'))
        self.assertFalse(any(call.args[1] == 'acquisition_source_opened' for call in log.await_args_list))

    def test_export_joins_source_and_receipt_without_telegram_identity(self):
        events = ('acquisition_source_opened', 'offer_request_submitted',
                  'offer_request_notification_sent', 'offer_email_sent')
        rows = []
        for event in events:
            self.assertIn(event, JOURNEY_EVENT_EXPORT_TYPES)
            rows.append(journey_event_to_sheet_row(
                {'id':1, 'user_id':self.uid, 'event_name':event,
                 'event_data':json.dumps({'source':'src_video01', 'request':'PRIVATE STORY'})},
                secret_salt='synthetic-test-salt'))
        self.assertEqual(len({row[2] for row in rows}), 1)
        self.assertEqual(rows[0][8], 'src_video01')
        self.assertNotIn('PRIVATE STORY', str(rows))
        self.assertNotIn(str(self.uid), str(rows))

    def test_group_card_contains_confirmed_terms_without_unapproved_inclusions(self):
        text = bot.tariff_group_text()
        for term in ('€240', '10 онлайн-встреч', '20 часов'):
            self.assertIn(term, text)
        for promise in ('задания каждый день', 'чат-поддержка', 'материалы после встреч'):
            self.assertNotIn(promise, text)
