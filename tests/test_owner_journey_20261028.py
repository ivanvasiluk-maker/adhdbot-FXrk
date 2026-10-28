"""PATCH-82: regressions from the owner's actual free-bot journey."""
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import unquote

import bot
import flows
from core.consultation import consultation_url
from core.experiment_dialogue import reported_chain, render_chain
from db import get_user, get_user_profile
from test_dialogue_ux_patch import Message
import test_launch_fixes_20261011 as fixtures

CASE = ('Мне нужно написать рекламный пост о группе СДВГ. '
        'Открываю документ, думаю: «Получится банально, никто не запишется», '
        'и начинаю смотреть чужие посты. На несколько минут тревога уменьшается, '
        'но потом становится сильнее: время ушло, а мой текст пустой. '
        'Хочу сегодня написать хотя бы черновик.')
TASK = 'написать рекламный пост о группе СДВГ'


class OwnerJourney(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LaunchJourneys.asyncSetUp
    asyncTearDown = fixtures.LaunchJourneys.asyncTearDown
    user = fixtures.LaunchJourneys.user

    def test_chain_preserves_all_explicit_links_and_separates_the_later_reversal(self):
        chain = reported_chain(CASE)
        self.assertEqual(chain['thoughts'], 'Получится банально, никто не запишется')
        self.assertEqual(chain['behavior'], 'начинаю смотреть чужие посты')
        self.assertIn('тревога уменьшается', chain['immediate'])
        self.assertNotIn('потом', chain['immediate'])
        self.assertIn('потом становится сильнее', chain['delayed'])
        self.assertIn('мой текст пустой', chain['delayed'])
        self.assertFalse(chain['missing'])
        self.assertNotIn('Что ты делаешь вместо задачи?', render_chain(chain))

    def test_explicit_no_relief_is_not_presented_as_negative_reinforcement(self):
        chain = reported_chain('Нужно написать отчёт. Смотрю YouTube. После этого облегчения нет.')
        self.assertNotIn('может закрепляться временным облегчением', render_chain(chain))
        unknown = reported_chain('Нужно написать отчёт. Смотрю YouTube.')
        self.assertIsNone(unknown['immediate'])

    async def test_initial_analysis_does_not_reparse_a_truncated_case(self):
        u = await self.user(stage='await_problem_text', trainer_key='marsha')
        m = Message(self.uid)
        await flows.run_analysis(m, u, CASE, self.path, client=None)
        text = '\n'.join(value for value, _ in m.answers)
        self.assertIn('начинаю смотреть чужие посты', text)
        self.assertIn('мой текст пустой', text)
        self.assertNotIn('Поведение: ещё не уточнено', text)

    async def test_day_one_uses_the_task_already_named_in_diagnosis(self):
        u = await self.user(stage='waiting_next_day', trainer_key='marsha',
                            today_target=TASK, current_task_title=TASK, current_task_name=TASK,
                            plan_json=json.dumps(['check_the_facts_light']),
                            analysis_json=json.dumps({'behavioral_chain':reported_chain(CASE)}))
        m = Message(self.uid)
        await bot.start_day(m, u, 1, self.path)
        text = '\n'.join(value for value, _ in m.answers)
        self.assertNotIn('Перед стартом: что', text)
        self.assertNotEqual(u['stage'], 'await_training_target')
        self.assertEqual(u['today_target'], TASK)
        self.assertIn('Навык', text)

    async def test_unknown_task_still_gets_the_existing_question(self):
        u = await self.user(stage='waiting_next_day', today_target='', current_task_title='',
                            current_task_name='', analysis_json='{}', plan_json=json.dumps(['open_only']))
        m = Message(self.uid)
        await bot.start_day(m, u, 1, self.path)
        self.assertEqual(u['stage'], 'await_training_target')
        self.assertIn('Перед стартом:', m.answers[-1][0])

    def test_return_does_not_claim_a_generated_step_was_chosen(self):
        text = bot.returning_to_task_text({'today_target':TASK,
                                         'current_next_physical_step':'открыть реклама на 10 секунд'})
        self.assertIn(TASK, text)
        self.assertNotIn('выбрал', text)
        self.assertNotIn('открыть реклама', text)

    async def test_gender_choice_delivers_plain_text_notice_and_repeat_replays_it(self):
        await self.user(stage='ask_address', address_mode='ty',
                        dialogue_context=json.dumps({'choosing_address':'gender',
                                                     'trainer_selected_before_address':True}))
        class TelegramLikeMessage(Message):
            async def answer(self, text, **kwargs):
                if '🔐' in text and kwargs.get('parse_mode', 'Markdown') is not None:
                    raise ValueError('Telegram cannot parse the underscore in the contact')
                return await super().answer(text, **kwargs)
        for _ in range(2):
            m = TelegramLikeMessage(self.uid, 'Мужской')
            await bot.main_flow(m)
            self.assertEqual((await get_user(self.uid, self.path))['stage'], 'privacy_consent')
            self.assertIn('🔐', m.answers[-1][0])
            self.assertIsNone(m.answers[-1][1]['parse_mode'])
            self.assertIn('/reset_me', m.answers[-1][0])
            self.assertNotIn('\\_', m.answers[-1][0])
        await bot.main_flow(Message(self.uid, '✅ Даю согласие, продолжить'))
        self.assertTrue((await get_user_profile(self.uid, self.path))['privacy_consent'])

    def test_public_offers_are_generic_and_calendar_keeps_the_supplied_destination(self):
        for text in (bot.tariff_live_text(), bot.tariff_group_text(), bot.short_offer_text(),
                     bot.offer_request_submitted_text(True), bot.offer_request_submitted_text(False)):
            self.assertNotIn('Василюк', text)
            self.assertNotIn('Иван', text.replace('@Ivan_Vasiliuk', ''))
        self.assertIn('психолог', bot.tariff_live_text())
        # The data-recipient identity stays explicit at consent, not in sales copy.
        self.assertIn('Ивану Василюку', bot.offer_request_form_text('Группа', group=True))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(unquote(consultation_url()), 'https://cal.com/иван-василюк-dwlef3/15min')
