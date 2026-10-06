import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import bot
from core.conversation_intent import resolve_intent
from core.session_continuity import render_pause
from core.trainer_voice import VoiceContent, render_message
from db import default_user, init_db, migrate_db, save_user, get_user
from test_dialogue_ux_patch import Message


class TrainerCopyTests(unittest.TestCase):
    def test_three_voices_keep_instruction_and_function(self):
        content = VoiceContent('skill_instruction', target_function='START', skill_name='Техническое имя', facts={'instruction': 'Откройте документ и напишите одну строку.'})
        texts = []
        for trainer in ('skinny', 'marsha', 'beck'):
            result = render_message(trainer, content)
            texts.append(result.text)
            self.assertIn(content.facts['instruction'], result.text)
            self.assertEqual(result.target_function, 'START')
            if content.skill_name in result.text:
                self.assertLess(result.text.index('Откройте'), result.text.index(content.skill_name))
        self.assertEqual(len(set(texts)), 3)

    def test_all_trainers_rotate_without_inventing_history(self):
        for trainer in ('skinny', 'marsha', 'beck'):
            u = default_user(44); u.update(trainer_key=trainer, day=30)
            first = bot.trainer_general_line_for_user(u)
            second = bot.trainer_general_line_for_user(u)
            self.assertNotEqual(first, second)
            for text in (first, second):
                self.assertNotIn('уже помог', text)
                self.assertNotIn('избегание', text)
                self.assertNotIn('стыд', text)

    def test_switch_preserves_separate_voice_counters(self):
        u = default_user(44); u['trainer_key'] = 'beck'
        first = bot.trainer_general_line_for_user(u)
        u['trainer_key'] = 'skinny'; bot.trainer_general_line_for_user(u)
        u['trainer_key'] = 'beck'
        self.assertNotEqual(first, bot.trainer_general_line_for_user(u))

    def test_day_card_purpose_and_action_precede_technique(self):
        skill = {'name': 'Техническое имя', 'why_short': 'Уменьшим размер первого шага.', 'steps': ['Откройте документ.'], 'minimum': 'Одна строка.'}
        text = bot.new_day_skill_card_text(skill, default_user(44))
        self.assertLess(text.index('Что будем делать'), text.index('Навык дня'))
        self.assertLess(text.index('Сделай:'), text.index('Навык дня'))

    def test_current_card_purpose_precedes_technique(self):
        text = bot.build_current_skill_text({'name': 'Техническое имя', 'why_short': 'Уменьшим шаг.', 'steps': ['Откройте документ.']})
        self.assertLess(text.index('Сделай:'), text.index('Техническое имя'))

    def test_familiar_tone_requires_selected_skill_evidence(self):
        for trainer in ('skinny', 'marsha', 'beck'):
            u = default_user(44); u.update(trainer_key=trainer, day=30)
            skill = {'name': 'Способ', 'steps': ['Откройте документ.']}
            without = bot.build_new_day_intro(u, skill, include_context=False)
            self.assertNotIn('уже помог', without)
            self.assertNotIn('отметили пользу', without)
            with_evidence = bot.build_new_day_intro(u, dict(skill, familiar_skill=True), include_context=False)
            self.assertTrue('уже помог' in with_evidence or 'отметили пользу' in with_evidence)

    def test_pause_does_not_claim_execution_or_effect(self):
        texts = [render_pause(t) for t in ('skinny', 'marsha', 'beck')]
        self.assertEqual(len(set(texts)), 3)
        for text in texts:
            self.assertNotIn('получилось', text)
            self.assertNotIn('помогло', text)
            self.assertNotIn('?', text)
            self.assertIn('до конца дня', text)

    def test_gratitude_is_navigation_only(self):
        self.assertEqual(resolve_intent('Спасибо!', 'training_main'), 'ACKNOWLEDGE')
        for stage in ('ask_name', 'request_task', 'intent_act_context', 'chain_edit_text'):
            self.assertNotEqual(resolve_intent('спасибо', stage), 'ACKNOWLEDGE')

    def test_feedback_owns_courtesy(self):
        self.assertEqual(resolve_intent('спасибо', 'minimal_feedback_help'), 'FEEDBACK_REPLY')
        self.assertEqual(resolve_intent('спасибо', 'training_main', {'state': 'EXPERIMENT_FEEDBACK'}), 'FEEDBACK_REPLY')

    def test_paused_navigation_outranks_frozen_feedback(self):
        session = {'state': 'EXPERIMENT_FEEDBACK'}
        self.assertEqual(resolve_intent('Продолжить', 'intent_paused', session), 'CONTINUE')
        self.assertEqual(resolve_intent('спасибо', 'intent_paused', session), 'ACKNOWLEDGE')
        self.assertEqual(resolve_intent('стоп', 'intent_paused', session), 'STOP')
        self.assertEqual(resolve_intent('спасибо', 'minimal_feedback_help', session), 'FEEDBACK_REPLY')

    def test_intake_owns_courtesy(self):
        self.assertEqual(resolve_intent('понятно', 'training_main', {'awaiting_target': True}), 'PENDING_REPLY')
        self.assertEqual(resolve_intent('спасибо', 'offer_request_form'), 'APPLICATION_DATA')

    def test_farewell_stops_navigation_without_result(self):
        for text in ('пока', 'до свидания', 'на сегодня всё', 'спасибо, на сегодня всё'):
            self.assertEqual(resolve_intent(text, 'training_main'), 'STOP')

    def test_courtesy_with_new_information_is_not_swallowed(self):
        for text in ('спасибо, стало хуже', 'понятно, но не помогло', 'спасибо, я начал отчёт'):
            self.assertEqual(resolve_intent(text, 'training_main'), 'OTHER')


class ConversationJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = self.tmp.name + '/db'
        await init_db(self.path); await migrate_db(self.path)
        self.patcher = patch.object(bot, 'DB_PATH', self.path); self.patcher.start()
        u = default_user(81819)
        u.update(stage='training_main', name='Иван', username='ivan_test', current_action_id='keep_action')
        u['dialogue_context'] = json.dumps({'text': 'Прежний экран', 'stage': 'training_main'})
        await save_user(u, self.path)

    async def asyncTearDown(self):
        self.patcher.stop(); self.tmp.cleanup()

    async def send(self, text):
        m = Message(uid=81819, text=text); m.from_user.username = 'ivan_test'
        with patch.object(bot, 'client', None):
            await bot.main_flow(m)
        return m, await get_user(81819, self.path)

    async def test_thanks_preserves_screen_action_and_plan(self):
        before = await get_user(81819, self.path)
        with patch.object(bot, 'run_analysis', AsyncMock()) as analysis, patch.object(bot, 'handle_action_request', AsyncMock()) as action:
            m, after = await self.send('спасибо')
            analysis.assert_not_awaited(); action.assert_not_awaited()
        for field in ('stage', 'dialogue_context', 'current_action_id', 'pending_feedback_json', 'plan_json'):
            self.assertEqual(after.get(field), before.get(field), field)
        self.assertNotIn('?', m.answers[-1][0])

    async def test_goodbye_saves_resume_without_claiming_success(self):
        m, u = await self.send('до свидания')
        self.assertEqual(u['stage'], 'intent_paused')
        self.assertEqual(u['current_action_id'], 'keep_action')
        self.assertNotIn('?', m.answers[-1][0])
        ctx = json.loads(u['dialogue_context'])
        self.assertEqual(ctx['intent_resume_date'], bot.local_date_for_user(u))

    async def test_thanks_while_paused_keeps_resume_screen(self):
        await self.send('стоп')
        before = await get_user(81819, self.path)
        _, after = await self.send('спасибо')
        self.assertEqual(after['dialogue_context'], before['dialogue_context'])
        m, resumed = await self.send('Продолжить')
        self.assertEqual(resumed['stage'], 'training_main')
        self.assertEqual(m.answers[-1][0], 'Прежний экран')

    async def test_stale_snapshot_never_restores_old_action_or_feedback(self):
        await self.send('стоп')
        u = await get_user(81819, self.path)
        ctx = json.loads(u['dialogue_context']); ctx['intent_resume_date'] = '2000-01-01'
        ctx['intent_resume'].update(current_action_id='yesterday', pending_feedback_json='{"completed":true}')
        u['dialogue_context'] = json.dumps(ctx); u['current_action_id'] = 'today'; u['pending_feedback_json'] = None
        await save_user(u, self.path)
        with patch.object(bot, 'resume_daily_flow', AsyncMock()) as resume:
            _, after = await self.send('Продолжить'); resume.assert_awaited_once()
        self.assertEqual(after['current_action_id'], 'today')
        self.assertFalse(after['pending_feedback_json'])
        self.assertNotIn('intent_resume', json.loads(after['dialogue_context']))

    async def test_legacy_undated_snapshot_is_not_restored(self):
        await self.send('стоп')
        u = await get_user(81819, self.path); ctx = json.loads(u['dialogue_context'])
        ctx.pop('intent_resume_date'); ctx['intent_resume']['current_action_id'] = 'unknown_old'
        u['dialogue_context'] = json.dumps(ctx); await save_user(u, self.path)
        with patch.object(bot, 'resume_daily_flow', AsyncMock()):
            _, after = await self.send('Продолжить')
        self.assertNotEqual(after['current_action_id'], 'unknown_old')

    async def test_feedback_thanks_is_not_execution(self):
        u = await get_user(81819, self.path); u['stage'] = 'minimal_feedback_help'; await save_user(u, self.path)
        _, after = await self.send('спасибо')
        self.assertEqual(after['stage'], 'minimal_feedback_help')
        self.assertEqual(after['current_action_id'], 'keep_action')
