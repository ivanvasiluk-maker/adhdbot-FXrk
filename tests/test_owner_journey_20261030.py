"""PATCH-85: regressions from the owner's 30 October launch acceptance."""
import json
import unittest
from unittest.mock import AsyncMock, patch
import bot
from core.addressing import render_address
from core.dialogue_ux import plain_text
from core.experiment_dialogue import reported_chain, wants_larger_step
from db import get_user, update_user_profile
from test_dialogue_ux_patch import Message
import test_launch_fixes_20261011 as fixtures

CASE = ('Я уже неделю откладываю рабочую презентацию. Завтра нужно сдавать, '
        'а у меня даже первый слайд не готов. Каждый раз открываю ноутбук, '
        'думаю, что надо сделать всё идеально, начинаю тревожиться и ухожу в телефон. '
        'Потом ненавижу себя за это. Сейчас уже вечер, я устала, мне стыдно, '
        'и кажется, что я опять всё провалила. Не понимаю, как заставить себя начать.')
OBJECTION = 'Мне кажется что я могу больше а надо делать эти мелочи'

class Journey(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LaunchJourneys.asyncSetUp
    asyncTearDown = fixtures.LaunchJourneys.asyncTearDown
    user = fixtures.LaunchJourneys.user

    def test_task_is_the_postponed_presentation_not_the_deadline_clause(self):
        title = bot.extract_task_context_from_text(CASE)['current_task_name']
        self.assertEqual(title, 'подготовить рабочую презентацию')
        self.assertNotIn('сдавать', title)
        self.assertEqual(bot.extract_task_context_from_text(CASE)['current_deadline'], 'завтра')

    def test_chain_links_are_separate_reported_clauses(self):
        chain = reported_chain(CASE)
        self.assertEqual(chain['situation'], 'Каждый раз открываю ноутбук')
        self.assertEqual(chain['thoughts'], 'что надо сделать всё идеально')
        self.assertEqual(chain['behavior'], 'ухожу в телефон')
        self.assertEqual(chain['delayed'], 'Потом ненавижу себя за это')
        self.assertIsNone(chain['immediate'])
        self.assertNotIn('delayed', chain['missing'])

    def test_crisis_card_has_neutral_self_support_and_correct_noun_agreement(self):
        raw = bot.combined_crisis_tool_text(['task_entry_block','low_energy','self_attack'])
        for form in ('female', 'male', 'neutral'):
            text = render_address(raw, {'address_mode':'ty','address_form':form})
            self.assertNotIn('Я застрял', text)
            self.assertNotIn('не обязан', text)
            self.assertNotIn('один короткое', text.lower())
            self.assertIn('одно короткое действие', text.lower())
        quoted = 'Ты написала: «Один микрошаг, я застрял». '
        self.assertIn('«Один микрошаг, я застрял»', render_address(quoted, {'address_mode':'ty','address_form':'female'}))

    def test_larger_scope_is_not_confused_with_exhaustion(self):
        self.assertTrue(wants_larger_step(OBJECTION))
        for text in ('Я не могу больше', 'Не хочу больше, нет сил', 'Я могу больше не успеть', 'Хочу больше поддержки'):
            self.assertFalse(wants_larger_step(text))

    async def test_objection_changes_format_without_ai_or_a_new_diagnosis(self):
        await self.user(stage='misunderstood_problem_await', address_mode='ty', address_form='female',
                        current_task_title='подготовить рабочую презентацию', today_target='подготовить рабочую презентацию',
                        current_skill='bad_first_step', daily_skill_id='bad_first_step',
                        analysis_json=json.dumps({'hypothesis_rejected':True}), has_started_training=1)
        with patch.object(bot, 'rebuild_analysis_lightweight', AsyncMock()) as rebuild, \
             patch.object(bot, 'day_closed_today', return_value=False):
            m = Message(self.uid, OBJECTION)
            await bot.main_flow(m)
            u = await get_user(self.uid, self.path)
            self.assertEqual(u['stage'], 'training_format')
            self.assertTrue(json.loads(u['analysis_json'])['hypothesis_rejected'])
            self.assertIn('заметный результат', '\n'.join(t for t, _ in m.answers))
            rebuild.assert_not_awaited()
            m = Message(self.uid, '🏁 Кусочек настоящего дела')
            await bot.main_flow(m)
            result = '\n'.join(t for t, _ in m.answers)
            self.assertIn('законченный фрагмент', result)
            self.assertIn('рабочую презентацию', result)
            self.assertNotIn('Написать одно слово', result)
            self.assertEqual((await get_user(self.uid,self.path))['stage'], 'training')

    async def test_scope_request_cannot_bypass_consent_or_reopen_closed_day(self):
        u = await self.user(stage='privacy_consent', current_skill='bad_first_step')
        self.assertFalse(await bot.handle_practice_scope_request(Message(self.uid), u, OBJECTION))
        u = await self.user(stage='day_core_stop', current_skill='bad_first_step')
        with patch.object(bot,'day_closed_today',return_value=True):
            self.assertTrue(await bot.handle_practice_scope_request(Message(self.uid), u, OBJECTION))
        self.assertEqual(u['stage'], 'day_core_stop')
        u = await self.user(stage='training_main', current_skill='bad_first_step', daily_skill_id='bad_first_step')
        await update_user_profile(self.uid, {'collection_disabled_skills':['bad_first_step']}, self.path)
        with patch.object(bot,'day_closed_today',return_value=False):
            self.assertFalse(await bot.handle_practice_scope_request(Message(self.uid), u, OBJECTION))
