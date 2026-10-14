"""PATCH-69: denied mechanisms and exhausted choices never reopen rejected skills."""
import json
import unittest
from unittest.mock import AsyncMock, patch

import bot
import test_launch_fixes_20261011 as fixtures
from test_dialogue_ux_patch import Message
from db import get_user
from core.reported_mechanisms import signal_status, unsupported_skills


class SelectionTests(unittest.TestCase):
    def test_denials_do_not_enable_conditional_skills(self):
        cases = {
            'self_criticism_to_instruction': ('Самокритики нет, я себя не ругаю', 'Не самокритика, а тревога', 'Без самокритики', 'Я не чувствую стыда'),
            'restart_after_slip': ('Я не отвлекаюсь', 'Срывов нет', 'Я не переключаюсь на телефон'),
        }
        for sid, texts in cases.items():
            for text in texts:
                with self.subTest(skill=sid, text=text):
                    self.assertIs(signal_status(text, sid), False)

    def test_current_denial_overrides_stored_positive_mechanism(self):
        self.assertIn('self_criticism_to_instruction', unsupported_skills('self_attack', 'Самокритики нет'))
        u={'today_target':'Написать пост', 'bucket':'self_attack', 'dialogue_context':json.dumps({'experiment_feedback_detail':'Самокритики нет, я себя не ругаю'})}
        self.assertNotEqual(bot._first_non_repeating_skill_id(u,['self_criticism_to_instruction'],[]),'self_criticism_to_instruction')

    def test_positive_and_double_negative_reports_remain_available(self):
        for text in ('Самокритика осталась', 'Я себя ругаю', 'Не могу перестать себя ругать', 'Не ругаю себя, но самокритика осталась'):
            self.assertIs(signal_status(text,'self_criticism_to_instruction'),True)
        self.assertIs(signal_status('Не могу не отвлекаться','restart_after_slip'),True)

    def test_alias_cannot_bypass_blocked_skill(self):
        blocked=['one_tab_focus','restart_after_slip','body_first','external_start','phone_away_3_min','open_without_timer','ninety_sec_start','one_visible_step']
        result=bot._first_non_repeating_skill_id({'today_target':'Написать пост'},['self_criticism_to_instruction'],blocked)
        self.assertEqual(result,'')

    def test_exhaustion_preserves_repetition_and_mechanism_exclusions(self):
        for sid in ('self_criticism_to_instruction','restart_after_slip'):
            with patch.object(bot,'should_block_skill_for_repetition',return_value=True):
                self.assertEqual(bot._first_non_repeating_skill_id({'today_target':'Написать пост'},[sid],[]),'')

    def test_explicit_repeat_does_not_override_a_block_or_denial(self):
        result=bot._first_non_repeating_skill_id({'today_target':'Написать пост'},['self_criticism_to_instruction'],list(bot.SKILLS_DB),repeat_requested=True)
        self.assertEqual(result,'')


class ExhaustedJourney(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.LaunchJourneys.asyncSetUp
    asyncTearDown=fixtures.LaunchJourneys.asyncTearDown
    user=fixtures.LaunchJourneys.user

    async def test_no_card_or_new_attempt_when_options_are_exhausted(self):
        u=await self.user(stage='training_main',today_target='Написать пост',current_skill='open_without_timer',day_core_skill_id='',active_attempt={})
        before=dict(u)
        persisted_before=await get_user(self.uid,bot.DB_PATH)
        m=Message(self.uid)
        with patch.object(bot,'get_honest_day_counts',AsyncMock(return_value={'attempts_today':0})),patch.object(bot,'should_switch_to_consolidation',return_value=False),patch.object(bot,'daily_conclusion',return_value={}),patch.object(bot,'should_block_skill_for_repetition',return_value=True),patch.object(bot,'bot_record_action_event',AsyncMock()) as event:
            await bot.open_next_logical_step(m,u)
            event.assert_not_awaited()
        self.assertIn('Подходящего нового способа',m.answers[-1][0])
        saved=await get_user(self.uid,bot.DB_PATH)
        self.assertEqual(saved['today_target'],before['today_target'])
        self.assertEqual(u['current_skill'],before['current_skill'])
        self.assertEqual(bot.current_skill_for_action(saved),bot.current_skill_for_action(persisted_before))
        self.assertEqual(saved['stage'],before['stage'])
