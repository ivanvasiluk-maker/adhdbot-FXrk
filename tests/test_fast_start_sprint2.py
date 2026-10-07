import json
import unittest
from unittest.mock import AsyncMock, patch
import bot
from test_conversation_intent_sprint1 import IntentJourneys
from db import get_user, save_user
from texts import preliminary_diagnosis_conclusion_text, preliminary_hypothesis_note

class FastStartJourneys(IntentJourneys):
    async def test_trainer_goes_straight_to_consent(self):
        u=await get_user(81819,self.path);u['stage']='await_trainer';await save_user(u,self.path)
        with patch.object(bot,'send_trainer_photo_if_any',AsyncMock()) as photo:
            m,u=await self.send('🐈 Марша (мягко)')
            photo.assert_not_awaited()
        self.assertEqual(u['stage'],'privacy_consent')
        self.assertFalse(u['notifications_enabled'])
        self.assertEqual(len(m.answers),1)
        self.assertIn('/privacy',m.answers[0][0])
        m,u=await self.send('✅ Согласен(на), продолжить')
        self.assertEqual(u['stage'],'await_input_mode')
        self.assertTrue(json.loads(u['profile_json'])['privacy_consent'])
        self.assertFalse(u['notifications_enabled'])
    async def test_decline_does_not_begin_analysis(self):
        u=await get_user(81819,self.path);u['stage']='privacy_consent';await save_user(u,self.path)
        with patch.object(bot,'_run_analysis',AsyncMock()) as analysis:
            m,u=await self.send('❌ Не согласен(на)');analysis.assert_not_awaited()
        self.assertEqual(u['stage'],'privacy_declined')
    async def test_old_intro_still_works(self):
        u=await get_user(81819,self.path);u['stage']='trainer_intro';await save_user(u,self.path)
        m,u=await self.send('✅ Да');self.assertEqual(u['stage'],'await_input_mode')
    async def test_understanding_switch_uses_its_recommendation(self):
        m,u=await self.send('Почему я постоянно откладываю отчёт, хотя хочу начать работу?')
        sid=json.loads(u['analysis_json'])['analysis_result']['recommended_variant']
        u['plan_json']=json.dumps(['open_only','bad_draft']);u['day_core_skill_id']='open_only';await save_user(u,self.path)
        m,u=await self.send('⚡ Давай попробуем')
        self.assertEqual(bot.current_skill_for_action(u),bot._canonical_daily_skill_id(sid))
        self.assertEqual(u['day_core_skill_id'],'open_only')
        self.assertEqual(json.loads(u['plan_json']),['open_only','bad_draft'])
        self.assertNotEqual(u['current_action_id'],'keep_action')
        self.assertIn('Один шаг сейчас',m.answers[-1][0])
        self.assertNotIn('Это похоже на',m.answers[-1][0])
    async def test_short_case_one_clarification_then_action(self):
        m,u=await self.send('Помоги мне сейчас начать отчёт.')
        self.assertEqual(u['stage'],'awaiting_barrier_choice')
        m,u=await self.send(next(iter(bot.BARRIER_BUTTONS)))
        self.assertEqual(u['stage'],'training')
        self.assertIn('Один шаг сейчас',m.answers[-1][0])
        self.assertEqual(bot.active_attempt(u)['effect_status'],'unknown')
    async def test_health_request_keeps_health_route(self):
        m,u=await self.send('Почему у меня депрессия и какие лекарства принимать?')
        self.assertIn(u['stage'],{'request_context','request_support'})
        self.assertNotIn('Один шаг сейчас',m.answers[-1][0])

class ShortCopyTests(unittest.TestCase):
    def test_short_conclusion_has_no_english_or_invented_success(self):
        text=preliminary_diagnosis_conclusion_text()
        self.assertLess(len(text),400)
        for bad in ('START','STAY','RETURN','заметил','протокол','диагноз'):
            self.assertNotIn(bad,text)
        self.assertNotIn('мало',preliminary_hypothesis_note())
