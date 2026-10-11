"""PATCH-64: reproduce launch defects found in the owner's October Telegram run."""
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
import flows
from core.addressing import render_address
from core.dialogue_ux import plain_text
from db import default_user, get_user, get_user_profile, init_db, migrate_db, save_user, update_user_profile, daily_profile_explanation
from test_dialogue_ux_patch import Message


class LaunchJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/db'
        await init_db(self.path)
        await migrate_db(self.path)
        self.patch = patch.object(bot, 'DB_PATH', self.path)
        self.patch.start()
        self.uid = 646411

    async def asyncTearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    async def user(self, **values):
        u = await get_user(self.uid, self.path)
        u.update(values)
        await save_user(u, self.path)
        return u

    async def test_voice_cannot_reach_service_without_consent_in_any_stage(self):
        for stage in ('ask_name', 'privacy_consent', 'privacy_declined', 'await_problem_text', 'training_main', 'offer_request_form'):
            await self.user(stage=stage)
            with patch.object(bot, 'whisper_transcribe', AsyncMock()) as service:
                m = Message(self.uid, voice=SimpleNamespace())
                await bot.main_flow(m)
                service.assert_not_awaited()
                self.assertIn('Сначала нужно согласие', m.answers[-1][0])
                self.assertEqual((await get_user(self.uid, self.path))['stage'], stage)

    async def test_voice_is_transcribed_once_in_both_legacy_diagnostic_modes(self):
        for stage in ('await_problem_text', 'await_problem_voice', 'intent_act_context'):
            await self.user(stage=stage)
            await update_user_profile(self.uid, {'privacy_consent': True}, self.path)
            with patch.object(bot, 'whisper_transcribe', AsyncMock(return_value='Хочу начать отчёт, но смотрю видео')) as service, patch.object(bot, 'run_analysis', AsyncMock()):
                m = Message(self.uid, voice=SimpleNamespace())
                await bot.main_flow(m)
                self.assertEqual(service.await_count, 1, stage)
                self.assertEqual(sum('Распознаю голосовое' in answer[0] for answer in m.answers), 1, stage)

    async def test_stale_confirmation_does_not_invent_a_social_support(self):
        await self.user(stage='social_support_await')
        m = Message(self.uid, '✅ Да, в точку')
        await bot.main_flow(m)
        saved = await get_user(self.uid, self.path)
        profile = await get_user_profile(self.uid, self.path)
        self.assertEqual(saved['stage'], 'social_support_await')
        self.assertFalse(profile.get('social_support_available'))
        self.assertNotIn('Записал в карту', m.answers[-1][0])

    async def test_selected_barrier_replaces_old_generic_pattern(self):
        u = await self.user(stage='awaiting_barrier_choice', analysis_json=json.dumps({'specific_pattern': 'default_start_block', 'live_pattern': 'default_start_block'}))
        m = Message(self.uid, '📱 Отвлечения')
        await bot.continue_analysis_from_barrier(m, u, m.text)
        answer = m.answers[-1][0]
        self.assertNotIn('default_start_block', answer)
        self.assertNotIn('чаще всего вход ломает переключаюсь', answer)
        comp = json.loads(u['analysis_json'])
        self.assertEqual(comp['live_pattern'], 'attention_escape')
        self.assertEqual(comp['selected_barrier'], 'attention_escape')

    async def test_repeated_case_confirmation_starts_action_without_second_onboarding(self):
        await self.user(stage='confirm_analysis', has_started_training=1, analysis_json=json.dumps({'selected_skill':'open_only'}))
        with patch.object(bot, 'start_intent_action', AsyncMock()) as action:
            m = Message(self.uid, '✅ Да, в точку')
            await bot.main_flow(m)
            action.assert_awaited_once()
            self.assertFalse(any('опорой' in x[0] for x in m.answers))

    async def test_old_training_button_cannot_submit_application(self):
        await self.user(stage='offer_request_form', username='owner_test')
        with patch.object(bot, 'notify_offer_request', AsyncMock()) as send:
            await bot.main_flow(Message(self.uid, '🏠 Семья / близкий'))
            send.assert_not_awaited()
        self.assertEqual((await get_user(self.uid, self.path))['stage'], 'offer_request_form')

    async def test_successful_phone_step_continues_without_phone_restart(self):
        u = await self.user(stage='post_action_reflection', current_skill='phone_far_3min', daily_skill_id='phone_far_3min',
                            day_core_skill_id='phone_far_3min', day_core_skill_date=bot.local_date_for_user(default_user(self.uid)),
                            current_task_title='убрать кухню', done_count=1)
        await update_user_profile(self.uid, {'last_skill_feedback': {'continued_after_skill': True, 'helpfulness':'helped'}}, self.path)
        with patch.object(bot, 'get_honest_day_counts', AsyncMock(return_value={'attempts_today':1})), patch.object(bot, 'should_switch_to_consolidation', return_value=False):
            m = Message(self.uid)
            await bot.open_next_logical_step(m, u)
        self.assertNotEqual(u['daily_skill_id'], 'phone_far_3min')
        self.assertEqual(u['day_core_skill_id'], 'phone_far_3min')
        self.assertIn('Следующий шаг по задаче «убрать кухню»', m.answers[-1][0])
        self.assertNotIn('телефон уже убран', m.answers[-1][0])


class LaunchCopy(unittest.TestCase):
    def test_day_count_and_distraction_never_invent_emotional_cause(self):
        for day in (1, 2, 3, 8, 28):
            text = daily_profile_explanation({'attention_pattern':'scroll_autopilot', 'downscale_count':3}, 'open_only', day)
            for unsupported in ('уйти от напряжения', 'карта стала точнее', 'Уже видно', 'помог тебе начать'):
                self.assertNotIn(unsupported, text)
            self.assertIn('данных мало', text)

    def test_machine_fallback_is_hidden_but_user_quotes_stay_intact(self):
        text = plain_text('default_start_block — «default_start_block» https://example.com/default_start_block')
        self.assertTrue(text.startswith('первое действие пока трудно выбрать'))
        self.assertIn('«default_start_block»', text)
        self.assertIn('https://example.com/default_start_block', text)

    def test_skinny_instruction_has_no_assumed_male_past(self):
        line = bot.SKILLS_DB['open_only']['trainer_variants']['skinny']
        for mode in ('ty', 'vy'):
            for form in ('male','female','neutral'):
                self.assertNotIn('Открыл.', render_address(line, {'address_mode':mode,'address_form':form}))

    def test_preliminary_map_does_not_repeat_synonymous_attention_facts(self):
        comp = {'specific_pattern':'уход внимания в быстрые награды', 'avoidance_behavior':'внимание уходит в быстрые награды',
                'analysis_result':{'evidence_signals':['внимание уходит в быстрые награды'], 'first_check':'Убрать телефон'}}
        text = bot.preliminary_development_map_from_analysis(comp)
        self.assertEqual(text.count('быстрые награды'), 1)
        self.assertLess(len(text), 500)


class PublicMetadata(unittest.IsolatedAsyncioTestCase):
    async def test_description_sync_changes_only_outdated_fields(self):
        client = SimpleNamespace(get_my_description=AsyncMock(return_value=SimpleNamespace(description='old')),
            set_my_description=AsyncMock(), get_my_short_description=AsyncMock(return_value=SimpleNamespace(short_description=bot.PUBLIC_BOT_SHORT_DESCRIPTION)),
            set_my_short_description=AsyncMock())
        await bot.sync_public_bot_description(client)
        client.set_my_description.assert_awaited_once_with(description=bot.PUBLIC_BOT_DESCRIPTION)
        client.set_my_short_description.assert_not_awaited()
        self.assertLessEqual(len(bot.PUBLIC_BOT_DESCRIPTION),512)
        self.assertLessEqual(len(bot.PUBLIC_BOT_SHORT_DESCRIPTION),120)

    async def test_metadata_failure_does_not_block_polling_startup(self):
        await bot.sync_public_bot_description(SimpleNamespace(get_my_description=AsyncMock(side_effect=RuntimeError('offline'))))
