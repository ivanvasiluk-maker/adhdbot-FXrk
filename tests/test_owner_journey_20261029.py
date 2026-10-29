"""PATCH-83: observed stop/exhaustion loop and negative resource evidence."""
import json
import unittest
from unittest.mock import patch, AsyncMock
import bot
from core.case_plan import case_plan
from core.experiment_dialogue import reported_chain
from db import get_user
from test_dialogue_ux_patch import Message
import test_launch_fixes_20261011 as fixtures


class Journey(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LaunchJourneys.asyncSetUp
    asyncTearDown = fixtures.LaunchJourneys.asyncTearDown
    user = fixtures.LaunchJourneys.user

    def test_negative_evening_result_is_not_a_resource(self):
        for text in ('Сейчас уже вечер, и я опять почти ничего не сделал.',
                     'Вечером не успеваю закончить работу.'):
            self.assertIsNone(reported_chain(text)['resources'])
        self.assertIn('сделал', reported_chain('Вечером сделал презентацию.')['resources'])

    def test_short_answer_relief_is_evidence_not_a_guessed_fact(self):
        self.assertTrue(case_plan({'behavioral_chain': {'immediate': 'легче'}})['relief_reported'])
        self.assertFalse(case_plan({'behavioral_chain': {'immediate': 'не легче'}})['relief_reported'])

    async def test_stop_clears_optional_interview_and_next_action_is_available(self):
        for stage in ('reported_case_question', 'analysis_clarify_done'):
            await self.user(stage=stage, name='Иван', closed_day_additional_active=1,
                            analysis_json=json.dumps({'case_question':'immediate','case_mode':'detailed'}))
            with patch.object(bot, 'day_closed_today', return_value=True):
                await bot.main_flow(Message(self.uid, 'На этом пока остановиться'))
                saved = await get_user(self.uid, self.path)
                self.assertEqual(saved['stage'], 'day_core_stop')
                self.assertFalse(saved['closed_day_additional_active'])
                self.assertNotIn('case_question', json.loads(saved['analysis_json']))
                with patch.object(bot, 'open_closed_day_voluntary_step', AsyncMock()) as action:
                    m = Message(self.uid, '⚡ Дать короткий навык')
                    await bot.main_flow(m)
                    action.assert_awaited_once()
                    self.assertNotIn('Сначала ответ', '\n'.join(t for t, _ in m.answers))

    async def test_exhausted_candidates_leave_no_phantom_question_lock(self):
        u = await self.user(stage='reported_case_question', name='Иван',
                            closed_day_additional_active=1, today_target='написать письмо')
        comp = {'case_question':'immediate', 'behavioral_chain':{'task':'написать письмо'}}
        with patch.object(bot, 'should_block_skill_for_repetition', return_value=True), \
             patch.object(bot, 'day_closed_today', return_value=True):
            m = Message(self.uid)
            await bot.complete_reported_case(m, u, self.path, comp)
            self.assertFalse(u['closed_day_additional_active'])
            self.assertNotIn('case_question', json.loads(u['analysis_json']))
            self.assertEqual(u['stage'], 'day_core_stop')
            self.assertFalse(u.get('pending_skill_id'))
            self.assertIn('Разбор сохранён', m.answers[-1][0])

    async def test_old_stop_button_cannot_skip_privacy_consent(self):
        await self.user(stage='privacy_consent', name='Иван')
        await bot.main_flow(Message(self.uid, 'На этом пока остановиться'))
        saved = await get_user(self.uid, self.path)
        self.assertEqual(saved['stage'], 'privacy_consent')
