"""Regression journeys from the September 27 feedback (no network/Telegram)."""
import copy
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from db import init_db, migrate_db, get_user, save_user, default_user
from core.dialogue_ux import (
    valid_transcript, is_clarification, concrete_step, review_focus,
    plain_text, VOICE_FAILURE, VOICE_SKIP, OTHER, BUTTON_LABELS,
)
from core.skiller_router import new_session, DialogState, route_user_input, route_callback
from core.personal_working_model import render_working_model
from texts import contextualize_task_text


class Message:
    def __init__(self, uid=38801, text='', voice=None):
        self.from_user = SimpleNamespace(id=uid)
        self.chat = SimpleNamespace(id=uid)
        self.text, self.voice = text, voice
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return SimpleNamespace(message_id=len(self.answers))


class PureDialogueTests(unittest.TestCase):
    def test_noise_does_not_become_case_data(self):
        for value in ('', '   ', '🙂', '!!!', 'ъ', 'йц', 'ммм', '…'):
            with self.subTest(value=value):
                self.assertFalse(valid_transcript(value))
                s = new_session()
                before = copy.deepcopy(s)
                result = route_user_input(s, value, kind='voice')
                self.assertEqual(s, before)
                self.assertIn('не распозналось', result['text'])

    def test_negated_progress_is_not_recorded_as_success(self):
        for value in ('не продолжил', 'не закончила', 'не стало хуже'):
            s = new_session(DialogState.EXPERIMENT_ACTIVE)
            before = copy.deepcopy(s)
            route_user_input(s, value)
            self.assertEqual(s, before)

    def test_numeric_low_confidence_is_rejected(self):
        self.assertFalse(valid_transcript('10', [{'no_speech_prob': .99}]))

    def test_voice_transcript_is_reused(self):
        import asyncio
        message = SimpleNamespace(_voice_transcript='убрать кухню')
        self.assertEqual(asyncio.run(bot.whisper_transcribe(message)), 'убрать кухню')

    def test_short_meaningful_answers_remain_valid(self):
        for value in ('да', 'нет', 'я', '0', '10', 'не знаю', 'хочу убрать кухню'):
            self.assertTrue(valid_transcript(value), value)

    def test_available_confidence_is_used(self):
        self.assertFalse(valid_transcript('спасибо за просмотр', [{'no_speech_prob': .95}]))
        self.assertFalse(valid_transcript('несвязный ответ', [{'avg_logprob': -2.1}]))
        self.assertTrue(valid_transcript('мне трудно начать', [{'no_speech_prob': .05, 'avg_logprob': -.2}]))

    def test_clarification_is_not_rejection_or_story(self):
        for text in ('Не понял', 'Я не понял', 'Что это значит?', 'Как именно это сделать?', 'Приведи пример', 'Что такое фокус?'):
            self.assertTrue(is_clarification(text), text)
        for text in ('Вы меня не поняли', 'Ты меня не понял', 'Не могу начать отчёт', 'Муж сказал: что это значит?'):
            self.assertFalse(is_clarification(text), text)

    def test_clarification_preserves_router_state_and_answers(self):
        s = new_session()
        route_user_input(s, 'Уже час откладываю уборку')
        before = copy.deepcopy(s)
        reply = route_user_input(s, 'Что значит видимый шаг?')
        self.assertEqual(s, before)
        self.assertIn('поверхность', reply['text'])
        self.assertNotIn('файл', reply['text'])

    def test_contextual_instructions_do_not_open_an_apartment(self):
        for task, expected in [('убрать квартиру', 'поверхность'), ('разобрать почту', 'письмо'), ('написать статью', 'заголовок'), ('сделать презентацию', 'слайд')]:
            value = contextualize_task_text('Открой место задачи', {'current_task_title': task})
            self.assertIn(expected, value)
            self.assertNotIn('Открой «убрать', value)
        self.assertIn('?', concrete_step(''))

    def test_unknown_task_is_requested_before_experiment(self):
        s = new_session(DialogState.DAY1_SUMMARY)
        result = route_callback(s, 'experiment.start')
        self.assertIn('?', result['text'])
        self.assertEqual(s['experiment_count'], 0)
        self.assertNotEqual(s['state'], DialogState.EXPERIMENT_ACTIVE.value)
        route_user_input(s, 'достать поводок для прогулки')
        self.assertEqual(s['state'], DialogState.EXPERIMENT_ACTIVE.value)
        self.assertEqual(s['experiment_count'], 1)

    def test_preferences_are_not_observed_behaviors(self):
        result = bot._analysis_clarify_summary('phone', ['Собраться с мыслями', 'Скучно', 'Написать одну плохую строку'])
        self.assertNotIn('а после отвлечения', result)
        self.assertIn('ещё нужно попробовать', result)
        self.assertLessEqual(result.count('?'), 1)

    def test_report_does_not_invent_emotion_or_confidence(self):
        s = new_session()
        route_user_input(s, 'Нужно вымыть посуду')
        route_user_input(s, 'не знаю')
        route_user_input(s, 'сейчас просто нет времени')
        report = s['full_report']
        for false_claim in ('напряжение', 'краткое облегчение', 'избегание', '65%', 'способен(на)'):
            self.assertNotIn(false_claim, report)
        self.assertIn('нет времени', report)

    def test_explicit_review_is_not_overwritten_by_inference(self):
        self.assertEqual(review_focus({'function': 'start'}, 'STAY'), 'начать')
        self.assertEqual(review_focus({'function': 'return'}, 'START'), 'вернуться после отвлечения')

    def test_free_outcome_and_callback_retry_do_not_duplicate(self):
        s = new_session(DialogState.DAY1_SUMMARY)
        s['target_task'] = 'убрать квартиру'
        route_callback(s, 'experiment.start', callback_id='a')
        before = s['experiment_count']
        route_callback(s, 'experiment.start', callback_id='b')
        self.assertEqual(s['experiment_count'], before)
        reply = route_user_input(s, 'После этого стало хуже')
        self.assertIn('Остановим', reply['text'])
        self.assertNotIn('избегание', reply['text'])
        route_callback(s, 'experiment.result.negative', callback_id='c')
        self.assertEqual(len(s['skill_map']['failed_skills']), 1)
        self.assertEqual(s['skill_map']['successful_skills'], [])

    def test_facts_display_actual_success_denominator(self):
        text = render_working_model({'evidence_count': 3, 'helpful_interventions': {'Выбрать письмо': 2}, 'unhelpful_interventions': {'Выбрать письмо': 1}})
        self.assertIn('2 из 3', text)
        self.assertNotIn('%', text)

    def test_plain_render_keeps_quotes_and_urls(self):
        text = plain_text('START: попробовать. «Я сказал START». https://example.org/START')
        self.assertIn('начало:', text)
        self.assertIn('«Я сказал START»', text)
        self.assertIn('https://example.org/START', text)

    def test_button_aliases_are_unambiguous(self):
        self.assertEqual(len(BUTTON_LABELS.values()), len(set(BUTTON_LABELS.values())))


class DialogueIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = self.temp.name + '/test.db'
        await init_db(self.path)
        await migrate_db(self.path)
        self.path_patch = patch.object(bot, 'DB_PATH', self.path)
        self.path_patch.start()
        self.events_patch = patch.object(bot, 'log_event', AsyncMock())
        self.events_patch.start()
        self.user = default_user(38801)
        self.user.update(stage='analysis_clarify_questions', current_task_title='убрать квартиру')
        self.user['pending_feedback_json'] = json.dumps({'type': 'analysis_clarify', 'kind': 'phone', 'index': 1, 'answers': ['Отвлечься']})
        await save_user(self.user, self.path)

    async def asyncTearDown(self):
        self.events_patch.stop()
        self.path_patch.stop()
        self.temp.cleanup()

    async def test_voice_noise_preserves_pending_question_and_screen(self):
        self.user['dialogue_context'] = json.dumps({'text': 'Что происходит после этого?', 'stage': self.user['stage']})
        await save_user(self.user, self.path)
        m = Message(voice=SimpleNamespace())
        with patch.object(bot, 'whisper_transcribe', AsyncMock(return_value='🙂')):
            await bot.main_flow(m)
        saved = await get_user(self.user['user_id'], self.path)
        self.assertEqual(saved['stage'], self.user['stage'])
        self.assertEqual(saved['pending_feedback_json'], self.user['pending_feedback_json'])
        self.assertEqual(json.loads(saved['dialogue_context'])['text'], 'Что происходит после этого?')
        self.assertEqual(m.answers[-1][0], VOICE_FAILURE)
        self.assertEqual(len(m.answers[-1][1]['reply_markup'].keyboard), 2)

    async def test_help_survives_restart_without_advancing_answer(self):
        m = Message()
        await bot.answer_with_keyboard(m, self.user, 'Выберите один видимый шаг. Что это будет?', bot.kb_analysis_need_more, 'analysis')
        reloaded = await get_user(self.user['user_id'], self.path)
        self.assertIn('видимый', json.loads(reloaded['dialogue_context'])['text'])
        help_message = Message(text='Что значит видимый?')
        await bot.main_flow(help_message)
        saved = await get_user(self.user['user_id'], self.path)
        self.assertEqual(saved['stage'], 'analysis_clarify_questions')
        self.assertEqual(saved['pending_feedback_json'], self.user['pending_feedback_json'])
        self.assertIn('поверхность', help_message.answers[-1][0])

    async def test_other_button_does_not_become_clarifying_answer(self):
        m = Message(text=OTHER)
        await bot.main_flow(m)
        saved = await get_user(self.user['user_id'], self.path)
        self.assertEqual(saved['pending_feedback_json'], self.user['pending_feedback_json'])
        self.assertIn('обычными словами', m.answers[-1][0])

    async def test_skip_voice_does_not_skip_consent_or_question(self):
        self.user['stage'] = 'privacy_consent'
        await save_user(self.user, self.path)
        m = Message(text=VOICE_SKIP)
        await bot.main_flow(m)
        saved = await get_user(self.user['user_id'], self.path)
        self.assertEqual(saved['stage'], 'privacy_consent')

    async def test_gender_choice_is_persisted_and_optional(self):
        self.user['stage'] = 'ask_address'
        await save_user(self.user, self.path)
        m = Message(text='Женскую')
        await bot.main_flow(m)
        saved = await get_user(self.user['user_id'], self.path)
        self.assertEqual(saved['address_form'], 'female')
        self.assertEqual(saved['stage'], 'await_trainer')
        self.assertIn('стиль общения', m.answers[-1][0])

    async def test_yes_no_screen_has_a_question_and_free_text_escape(self):
        m = Message()
        kb = bot.ReplyKeyboardMarkup(keyboard=[[bot.KeyboardButton(text='Да'), bot.KeyboardButton(text='Нет')]])
        await bot.dialogue_message(m, self.user).answer('Похоже, мешает перегруз.', reply_markup=kb)
        self.assertIn('?', m.answers[-1][0])
        self.assertLessEqual(sum(len(r) for r in m.answers[-1][1]['reply_markup'].keyboard), bot.MAX_KEYBOARD_BUTTONS)

    async def test_safety_precedes_clarification_interceptor(self):
        m = Message(text='Объясни, я могу навредить себе прямо сейчас')
        with patch.object(bot, 'start_safety_interceptor', AsyncMock()) as safety:
            await bot.main_flow(m)
        safety.assert_awaited_once()
