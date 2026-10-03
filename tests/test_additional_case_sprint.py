"""Journeys after completing the daily core, with a DB reload per message."""
import json
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

import bot
import flows
from db import default_user, init_db, migrate_db, save_user, get_user, get_user_profile, update_user_profile
from core.case_sessions import begin_case
from test_dialogue_ux_patch import Message


class AdditionalCaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/test.db'
        await init_db(self.path)
        await migrate_db(self.path)
        self.u = default_user(77442)
        self.u.update(stage='day_core_stop', day=3, day_number=3, current_day=3,
                      day_closed=1, today_closed=1, daily_training_completed=1,
                      last_day_closed_at=bot.local_date_for_user(self.u),
                      plan_json=json.dumps(['open_only']),
                      analysis_json=json.dumps({'specific_pattern': 'старая самокритика', 'selected_skill': 'body_first'}))
        await save_user(self.u, self.path)
        await update_user_profile(77442, {'primary_hypothesis': 'old_hypothesis'}, self.path)
        self.patcher = patch.object(bot, 'DB_PATH', self.path)
        self.patcher.start()

    async def asyncTearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    async def send(self, text):
        message = Message(uid=77442, text=text)
        with patch.object(bot, 'client', None):
            await bot.main_flow(message)
        return message, await get_user(77442, self.path)

    async def test_complete_optional_journey_keeps_daily_core_and_uses_new_case(self):
        await self.send('🎯 Разобрать ещё одну ситуацию')
        _, saved = await self.send('Не могу начать')
        self.assertEqual(saved['stage'], 'awaiting_barrier_choice')
        self.assertEqual(saved['closed_day_additional_active'], 1)
        case_id = json.loads(saved['current_case_json'])['case_id']
        history = json.loads(saved['case_history_json'])
        self.assertEqual(history[-1]['analysis']['specific_pattern'], 'старая самокритика')
        self.assertEqual(history[-1]['historical_hypothesis'], 'old_hypothesis')
        # A request to act cannot answer the pending question on the user's behalf.
        _, waiting = await self.send('💪 Давай действие')
        self.assertEqual(waiting['stage'], 'awaiting_barrier_choice')
        self.assertEqual(json.loads(waiting['current_case_json'])['case_id'], case_id)
        _, saved = await self.send('😬 Страх ошибки')
        self.assertEqual(saved['stage'], 'confirm_analysis')
        self.assertNotIn('старая самокритика', saved['analysis_json'])
        self.assertEqual(json.loads(saved['current_case_json'])['case_id'], case_id)
        details, saved = await self.send('📚 Подробнее')
        self.assertNotIn('Основная тренировка на сегодня закончена', details.answers[-1][0])
        self.assertNotIn('старая самокритика', details.answers[-1][0])
        self.assertEqual(saved['stage'], 'confirm_analysis')
        _, saved = await self.send('💪 Давай действие')
        self.assertEqual(saved['stage'], 'closed_day_voluntary_step')
        self.assertEqual(saved['daily_skill_id'], json.loads(saved['analysis_json'])['selected_skill'])
        _, saved = await self.send('✅ Получилось сделать')
        self.assertEqual(saved['stage'], 'day_core_stop')
        self.assertEqual(saved['closed_day_additional_active'], 0)
        self.assertTrue(json.loads(saved['current_case_json'])['completed'])
        self.assertEqual(saved['daily_training_completed'], 1)
        self.assertTrue(bot.day_closed_today(saved))
        self.assertEqual(saved['day'], 3)
        self.assertEqual(saved['plan_json'], self.u['plan_json'])
        # The next situation gets its own id, and does not inherit the last hypothesis.
        await self.send('🎯 Разобрать ещё одну ситуацию')
        _, next_case = await self.send('Не могу начать')
        self.assertNotEqual(json.loads(next_case['current_case_json'])['case_id'], case_id)
        self.assertIsNone(json.loads(next_case['current_case_json'])['current_case_hypothesis'])

    async def test_answer_to_clarification_does_not_start_another_case(self):
        _, saved = await self.send('Не могу начать')
        case_id = json.loads(saved['current_case_json'])['case_id']
        saved.update(stage='analysis_clarify_questions', pending_feedback_json=json.dumps({
            'type': 'analysis_clarify', 'kind': 'fear', 'index': 0, 'answers': []}))
        await save_user(saved, self.path)
        with patch.object(bot, 'run_analysis', AsyncMock()) as analyze:
            _, saved = await self.send('Боюсь, что работу оценят плохо')
            analyze.assert_not_awaited()
        self.assertEqual(json.loads(saved['current_case_json'])['case_id'], case_id)
        self.assertIn('Боюсь', saved['pending_feedback_json'])

    async def test_details_keep_pending_answer_and_work_after_completion(self):
        for stage in ('day_core_stop', 'closed_day_voluntary_step', 'awaiting_barrier_choice', 'analysis_clarify_questions'):
            saved = await get_user(77442, self.path)
            saved.update(stage=stage, pending_feedback_json='{"pending": true}')
            await save_user(saved, self.path)
            message, after = await self.send('📚 Подробнее')
            self.assertTrue(message.answers)
            self.assertEqual(after['stage'], stage)
            self.assertEqual(after['pending_feedback_json'], saved['pending_feedback_json'])

    async def test_full_analysis_does_not_reset_day_or_plan(self):
        u = await get_user(77442, self.path)
        begin_case(u)
        await save_user(u, self.path)
        result = {'evidence_signals': ['описание задачи', 'страх ошибки', 'избегание'],
                  'recommended_variant': 'open_only', 'first_check': 'открыть файл'}
        with patch.object(flows, 'analysis_needs_more_input', return_value=False), \
             patch.object(flows, 'build_analysis_result', return_value=result), \
             patch.object(flows, 'ai_analyze', AsyncMock(return_value={'bucket': 'anxiety'})), \
             patch.object(flows, 'ai_analyze_comprehensive', AsyncMock(return_value={
                 'bucket': 'anxiety', 'specific_pattern': 'страх ошибки', 'selected_skill': 'open_only'})):
            await flows.run_analysis(Message(uid=77442), u, 'Страшно открыть файл и ошибиться', self.path)
        saved = await get_user(77442, self.path)
        self.assertEqual(saved['day'], 3)
        self.assertEqual(saved['plan_json'], self.u['plan_json'])
        self.assertEqual(saved['daily_training_completed'], 1)
        self.assertEqual(saved['closed_day_additional_active'], 1)

    async def test_migration_is_additive_and_repeatable(self):
        await migrate_db(self.path)
        saved = await get_user(77442, self.path)
        self.assertEqual(saved['daily_training_completed'], 1)
        self.assertEqual(saved['analysis_json'], self.u['analysis_json'])
        self.assertEqual(saved['closed_day_additional_active'], 0)


if __name__ == '__main__':
    unittest.main()
