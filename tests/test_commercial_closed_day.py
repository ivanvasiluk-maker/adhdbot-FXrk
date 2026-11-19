"""A completed daily session must not replace a new case's skill route."""
import json
import unittest
from unittest.mock import patch

import bot
import core.experiment_dialogue as experiment_dialogue
from db import get_user, get_user_profile, update_user_profile
from test_dialogue_ux_patch import Message
import test_launch_fixes_20261011 as fixtures


CASE = """Сегодня планировал закончить отчёт, ответить на письма и записаться к врачу.
Сейчас у меня открыто 25 вкладок. Когда пытаюсь выбрать одну задачу,
кажется, что всё одинаково срочно, начинаю листать социальные сети.
Не знаю что выбрать и как закончить хоть одно дело."""


class CommercialClosedDay(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LaunchJourneys.asyncSetUp
    asyncTearDown = fixtures.LaunchJourneys.asyncTearDown
    user = fixtures.LaunchJourneys.user

    async def say(self, text):
        message = Message(self.uid, text)
        await bot.main_flow(message)
        return message, await get_user(self.uid, bot.DB_PATH)

    async def test_new_case_skill_rating_repeat_and_switch_after_closed_day(self):
        with patch.object(bot, 'COMMERCIAL_PRACTICE_ENABLED', True), patch.object(experiment_dialogue, 'COMMERCIAL_PRACTICE_ENABLED', True):
            u = await self.user(stage='intent_act_context', today_target='',
                                day_status='closed', day_closed=1, today_closed=1,
                                day_core_skill_id='open_only')
            closed_at = bot.local_date_for_user(u) + 'T12:00:00+00:00'
            await self.user(last_day_closed_at=closed_at)
            await update_user_profile(self.uid, {'privacy_consent': True}, bot.DB_PATH)
            m, u = await self.say(CASE)
            self.assertEqual(u['stage'], 'analysis_clarify_done')
            self.assertIn('Расстановка приоритетов', m.answers[-1][0])
            m, u = await self.say('Попробовать навык')
            instruction = m.answers[-1][0]
            self.assertEqual(u['stage'], 'training')
            self.assertIn('последствия переноса', instruction)
            self.assertNotIn('Одна видимая подсказка', instruction)
            labels = [b.text for row in m.answers[-1][1]['reply_markup'].keyboard for b in row]
            self.assertIn('✅ Шаг выполнен', labels)
            self.assertEqual(u['day_core_skill_id'], 'open_only')
            self.assertEqual(u['last_day_closed_at'], closed_at)
            m, u = await self.say('✅ Шаг выполнен')
            self.assertIn('Удалось ли', m.answers[-1][0])
            m, u = await self.say('Да, помогло')
            self.assertTrue(any('Карточка сохранена' in a[0] for a in m.answers))
            self.assertTrue(bot.day_closed_today(u))
            m, u = await self.say('Повторить навык')
            self.assertEqual(u['stage'], 'training')
            self.assertEqual(m.answers[-1][0], instruction)
            await self.say('✅ Шаг выполнен')
            await self.say('Пока не помогло')
            m, u = await self.say('Выбрать другой навык')
            labels = [b.text for row in m.answers[-1][1]['reply_markup'].keyboard for b in row]
            alternative = next(label for label in labels if label.startswith('Навык: '))
            m, u = await self.say(alternative)
            self.assertEqual(u['stage'], 'training')
            self.assertNotEqual(json.loads(u['analysis_json'])['selected_skill'], 'visible_next_step')
            self.assertNotIn('основной день останется закрытым', m.answers[-1][0])
            self.assertEqual(u['last_day_closed_at'], closed_at)
            profile = await get_user_profile(self.uid, bot.DB_PATH)
            self.assertIn('last_skill_feedback', profile)
