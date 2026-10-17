"""PATCH-70: reproduce the owner's live morning/work/evening dialogue."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import bot
import flows
import test_launch_fixes_20261011 as fixtures
from test_dialogue_ux_patch import Message
from db import get_user, update_user_profile
from core.experiment_dialogue import reported_chain

CASE = '''Мне 34. Я работаю из дома, и у меня уже недели три полный развал.
Работы объективно не очень много, но я всё откладываю до вечера. Утром открываю ноутбук, потом иду делать кофе, читаю новости, могу зависнуть в YouTube или Telegram. Потом уже часов в 15 понимаю, что ничего не сделал, начинаю паниковать и ненавидеть себя.
Вечером иногда за два часа делаю почти всё, что откладывал весь день. Поэтому понимаю, что вроде могу. Но каждый следующий день повторяется то же самое.
Я пробовал ежедневники, Pomodoro, Notion, удалял соцсети — хватает максимум на пару дней.
Мне кажется, я просто ленивый и безвольный человек. Что со мной делать?'''

class Facts(unittest.TestCase):
    def test_reported_behavior_and_delayed_cost_are_not_unknown(self):
        chain=reported_chain(CASE)
        self.assertIn('недели три',chain['context'])
        self.assertIn('ноутбук',chain['situation'])
        self.assertIn('кофе',chain['behavior'])
        self.assertIn('YouTube',chain['behavior'])
        self.assertIn('15',chain['delayed'])
        self.assertIn('паниковать',chain['delayed'])
        self.assertIn('два часа',chain['resources'])
        self.assertIn('Pomodoro',chain['tried'])
        self.assertIsNone(chain['immediate'])
        self.assertIsNone(chain['feelings'])
        self.assertEqual(chain['later_feelings'],'паника')
        self.assertIsNone(chain['thoughts'])
        self.assertIn('ленивый',chain['self_assessment'])

    def test_safe_memory_preserves_chain_without_full_transcript(self):
        memory=flows.safe_analysis_memory(CASE)
        self.assertIn('кофе',memory['behavioral_chain']['behavior'])
        self.assertNotIn(CASE,json.dumps(memory,ensure_ascii=False))

    def test_reanalysis_prefers_reported_facts_to_generic_categories(self):
        memory=flows.safe_analysis_memory(CASE)
        memory['input_signal_summary']={'specific_pattern':'нужно вернуть контроль внимания'}
        text=bot.stored_analysis_user_text({'analysis_json':json.dumps(memory,ensure_ascii=False)})
        self.assertIn('кофе',text)
        self.assertIn('паниковать',text)
        self.assertIn('два часа',text)
        self.assertNotIn('нужно вернуть контроль внимания',text)

class Journey(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.LaunchJourneys.asyncSetUp
    asyncTearDown=fixtures.LaunchJourneys.asyncTearDown
    user=fixtures.LaunchJourneys.user

    async def say(self,text):
        m=Message(self.uid,text)
        with patch.object(flows,'ai_analyze',AsyncMock()) as quick,patch.object(flows,'ai_analyze_comprehensive',AsyncMock()) as ai,patch.object(bot,'ai_analyze_comprehensive',AsyncMock()) as rebuild:
            await bot.main_flow(m)
            quick.assert_not_awaited();ai.assert_not_awaited();rebuild.assert_not_awaited()
        return m,await get_user(self.uid,bot.DB_PATH)

    async def test_brief_buttons_to_action_in_two_answers(self):
        await self.user(stage='await_problem_text',today_target='')
        m,u=await self.say(CASE)
        self.assertLess(len(m.answers[-1][0]),250)
        self.assertNotIn('Уже известные факты',m.answers[-1][0])
        self.assertEqual(json.loads(u['analysis_json'])['case_question'],'brief_barrier')
        m,u=await self.say('🟠 Скучно')
        comp=json.loads(u['analysis_json'])
        self.assertEqual(comp['case_question'],'brief_task')
        self.assertEqual(comp['reported_difficulty'],'🟠 Скучно')
        self.assertIsNone(comp['behavioral_chain']['immediate'])
        m,u=await self.say('Открыть отчёт по продажам')
        self.assertEqual(u['stage'],'analysis_clarify_done')
        self.assertEqual(u['today_target'],'Открыть отчёт по продажам')
        self.assertNotIn('Автоматические мысли',m.answers[-1][0])
        m,u=await self.say('💪 Давай действие')
        self.assertNotEqual(u['stage'],'reported_case_question')
        self.assertEqual(u['today_target'],'Открыть отчёт по продажам')

    async def test_known_task_needs_only_barrier_and_start(self):
        await self.user(stage='await_problem_text')
        m,u=await self.say(CASE.replace('Работы объективно','Нужно написать отчёт по продажам. Работы объективно'))
        await self.say('🌀 Непонятно, с чего начать')
        m,u=await self.say('▶️ Да, давай начнём')
        self.assertEqual(u['stage'],'analysis_clarify_done')
        self.assertEqual(u['today_target'],'написать отчёт по продажам')

    async def test_other_and_free_text_do_not_fill_chain_consequences(self):
        await self.user(stage='await_problem_text')
        await self.say(CASE)
        m,u=await self.say('✍️ Другое — расскажу')
        self.assertEqual(json.loads(u['analysis_json'])['case_question'],'brief_barrier')
        m,u=await self.say('Отвлекался')
        self.assertEqual(json.loads(u['analysis_json'])['reported_difficulty'],'Отвлекался')
        self.assertIsNone(json.loads(u['analysis_json'])['behavioral_chain']['immediate'])

    async def test_deep_chain_is_opt_in_and_keeps_reported_facts(self):
        await self.user(stage='await_problem_text',today_target='')
        await self.say(CASE)
        m,u=await self.say('🔎 Хочу разобраться глубже')
        comp=json.loads(u['analysis_json'])
        self.assertEqual(comp['case_mode'],'detailed')
        self.assertEqual(comp['case_question'],'task')
        self.assertIn('кофе',m.answers[-1][0])
        await self.say('Написать отчёт')
        m,u=await self.say('Стало спокойнее')
        self.assertEqual(json.loads(u['analysis_json'])['case_question'],'thoughts')

    async def test_deep_available_after_brief_completion(self):
        await self.user(stage='await_problem_text')
        await self.say(CASE)
        await self.say('🟠 Скучно')
        await self.say('Открыть таблицу продаж')
        m,u=await self.say('🔎 Хочу разобраться глубже')
        self.assertEqual(json.loads(u['analysis_json'])['case_mode'],'detailed')
        self.assertEqual(u['today_target'],'Открыть таблицу продаж')

    async def test_stale_buttons_pause_and_resume(self):
        await self.user(stage='await_problem_text')
        await self.say(CASE)
        m,u=await self.say('💪 Давай действие')
        self.assertNotIn('reported_difficulty',json.loads(u['analysis_json']))
        await self.say('стоп')
        m,u=await self.say('Продолжить')
        self.assertEqual(u['stage'],'reported_case_question')
        self.assertEqual(json.loads(u['analysis_json'])['case_question'],'brief_barrier')

    async def test_rejection_does_not_repeat_rejected_hypothesis(self):
        comp=flows.safe_analysis_memory(CASE)
        comp.update(specific_pattern='тревога уводит в быстрые награды',selected_skill='one_tab_focus')
        await self.user(stage='confirm_analysis',analysis_json=json.dumps(comp,ensure_ascii=False))
        await self.say('😑 Ты меня не понял')
        m,u=await self.say('2. Слишком общий ответ')
        self.assertEqual(json.loads(u['analysis_json'])['case_mode'],'brief')
        self.assertNotIn('быстрые награды',m.answers[-1][0])

    async def test_voice_answer_and_four_button_limit(self):
        await self.user(stage='await_problem_text')
        m,u=await self.say(CASE)
        markup=m.answers[-1][1]['reply_markup']
        self.assertLessEqual(sum(len(row) for row in markup.keyboard),4)
        await update_user_profile(self.uid,{'privacy_consent':True},self.path)
        with patch.object(bot,'whisper_transcribe',AsyncMock(return_value='Скучно')) as service:
            await bot.main_flow(Message(self.uid,voice=SimpleNamespace()))
            service.assert_awaited_once()
        u=await get_user(self.uid,self.path)
        self.assertEqual(json.loads(u['analysis_json'])['reported_difficulty'],'Скучно')

    async def test_safety_precedes_brief(self):
        await self.user(stage='await_problem_text')
        await self.say(CASE)
        with patch.object(bot,'start_safety_interceptor',AsyncMock()) as safety:
            await bot.main_flow(Message(self.uid,'Хочу умереть'))
            safety.assert_awaited_once()

class ComplexCases(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=Journey.asyncSetUp
    asyncTearDown=Journey.asyncTearDown
    user=Journey.user
    say=Journey.say

    async def test_long_task_without_recognized_behavior_or_consequences_asks(self):
        text = ('Нужно подготовить презентацию для нового клиента, но я не понимаю, как начать. '
                'В проекте несколько участников с разными ожиданиями, требования меняются, '
                'и у меня нет уверенности, что выбранный вариант подходит. '
                'Я хочу разобраться в одном вчерашнем эпизоде и понять, что именно мешает. '
                'Пока не могу назвать ни конкретное действие, ни то, что изменилось сразу после него. '
                'Мне нужен разговор с уточнениями, а не готовое объяснение причин.')
        self.assertGreaterEqual(len(text.split()),60)
        await self.user(stage='await_problem_text', today_target='')
        m,u=await self.say(text)
        self.assertEqual(u['stage'],'reported_case_question')
        comp=json.loads(u['analysis_json'])
        self.assertFalse(comp.get('selected_skill'))
        self.assertFalse(comp['analysis_result'])
        self.assertIn('труднее',m.answers[-1][0])

    async def test_long_relationship_asks_without_work_or_avoidance_assumption(self):
        text = ('У нас с мужем конфликт из-за домашних обязанностей. '
                'Я прошу его сделать покупки, он соглашается, но потом забывает. '
                'Я напоминаю несколько раз и в конце повышаю голос. '
                'Он говорит, что я все контролирую, а я чувствую, что осталась одна со всеми делами. '
                'Я не понимаю, как обсудить это спокойно и не вернуться к прежней ссоре. '
                'Хочу разобрать один конкретный разговор вчера вечером, а не угадывать его намерения.')
        await self.user(stage='await_problem_text',today_target='')
        m,u=await self.say(text)
        self.assertEqual(u['stage'],'reported_case_question')
        self.assertNotIn('откладывани',m.answers[-1][0])
        self.assertIn('покупки',json.loads(u['analysis_json'])['behavioral_chain']['context'])
        self.assertFalse(json.loads(u['analysis_json']).get('selected_skill'))

    async def test_long_health_or_crisis_keeps_support_priority(self):
        await self.user(stage='await_problem_text',today_target='')
        m,u=await self.say('Принимаю антидепрессант. '+CASE)
        self.assertNotEqual(u['stage'],'reported_case_question')
        self.assertEqual(json.loads(u['analysis_json'])['request_kind'],'health_or_medication')
        with patch.object(bot,'start_safety_interceptor',AsyncMock()) as safety:
            await bot.main_flow(Message(self.uid,'Хочу умереть. '+CASE))
            safety.assert_awaited_once()
