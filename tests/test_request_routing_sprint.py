import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import bot
import flows
from db import default_user, init_db, migrate_db, save_user, get_user
from core.request_routing import request_kind, device_from_text
from test_dialogue_ux_patch import Message


class RoutingTests(unittest.TestCase):
    def test_categories_and_task_about_health(self):
        examples = {
            'Не могу начать отчёт': 'task_problem',
            'Ничего не хочется, лежу на диване между клиентами': 'emotional_state',
            'У меня изменился аппетит, это депрессия?': 'health_or_medication',
            'Хочу умереть': 'crisis',
            'Как начать разговор с женой после ссоры?': 'relationship',
            'Хочу понять, что делать дальше': 'other',
            'Нужно написать статью про депрессию': 'task_problem',
            'Я боюсь, что с ботом опять что-то пойдёт не так и избегаю его': 'task_problem',
        }
        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(request_kind(text), expected)

    def test_ai_label_is_not_preserved_as_a_conclusion(self):
        comp = flows.normalize_analysis({'specific_pattern': 'ленивое состояние'}, 'Не могу начать отчёт')
        self.assertNotIn('ленив', comp['specific_pattern'])
        from core.dialogue_ux import plain_text
        self.assertNotIn('ленивое состояние', plain_text('Это ленивое состояние.'))
        self.assertEqual(plain_text('«Я ленивый»'), '«Я ленивый»')

    def test_device_is_not_guessed_from_youtube(self):
        self.assertIsNone(device_from_text('Отвлекаюсь на YouTube и сайты переписок'))
        self.assertEqual(device_from_text('На компьютере смотрю YouTube'), 'computer')
        self.assertEqual(device_from_text('На телефоне и на компьютере'), 'both')

    def test_anxiety_without_physical_load_does_not_choose_body_first(self):
        comp = {'live_pattern':'anxious_anxiety', 'physiological_load':False}
        result = flows.build_analysis_result(comp, 'Боюсь, что всё пойдёт плохо, избегаю задачу')
        self.assertEqual(result['recommended_variant'], 'check_the_facts_light')
        self.assertIn('конкретное опасение', result['first_check'])
        comp['physiological_load'] = True
        self.assertEqual(flows.build_analysis_result(comp, 'Боюсь, тревога, сильное напряжение')['recommended_variant'], 'body_before_task')


class RequestJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/test.db'
        await init_db(self.path)
        await migrate_db(self.path)
        u = default_user(77443)
        u.update(stage='day_core_stop', day_closed=1, today_closed=1, daily_training_completed=1,
                 last_day_closed_at=bot.local_date_for_user(u))
        await save_user(u, self.path)
        self.path_patch = patch.object(bot, 'DB_PATH', self.path)
        self.path_patch.start()

    async def asyncTearDown(self):
        self.path_patch.stop()
        self.tmp.cleanup()

    async def send(self, text):
        m = Message(uid=77443, text=text)
        with patch.object(bot, 'client', None):
            await bot.main_flow(m)
        return m, await get_user(77443, self.path)

    async def test_health_request_gets_support_without_task_diagnosis_or_ai(self):
        text = 'Последний месяц лежу на диване, не хочу общаться, изменился аппетит. Может это депрессия и нужны препараты?'
        with patch.object(flows, 'ai_analyze_comprehensive', AsyncMock()) as ai:
            m, u = await self.send(text)
            ai.assert_not_awaited()
        reply = m.answers[-1][0]
        self.assertIn('диагноз не определяется', reply)
        self.assertIn('запишите', reply)
        self.assertNotIn('Что чаще ломает вход', reply)
        comp = json.loads(u['analysis_json'])
        self.assertEqual(comp['request_kind'], 'health_or_medication')
        self.assertIsNone(comp['selected_skill'])
        self.assertNotIn(text, u['analysis_json'])
        self.assertIsNone(json.loads(u['current_case_json'])['current_case_hypothesis'])
        details, after = await self.send('📚 Подробнее')
        self.assertIn('диагноз не определяется', details.answers[-1][0])
        self.assertEqual(after['stage'], u['stage'])
        await self.send('Разобрать конкретное дело')
        _, task = await self.send('Не могу начать отчёт')
        self.assertEqual(task['stage'], 'awaiting_barrier_choice')

    async def test_short_medication_request_waits_for_context(self):
        _, u = await self.send('Нужны ли мне препараты?')
        self.assertEqual(u['stage'], 'request_context')
        self.assertIsNone(json.loads(u['analysis_json'])['selected_skill'])
        _, after = await self.send('Месяц сплю меньше и ничего не хочется')
        self.assertEqual(after['stage'], 'request_support')
        self.assertEqual(json.loads(after['analysis_json'])['request_kind'], 'health_or_medication')
        self.assertEqual(json.loads(after['current_case_json'])['case_id'], json.loads(u['current_case_json'])['case_id'])

    async def test_device_question_waits_then_uses_computer_skill(self):
        with patch.object(flows, 'ai_analyze_comprehensive', AsyncMock()) as ai:
            question, first = await self.send('Отвлекаюсь на YouTube и сайты переписок')
            ai.assert_not_awaited()
        self.assertEqual(first['stage'], 'request_device')
        self.assertIn('Где чаще отвлекаетесь', question.answers[-1][0])
        case_id = json.loads(first['current_case_json'])['case_id']
        _, second = await self.send('На том же компьютере, где работаю')
        comp = json.loads(second['analysis_json'])
        self.assertEqual(comp['request_device'], 'computer')
        self.assertEqual(json.loads(second['current_case_json'])['case_id'], case_id)
        if second['stage'] == 'awaiting_barrier_choice':
            _, second = await self.send('📱 Отвлечения')
            comp = json.loads(second['analysis_json'])
        self.assertEqual(comp['selected_skill'], 'one_tab_focus')
        self.assertIn('рабочее окно', comp['analysis_result']['first_check'])
        self.assertNotIn('телефон', comp['analysis_result']['first_check'].lower())
        _, action = await self.send('💪 Давай действие')
        self.assertEqual(action['daily_skill_id'], 'one_tab_focus')
        self.assertTrue(bot.day_closed_today(action))

    async def test_explicit_phone_does_not_repeat_device_question(self):
        _, u = await self.send('Не могу начать задачу: отвлекаюсь на YouTube на телефоне и не возвращаюсь к делу')
        self.assertNotEqual(u['stage'], 'request_device')
        self.assertEqual(json.loads(u['analysis_json'])['request_device'], 'phone')

    async def test_unclear_request_can_be_clarified_as_a_task(self):
        _, first = await self.send('Не знаю, что делать дальше')
        self.assertEqual(first['stage'], 'request_context')
        _, clarified = await self.send('Не могу начать отчёт')
        self.assertEqual(clarified['stage'], 'awaiting_barrier_choice')

    async def test_both_devices_do_not_invent_an_application(self):
        _, first = await self.send('Отвлекаюсь на соцсети')
        self.assertEqual(first['stage'], 'request_device')
        _, after = await self.send('И там и там')
        if after['stage'] == 'awaiting_barrier_choice':
            _, after = await self.send('📱 Отвлечения')
        comp = json.loads(after['analysis_json'])
        self.assertEqual(comp['request_device'], 'both')
        self.assertEqual(comp['selected_skill'], 'one_tab_focus')
        self.assertIn('Телефон', comp['analysis_result']['first_check'])
        self.assertNotIn('YouTube', after['analysis_json'])

    async def test_crisis_keeps_existing_global_priority(self):
        await self.send('Нужны ли мне препараты?')
        with patch.object(bot, 'start_safety_interceptor', AsyncMock()) as safety:
            await self.send('Хочу умереть')
            safety.assert_awaited_once()

    async def test_self_label_is_not_a_diagnosis_and_can_stop(self):
        m, u = await self.send('Я ленивый, ничего не хочется уже месяц')
        self.assertNotIn('ленивое состояние', m.answers[-1][0])
        self.assertNotIn('selected_skill', json.loads(u['analysis_json']).get('analysis_result', {}))
        _, after = await self.send('На этом пока остановиться')
        self.assertEqual(after['stage'], 'day_core_stop')
        self.assertEqual(after['closed_day_additional_active'], 0)


if __name__ == '__main__':
    unittest.main()
