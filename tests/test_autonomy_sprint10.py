import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import bot
import flows
from core.autonomy import return_recap, ability_progress, small_challenge
from core.training_track import context
from db import default_user, init_db, migrate_db, save_user, get_user, get_user_profile
from test_dialogue_ux_patch import Message

TODAY = '2026-10-06'
LABEL = lambda sid: 'Знакомый способ'

def row(a='1', **kwargs):
    data = dict(action_id=a, skill_id='open_only', calendar_date='2026-10-05', reported_at='2026-10-05',
                completed=1, partial=0, helpfulness=None, continued=1, independent=1,
                target_function='START', context_domain='work', source='independent_report')
    data.update(kwargs)
    return data

class AbilityTests(unittest.TestCase):
    def test_first_independent_start_is_a_fact_not_mastery(self):
        text = ability_progress([row()], TODAY, LABEL)
        self.assertIn('1 раз', text); self.assertNotIn('осво', text); self.assertNotIn('раньше', text)
    def test_three_distinct_starts(self):
        text = ability_progress([row(str(n)) for n in range(3)], TODAY, LABEL)
        self.assertIn('3 раз', text)
    def test_duplicates_are_one_application(self):
        self.assertIn('1 раз', ability_progress([row(), row()], TODAY, LABEL))
    def test_guided_does_not_prove_independence(self):
        self.assertEqual(ability_progress([row(independent=0)], TODAY, LABEL), '')
    def test_relief_does_not_prove_start(self):
        self.assertEqual(ability_progress([row(continued=None, helpfulness='helped')], TODAY, LABEL), '')
    def test_unknown_execution_does_not_prove_start(self):
        self.assertEqual(ability_progress([row(completed=None)], TODAY, LABEL), '')
    def test_other_function_does_not_prove_start(self):
        self.assertEqual(ability_progress([row(target_function='RETURN')], TODAY, LABEL), '')
    def test_transfer_needs_same_skill_and_known_successful_contexts(self):
        self.assertIn('разных ситуациях', ability_progress([row(), row('2', context_domain='home')], TODAY, LABEL))
        self.assertNotIn('разных ситуациях', ability_progress([row(), row('2', context_domain='other')], TODAY, LABEL))
        self.assertNotIn('разных ситуациях', ability_progress([row(), row('2', skill_id='other', context_domain='home')], TODAY, LABEL))
    def test_failed_transfer_and_worse_not_rewarded(self):
        self.assertNotIn('разных ситуациях', ability_progress([row(), row('2', completed=0, context_domain='home')], TODAY, LABEL))
        self.assertEqual(ability_progress([row(helpfulness='worse')], TODAY, LABEL), '')
    def test_future_and_unknown_dates_excluded(self):
        self.assertEqual(ability_progress([row(calendar_date='2099-01-01'), row('2', calendar_date='')], TODAY, LABEL), '')
    def test_recap_separates_relief_from_continuation(self):
        text = return_recap([row(continued=None, helpfulness='helped')], TODAY, LABEL)
        self.assertIn('стало легче', text); self.assertIn('не подтверждено', text)
    def test_recap_never_calls_old_history_yesterday(self):
        text = return_recap([row(calendar_date='2025-01-01')], TODAY, LABEL)
        self.assertNotIn('Вчера', text); self.assertIn('отрабатывать не нужно', text)
    def test_views_do_not_become_recap(self):
        text = return_recap([row(completed=None, source='skill_card', continued=None)], TODAY, LABEL)
        self.assertNotIn('дело продолжилось', text)
    def test_worse_recap_blocks_repetition(self):
        self.assertIn('не повторяем', return_recap([row(helpfulness='worse')], TODAY, LABEL))
    def test_challenge_optional_and_requires_useful_history(self):
        self.assertIn('Отчёт не обязателен', small_challenge([row()], TODAY, 'open_only', LABEL))
        self.assertEqual(small_challenge([row(completed=None, continued=None)], TODAY, 'open_only', LABEL), '')
        self.assertEqual(small_challenge([row()], TODAY, 'open_only', LABEL, ['open_only']), '')
    def test_latest_negative_suppresses_challenge(self):
        for changes in ({'helpfulness':'worse'}, {'completed':0}, {'helpfulness':'not_helped', 'continued':None}):
            self.assertEqual(small_challenge([row(), row('2', calendar_date=TODAY, **changes)], TODAY, 'open_only', LABEL), '')

class AutonomyJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = self.tmp.name + '/db'; self.uid = 99188
        await init_db(self.path); await migrate_db(self.path)
        self.p = patch.object(bot, 'DB_PATH', self.path); self.p.start()
        u = default_user(self.uid)
        u.update(name='Иван', username='ivan_test', stage='training', day=1, day_date=bot.local_date_for_user(u),
                 current_skill='open_without_timer', daily_skill_id='open_without_timer',
                 day_core_skill_id='open_without_timer', day_core_skill_date=bot.local_date_for_user(u),
                 current_task_title='Написать отчёт', current_task_context='work')
        bot.mark_action_card_active(u)
        await save_user(u, self.path)
    async def asyncTearDown(self):
        self.p.stop(); self.tmp.cleanup()
    async def send(self, text):
        m = Message(self.uid, text); m.from_user.username = 'ivan_test'
        with patch.object(bot, 'client', None): await bot.main_flow(m)
        return m, await get_user(self.uid, self.path)
    async def callback(self, action, value, token=None):
        u = await get_user(self.uid, self.path)
        token = token or context(u)['collection_ui']['token']
        c = SimpleNamespace(from_user=SimpleNamespace(id=self.uid), data=f'collection:{token}:{action}:{value}',
                            answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        await bot.on_collection_callback(c); return c
    async def test_close_two_answers_without_inventing_action_result(self):
        _, before = await self.send('🌙 Закрыть день')
        self.assertEqual(before['stage'], 'day_review_barrier')
        _, middle = await self.send('Перегруз'); self.assertEqual(middle['stage'], 'day_review_state')
        _, after = await self.send('🙂 Нормально'); self.assertTrue(bot.day_closed_today(after))
        profile = await get_user_profile(self.uid, self.path)
        self.assertEqual(profile['last_day_review']['function'], '')
        self.assertEqual(profile['last_day_review']['state'], 'нормально')
        self.assertIsNone(after['skill_attempts'][0].get('completed'))
    async def test_other_does_not_add_third_question(self):
        await self.send('🌙 Закрыть день')
        _, u = await self.send('Другое / не знаю'); self.assertEqual(u['stage'], 'day_review_state')
        _, u = await self.send('😐 Устал'); self.assertTrue(bot.day_closed_today(u))
    async def test_close_skip_is_optional(self):
        await self.send('🌙 Закрыть день')
        _, u = await self.send('🌙 Закрыть без разбора'); self.assertTrue(bot.day_closed_today(u))
    async def test_restart_keeps_pending_question(self):
        await self.send('🌙 Закрыть день'); await self.send('Перегруз')
        u = await get_user(self.uid, self.path); before = dict(u)
        m = Message(self.uid, '/start'); await bot.show_existing_user_start_menu(m, u)
        self.assertEqual(u['stage'], before['stage']); self.assertIn('Как', m.answers[-1][0])
        self.assertEqual(u['pending_feedback_json'], before['pending_feedback_json'])
    async def test_stale_review_never_closes_current_day(self):
        await self.send('🌙 Закрыть день')
        u = await get_user(self.uid, self.path); pending = bot._day_review_data(u)
        pending['calendar_date'] = '2000-01-01'; u['pending_feedback_json'] = pending
        await save_user(u, self.path)
        _, after = await self.send('Перегруз')
        self.assertEqual(after['stage'], 'waiting_next_day'); self.assertFalse(bot.day_closed_today(after))
    async def test_challenge_preserves_attempt_and_closed_day(self):
        u = await get_user(self.uid, self.path)
        u['skill_attempts'][0].update(completed=True, partial=False, result='completed', continued_target_task=True)
        await save_user(u, self.path)
        await bot.mark_day_closed(u, 'test'); bot.set_legacy_stage(u, 'day_core_stop'); await save_user(u, self.path)
        await self.send('🧰 Мои рабочие навыки'); await self.callback('skill', '0')
        before = await get_user(self.uid, self.path)
        c = await self.callback('challenge', 'show'); after = await get_user(self.uid, self.path)
        self.assertIn('Необязательная', c.message.answer.await_args.args[0])
        for key in ('stage', 'current_action_id', 'skill_attempts', 'day_closed', 'plan_json'):
            self.assertEqual(after.get(key), before.get(key), key)
    async def test_stale_challenge_callback_does_nothing(self):
        await self.send('🧰 Мои рабочие навыки'); await self.callback('skill', '0')
        c = await self.callback('challenge', 'show', token='expired')
        c.message.answer.assert_not_awaited()
    async def test_start_legacy_does_not_reward_opening(self):
        u = await get_user(self.uid, self.path); before = {k:u.get(k) for k in ('points', 'streak', 'level', 'return_count')}
        m = Message(self.uid)
        await flows.start_day(m, u, 2, self.path)
        for key, value in before.items(): self.assertEqual(u.get(key), value, key)
    async def test_day_two_has_single_preview_no_extra_prediction_question(self):
        u = await get_user(self.uid, self.path); u['day_core_skill_date'] = None
        m = Message(self.uid)
        with patch.object(bot, 'select_daily_skill', return_value=dict(bot.SKILLS_DB['open_without_timer'], skill_id='open_without_timer')):
            await bot.open_new_day_skill(m, u, 2, 'test_return')
        self.assertEqual(len(m.answers), 1)
        self.assertIn('отрабатывать не нужно', m.answers[0][0]); self.assertIn('Начать тренировку', m.answers[0][0])
        self.assertNotIn('Сделай:', m.answers[0][0]); self.assertNotIn('Это повторилось?', m.answers[0][0])

    async def test_start_after_day_two_preview_keeps_selected_skill(self):
        u = await get_user(self.uid, self.path); u['day_core_skill_date'] = None
        m = Message(self.uid)
        with patch.object(bot, 'select_daily_skill', return_value=dict(bot.SKILLS_DB['open_without_timer'], skill_id='open_without_timer')):
            await bot.open_new_day_skill(m, u, 2, 'test_return')
        before_count = len(u['skill_attempts'])
        await bot.send_current_skill(self.uid, m, u)
        after = await get_user(self.uid, self.path)
        self.assertEqual(after['daily_skill_id'], 'open_without_timer')
        self.assertEqual(len(after['skill_attempts']), before_count + 1)
        action = after['current_action_id']
        await bot.send_current_skill(self.uid, m, after)
        reloaded = await get_user(self.uid, self.path)
        self.assertEqual(reloaded['current_action_id'], action)
        self.assertEqual(len(reloaded['skill_attempts']), len(after['skill_attempts']))
