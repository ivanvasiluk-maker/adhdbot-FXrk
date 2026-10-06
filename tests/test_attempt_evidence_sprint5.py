import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import bot
from core.attempt_evidence import comparison, find_attempt, PREDICT_BUTTON, ACTUAL_BUTTON
from db import (default_user, get_user, save_user, init_db, migrate_db,
                get_attempt_evidence_metrics, StaleUserWriteError)
from db import get_action_metrics
from test_dialogue_ux_patch import Message


class AttemptEvidenceJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/db'
        self.uid = 55551
        await init_db(self.path)
        await migrate_db(self.path)
        self.p = patch.object(bot, 'DB_PATH', self.path)
        self.p.start()
        u = default_user(self.uid)
        u.update(stage='training', daily_skill_id='open_only', current_day_id='55551:1',
                 current_task_title='PRIVATE TASK', day_core_skill_id='open_only')
        bot.mark_action_card_active(u)
        await save_user(u, self.path)

    async def asyncTearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    async def send(self, text):
        m = Message(self.uid, text)
        with patch.object(bot, 'client', None):
            await bot.main_flow(m)
        return m, await get_user(self.uid, self.path)

    async def counts(self):
        return await get_attempt_evidence_metrics(self.uid, self.path)

    async def callback(self, phase, action_id, value):
        c = SimpleNamespace(from_user=SimpleNamespace(id=self.uid),
            data=f'difficulty:{phase}:{action_id}:{value}', answer=AsyncMock(),
            message=SimpleNamespace(answer=AsyncMock()))
        await bot.on_difficulty_callback(c)
        return c

    async def test_repeated_save_and_card_explanation_are_not_execution(self):
        u = await get_user(self.uid, self.path)
        await save_user(u, self.path)
        await save_user(u, self.path)
        m = Message(self.uid, '')
        await bot.answer_with_keyboard(m, u, 'Тот же шаг', bot.action_keyboard(), 'skill_card')
        await bot.answer_with_keyboard(m, u, 'Тот же шаг', bot.action_keyboard(), 'skill_card')
        counts = await self.counts()
        self.assertEqual(counts['shown'], 1)
        self.assertEqual(counts['completed'], 0)
        self.assertEqual(counts['execution_unknown'], 1)

    async def test_done_is_known_even_without_effect_answer(self):
        await self.send('✅ Сделал')
        counts = await self.counts()
        self.assertEqual(counts['completed'], 1)
        self.assertEqual(counts['felt_better'], 0)
        self.assertEqual(counts['continued'], 0)
        self.assertEqual(counts['execution_unknown'], 0)

    async def test_relief_and_continuation_are_separate(self):
        await self.send('✅ Сделал')
        await self.send('🙂 Стало легче')
        counts = await self.counts()
        self.assertEqual((counts['completed'], counts['felt_better'], counts['continued']), (1, 1, 0))
        u = await get_user(self.uid, self.path)
        bot.mark_action_card_active(u)
        u['stage'] = 'training'
        await save_user(u, self.path)
        await self.send('✅ Сделал')
        await self.send('🚀 Продолжил дело')
        counts = await self.counts()
        self.assertEqual((counts['shown'], counts['completed'], counts['felt_better'], counts['continued']), (2, 2, 1, 1))

    async def test_partial_is_not_full_completion(self):
        await self.send('🟡 Частично')
        await self.send('😐 Без изменений')
        counts = await self.counts()
        self.assertEqual((counts['completed'], counts['partial'], counts['continued']), (0, 1, 0))

    async def test_returned_training_updates_its_own_record(self):
        u = await get_user(self.uid, self.path)
        old_id = bot.active_attempt(u)['attempt_id']
        saved = dict(bot.active_attempt(u))
        bot.mark_action_card_active(u)
        new_id = bot.active_attempt(u)['attempt_id']
        u['active_attempt'] = saved
        u['current_action_id'] = old_id
        f = bot.minimal_feedback_base(u, source='action_done')
        f.update(completed=True, partial=False)
        bot.set_minimal_feedback(u, f)
        bot.update_latest_skill_attempt_result(u, result='completed', effect='helped')
        self.assertEqual(find_attempt(u, old_id)['effect'], 'helped')
        self.assertEqual(find_attempt(u, new_id)['result'], 'started')
        await save_user(u, self.path)
        counts = await self.counts()
        self.assertEqual((counts['shown'], counts['completed'], counts['execution_unknown']), (2, 1, 1))

    async def test_stale_write_does_not_change_evidence(self):
        u = await get_user(self.uid, self.path)
        stale = await get_user(self.uid, self.path)
        await save_user(u, self.path)
        stale['skill_attempts'][0].update(completed=True, result='completed')
        with self.assertRaises(StaleUserWriteError):
            await save_user(stale, self.path)
        self.assertEqual((await self.counts())['completed'], 0)

    async def test_history_limit_does_not_delete_durable_records(self):
        u = await get_user(self.uid, self.path)
        for _ in range(52):
            bot.mark_action_card_active(u)
            await save_user(u, self.path)
        self.assertEqual(len(u['skill_attempts']), 50)
        self.assertEqual((await self.counts())['shown'], 53)

    async def test_difficulty_is_optional_and_keeps_action_screen(self):
        u = await get_user(self.uid, self.path)
        action_id = bot.active_attempt(u)['attempt_id']
        m, after = await self.send(PREDICT_BUTTON)
        self.assertEqual(after['stage'], 'training')
        self.assertEqual(bot.active_attempt(after)['attempt_id'], action_id)
        await self.callback('expected', action_id, '8')
        await self.send('✅ Сделал')
        m, _ = await self.send('🙂 Стало легче')
        labels = [b.text for row in m.answers[0][1]['reply_markup'].keyboard for b in row]
        self.assertIn(ACTUAL_BUTTON, labels)
        c = await self.callback('actual', action_id, '3')
        self.assertIn('8/10, получилось 3/10', c.message.answer.await_args.args[0])
        repeated = await self.callback('actual', action_id, '9')
        repeated.message.answer.assert_not_awaited()
        u = await get_user(self.uid, self.path)
        self.assertEqual(find_attempt(u, action_id)['actual_difficulty'], 3)

    async def test_skip_and_invalid_rating_remain_unknown(self):
        u = await get_user(self.uid, self.path)
        action_id = bot.active_attempt(u)['attempt_id']
        await self.callback('expected', action_id, 'skip')
        await self.callback('expected', action_id, '11')
        u = await get_user(self.uid, self.path)
        self.assertIsNone(find_attempt(u, action_id)['expected_difficulty'])
        self.assertEqual(comparison(8, None), '')

    async def test_stale_prediction_cannot_change_next_attempt(self):
        u = await get_user(self.uid, self.path)
        old_id = bot.active_attempt(u)['attempt_id']
        bot.mark_action_card_active(u)
        await save_user(u, self.path)
        await self.callback('expected', old_id, '8')
        u = await get_user(self.uid, self.path)
        self.assertIsNone(find_attempt(u, old_id)['expected_difficulty'])
        self.assertIsNone(find_attempt(u, bot.active_attempt(u)['attempt_id'])['expected_difficulty'])

    async def test_analytics_has_no_private_task_text(self):
        async with aiosqlite.connect(self.path) as db:
            rows = await (await db.execute('SELECT * FROM attempt_evidence')).fetchall()
        self.assertNotIn('PRIVATE TASK', str(rows))

    async def test_yesterdays_prediction_and_safety_keep_priority(self):
        u = await get_user(self.uid, self.path)
        action_id = bot.active_attempt(u)['attempt_id']
        find_attempt(u, action_id)['calendar_date'] = '2020-01-01'
        await save_user(u, self.path)
        await self.callback('expected', action_id, '8')
        u = await get_user(self.uid, self.path)
        self.assertIsNone(find_attempt(u, action_id)['expected_difficulty'])
        find_attempt(u, action_id)['calendar_date'] = bot.local_date_for_user(u)
        u['safety_mode'] = 'active'
        await save_user(u, self.path)
        with patch.object(bot, 'repeat_active_safety_screen', AsyncMock()) as safety:
            await self.callback('expected', action_id, '8')
            safety.assert_awaited_once()
        u = await get_user(self.uid, self.path)
        self.assertIsNone(find_attempt(u, action_id)['expected_difficulty'])

    async def test_event_identity_is_per_attempt_instead_of_per_user(self):
        u = await get_user(self.uid, self.path)
        await bot.bot_record_action_event(u, 'attempt_started')
        await bot.bot_record_action_event(u, 'attempt_started')
        bot.mark_action_card_active(u)
        await bot.bot_record_action_event(u, 'attempt_started')
        async with aiosqlite.connect(self.path) as db:
            rows = await (await db.execute("SELECT metadata FROM action_events WHERE event_type='attempt_started'")).fetchall()
        import json
        records = [json.loads(row[0]) for row in rows]
        self.assertEqual(records[0]['dedupe_key'], records[1]['dedupe_key'])
        self.assertTrue(records[1]['duplicate_product_metric'])
        self.assertNotEqual(records[0]['dedupe_key'], records[2]['dedupe_key'])
        self.assertNotIn('duplicate_product_metric', records[2])
        counts = await get_action_metrics(self.uid, self.path, day_id=u['current_day_id'])
        self.assertEqual(counts['today']['attempts_started'], 2)
