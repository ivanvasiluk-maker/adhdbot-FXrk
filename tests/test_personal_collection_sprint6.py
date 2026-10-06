import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from core.personal_skill_collection import aggregate, familiar
from core.training_track import context
from db import init_db, migrate_db, default_user, save_user, get_user, get_user_profile, update_user_profile
from test_dialogue_ux_patch import Message


def evidence(action, *, independent=False, domain='work', day='2026-10-05', completed=1, relief=None, continued=1):
    return dict(action_id=action, skill_id='open_only', independent=int(independent), context_domain=domain,
        calendar_date=day, reported_at=day, shown_at=day, completed=completed, partial=0,
        helpfulness=relief, continued=continued, source='independent_report' if independent else 'skill_card',
        mechanism='unclear_next_action', target_function='START')


class MasteryEvidenceTests(unittest.TestCase):
    def test_one_success_is_not_mastery(self):
        item = aggregate([evidence('1')])[0]
        self.assertEqual(item['status'], 'Попробовал')
        self.assertEqual(item['successful'], 1)
        self.assertEqual(item['independent'], 0)

    def test_repeated_guided_success_does_not_prove_independence(self):
        item = aggregate([evidence(str(n), day=f'2026-10-{n+1:02d}') for n in range(10)])[0]
        self.assertEqual(item['status'], 'Повторил')
        self.assertEqual(item['independent'], 0)

    def test_mastery_requires_repetition_independence_contexts_and_days(self):
        rows = [evidence('1', independent=True), evidence('2', independent=True, domain='study', day='2026-10-06')]
        self.assertEqual(aggregate(rows)[0]['status'], 'Применил в разных ситуациях')
        rows.append(evidence('3', day='2026-10-06'))
        self.assertEqual(aggregate(rows)[0]['status'], 'Освоил')
        for row in rows:
            row['calendar_date'] = '2026-10-06'
        self.assertNotEqual(aggregate(rows)[0]['status'], 'Освоил')

    def test_unknown_execution_and_relief_do_not_prove_success(self):
        item = aggregate([evidence('1', completed=None, relief='helped', continued=None)])[0]
        self.assertEqual(item['status'], 'Познакомились')
        self.assertEqual(item['successful'], 0)

    def test_familiar_requires_matching_mechanism_and_function(self):
        items = aggregate([evidence('1')])
        self.assertEqual(familiar(items, 'unclear_next_action', 'START', {'open_only'}), 'open_only')
        self.assertIsNone(familiar(items, 'attention_drift', 'START', {'open_only'}))
        self.assertIsNone(familiar(items, 'unclear_next_action', 'RETURN', {'open_only'}))
        self.assertIsNone(familiar(items, 'unclear_next_action', 'START', {'open_only'}, {'open_only'}))


class CollectionJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/db'
        self.uid = 55661
        await init_db(self.path)
        await migrate_db(self.path)
        self.p = patch.object(bot, 'DB_PATH', self.path)
        self.p.start()
        u = default_user(self.uid)
        u.update(stage='training', daily_skill_id='open_without_timer', current_skill='open_without_timer', current_day_id='55661:1',
            day_core_skill_id='open_without_timer', day_core_skill_date=bot.local_date_for_user(u),
            current_task_title='PRIVATE TASK', current_task_context='work', current_mechanism='unclear_next_action')
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

    async def callback(self, action, value, *, token=None):
        u = await get_user(self.uid, self.path)
        token = token or context(u)['collection_ui']['token']
        c = SimpleNamespace(from_user=SimpleNamespace(id=self.uid), data=f'collection:{token}:{action}:{value}',
            answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        await bot.on_collection_callback(c)
        return c

    async def begin_report(self):
        await self.send('🧰 Мои рабочие навыки')
        await self.callback('skill', '0')
        await self.callback('independent', 'start')
        await self.callback('context', 'work')

    async def test_collection_view_preserves_pending_action_and_unknown_result(self):
        before = await get_user(self.uid, self.path)
        m, after = await self.send('🧰 Мои рабочие навыки')
        self.assertEqual(after['stage'], before['stage'])
        self.assertEqual(after['current_action_id'], before['current_action_id'])
        self.assertIn('Применений: 0', m.answers[0][0])
        self.assertNotIn('PRIVATE TASK', m.answers[0][0])

    async def test_old_and_current_names_share_the_same_skill_facts(self):
        u = await get_user(self.uid, self.path)
        u['skill_attempts'][0].update(completed=True, partial=False, result='completed', continued_target_task=True)
        old = dict(u['skill_attempts'][0], action_id='old-alias-action', skill_id='open_only')
        u['skill_attempts'].append(old)
        await save_user(u, self.path)
        items = await bot.refresh_skill_collection(u)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['applications'], 2)
        self.assertEqual(items[0]['skill_id'], 'open_without_timer')
        data = await bot.build_skill_map_data(u, await get_user_profile(self.uid, self.path))
        self.assertEqual(data['by_id']['open_without_timer']['attempt_count'], 2)

    async def test_collection_does_not_count_each_event_as_an_application(self):
        await self.send('✅ Сделал')
        await self.send('🚀 Продолжил дело')
        u = await get_user(self.uid, self.path)
        for _ in range(3):
            await bot.bot_record_action_event(u, 'skill_result_reported', skill_id='open_without_timer', metadata={'completed':True, 'effect':'easier'})
        items = await bot.refresh_skill_collection(u)
        self.assertEqual(items[0]['applications'], 1)
        skill_map = await bot.build_skill_map_data(u, await get_user_profile(self.uid, self.path))
        self.assertEqual(skill_map['by_id']['open_without_timer']['attempt_count'], 1)

    async def test_independent_report_is_separate_and_duplicate_safe(self):
        before = await get_user(self.uid, self.path)
        await self.begin_report()
        u = await get_user(self.uid, self.path)
        token = context(u)['collection_ui']['token']
        await self.callback('result', 'continued', token=token)
        await self.callback('result', 'continued', token=token)
        u = await get_user(self.uid, self.path)
        self.assertEqual(u['current_action_id'], before['current_action_id'])
        self.assertEqual(bot.active_attempt(u)['attempt_status'], 'not_tried')
        p = await get_user_profile(self.uid, self.path)
        item = p['personal_skill_collection'][0]
        self.assertEqual((item['applications'], item['independent'], item['continued']), (1, 1, 1))
        self.assertEqual(item['status'], 'Применил самостоятельно')

    async def test_old_report_buttons_cannot_submit_new_report(self):
        await self.begin_report()
        u = await get_user(self.uid, self.path)
        old = context(u)['collection_ui']['token']
        await self.callback('result', 'same', token=old)
        await self.begin_report()
        await self.callback('result', 'continued', token=old)
        u = await get_user(self.uid, self.path)
        self.assertEqual((await bot.refresh_skill_collection(u))[0]['applications'], 1)

    async def test_independent_deterioration_keeps_execution_unknown_and_blocks_reuse(self):
        await self.begin_report()
        await self.callback('result', 'worse')
        u = await get_user(self.uid, self.path)
        item = (await bot.refresh_skill_collection(u))[0]
        self.assertTrue(item['latest_worse'])
        self.assertEqual(item['completed'], 0)
        self.assertEqual(item['successful'], 0)
        self.assertIsNone(u['skill_attempts'][-1]['completed'])
        await self.send('🧰 Мои рабочие навыки')
        c = await self.callback('skill', '0')
        labels = [b.text for row in c.message.answer.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertNotIn('Использовать снова', labels)

    async def test_disable_keeps_history_and_prevents_familiar_choice(self):
        await self.send('✅ Сделал')
        await self.send('🚀 Продолжил дело')
        await self.send('🧰 Мои рабочие навыки')
        await self.callback('skill', '0')
        await self.callback('toggle', 'off')
        u = await get_user(self.uid, self.path)
        p = await get_user_profile(self.uid, self.path)
        self.assertEqual(p['personal_skill_collection'][0]['applications'], 1)
        self.assertIn('open_without_timer', p['collection_disabled_skills'])
        self.assertNotEqual(bot.select_daily_skill(u, p)['skill_id'], 'open_without_timer')
        await self.callback('toggle', 'on')
        p = await get_user_profile(self.uid, self.path)
        self.assertNotIn('open_without_timer', p['collection_disabled_skills'])

    async def test_familiar_choice_has_priority_for_same_problem(self):
        await self.send('✅ Сделал')
        await self.send('🚀 Продолжил дело')
        u = await get_user(self.uid, self.path)
        p = await get_user_profile(self.uid, self.path)
        chosen = bot.select_daily_skill(u, p)
        self.assertEqual(chosen['skill_id'], 'open_without_timer')
        self.assertTrue(chosen['familiar_skill'])

    async def test_view_does_not_intercept_mandatory_feedback(self):
        await self.send('✅ Сделал')
        m, u = await self.send('🧰 Мои рабочие навыки')
        self.assertEqual(u['stage'], 'minimal_feedback_help')
        self.assertNotIn('collection_ui', context(u))

    async def test_panel_is_stale_after_new_action_or_safety(self):
        await self.send('🧰 Мои рабочие навыки')
        u = await get_user(self.uid, self.path)
        old = context(u)['collection_ui']['token']
        bot.mark_action_card_active(u)
        await save_user(u, self.path)
        c = await self.callback('skill', '0', token=old)
        c.message.answer.assert_not_awaited()
        u = await get_user(self.uid, self.path)
        u['safety_mode'] = 'active'
        await save_user(u, self.path)
        with patch.object(bot, 'repeat_active_safety_screen', AsyncMock()) as safety:
            await self.callback('skill', '0', token=old)
            safety.assert_awaited_once()

    async def test_familiar_action_keeps_daily_plan_and_respects_device_choice(self):
        await self.send('✅ Сделал')
        await self.send('🚀 Продолжил дело')
        u = await get_user(self.uid, self.path)
        original = u['day_core_skill_id']
        u['analysis_json'] = json.dumps({'selected_skill':'bad_draft','selected_barrier':'unclear_next_action', 'analysis_result':{}})
        m = Message(self.uid, '')
        await bot.start_intent_action(m, u)
        self.assertEqual(u['daily_skill_id'], 'open_without_timer')
        self.assertEqual(u['day_core_skill_id'], original)
        self.assertIn('В похожей ситуации', m.answers[0][0])
        u['analysis_json'] = json.dumps({'selected_skill':'bad_draft','selected_barrier':'unclear_next_action', 'request_device':'computer', 'analysis_result':{}})
        await bot.start_intent_action(Message(self.uid, ''), u)
        self.assertEqual(u['daily_skill_id'], 'bad_draft')
        self.assertEqual(u['day_core_skill_id'], original)

    async def test_closed_day_report_keeps_day_closed_and_history_is_read_only(self):
        u = await get_user(self.uid, self.path)
        u.update(stage='day_core_stop', day_closed=1, today_closed=1, day_status='closed', last_day_closed_at=bot.local_date_for_user(u))
        await save_user(u, self.path)
        await self.begin_report()
        await self.callback('result', 'relief')
        u = await get_user(self.uid, self.path)
        self.assertTrue(bot.day_closed_today(u))
        self.assertEqual(u['stage'], 'day_core_stop')
        await self.send('🧰 Мои рабочие навыки')
        await self.callback('skill', '0')
        c = await self.callback('history', 'show')
        self.assertIn('Самостоятельно', c.message.answer.await_args.args[0])
        self.assertNotIn('PRIVATE TASK', c.message.answer.await_args.args[0])
        u = await get_user(self.uid, self.path)
        self.assertEqual((await bot.refresh_skill_collection(u))[0]['applications'], 1)
