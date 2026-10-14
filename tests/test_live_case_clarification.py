"""PATCH-70: reproduce the owner's live morning/work/evening dialogue."""
import json
import unittest
from unittest.mock import AsyncMock, patch
import bot
import flows
import test_launch_fixes_20261011 as fixtures
from test_dialogue_ux_patch import Message
from db import get_user
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

    async def test_full_initial_report_asks_task_before_a_skill(self):
        await self.user(stage='await_problem_text',trainer_key='beck',address_mode='ty',address_form='female',today_target='')
        m,u=await self.say(CASE)
        self.assertEqual(u['stage'],'reported_case_question')
        comp=json.loads(u['analysis_json'])
        self.assertEqual(comp['case_question'],'task')
        self.assertIn('кофе',m.answers[-1][0])
        self.assertIn('одно рабочее дело',m.answers[-1][0])
        self.assertNotIn('стало легче',m.answers[-1][0])
        self.assertNotIn('selected_skill',comp)
        self.assertNotIn('Что будем делать',m.answers[-1][0])
        m,u=await self.say('Написать отчёт по продажам')
        self.assertEqual(json.loads(u['analysis_json'])['case_question'],'immediate')
        self.assertIn('Что менялось сразу',m.answers[-1][0])
        m,u=await self.say('Становится спокойнее, потому что не нужно смотреть на отчёт')
        self.assertEqual(json.loads(u['analysis_json'])['case_question'],'thoughts')
        m,u=await self.say('Кажется, надо сделать всё идеально, непонятно с чего начать')
        self.assertEqual(json.loads(u['analysis_json'])['case_question'],'feelings')
        m,u=await self.say('Перед началом растерянность и напряжение')
        self.assertEqual(u['stage'],'analysis_clarify_done')
        self.assertEqual(u['today_target'],'Написать отчёт по продажам')
        m,u=await self.say('💪 Давай действие')
        self.assertNotEqual(u['stage'],'reported_case_question')
        self.assertEqual(u['today_target'],'Написать отчёт по продажам')
        self.assertNotIn('тревога уводит в быстрые награды','\n'.join(x[0] for x in m.answers))

    async def test_device_answer_preserves_reported_original_chain(self):
        initial=flows.safe_analysis_memory(CASE)
        initial.update(request_kind='task_problem',device_question_pending=True)
        u=await self.user(stage='request_device',analysis_json=json.dumps(initial,ensure_ascii=False))
        m=Message(self.uid,'И там и там')
        with patch.object(bot,'run_analysis',AsyncMock()) as analysis:
            await bot.handle_request_route(m,u,m.text)
            sent=analysis.await_args.args[2]
        self.assertIn('кофе',sent)
        self.assertIn('паниковать',sent)
        self.assertIn('на телефоне и на компьютере',sent)

    async def test_exact_rejection_and_questions_do_not_repeat_old_hypothesis(self):
        comp=flows.safe_analysis_memory(CASE)
        comp.update(specific_pattern='тревога уводит в быстрые награды',selected_skill='one_tab_focus',analysis_result={'pattern':'anxious_quick_rewards'})
        await self.user(stage='confirm_analysis',trainer_key='beck',analysis_json=json.dumps(comp,ensure_ascii=False))
        await self.say('😑 Ты меня не понял')
        m,u=await self.say('2. Слишком общий ответ')
        self.assertEqual(u['stage'],'reported_case_question')
        self.assertEqual(json.loads(u['analysis_json'])['case_question'],'immediate')
        m,u=await self.say('Поведение: ещё не уточнено\nКраткосрочный результат: ещё не уточнено\nОтсроченный результат: ещё не уточнено\nзадай вопросы')
        self.assertIn('Что менялось сразу',m.answers[-1][0])
        self.assertNotIn('быстрые награды',m.answers[-1][0])
        self.assertNotIn('Коротко, на что опираюсь',m.answers[-1][0])
        self.assertIn('кофе',json.loads(u['analysis_json'])['behavioral_chain']['behavior'])

    async def test_stale_action_does_not_become_a_fact_and_stop_is_available(self):
        await self.user(stage='await_problem_text',trainer_key='beck')
        await self.say(CASE)
        _,u=await self.say('💪 Давай действие')
        chain=json.loads(u['analysis_json'])['behavioral_chain']
        self.assertNotEqual(chain.get('task'),'💪 Давай действие')
        _,u=await self.say('стоп')
        self.assertEqual(u['stage'],'intent_paused')
        m,u=await self.say('Продолжить')
        self.assertEqual(u['stage'],'reported_case_question')
        self.assertIn('одно рабочее дело',m.answers[-1][0])

    async def test_details_do_not_become_the_task(self):
        await self.user(stage='await_problem_text',trainer_key='beck')
        await self.say(CASE)
        m,u=await self.say('📚 Подробнее')
        self.assertIn('кофе',m.answers[-1][0])
        self.assertNotEqual(json.loads(u['analysis_json'])['behavioral_chain'].get('task'),'📚 Подробнее')
        self.assertEqual(u['stage'],'reported_case_question')

    async def test_safety_still_precedes_the_question_route(self):
        await self.user(stage='await_problem_text',trainer_key='beck')
        await self.say(CASE)
        with patch.object(bot,'start_safety_interceptor',AsyncMock()) as safety:
            await bot.main_flow(Message(self.uid,'Хочу умереть'))
            safety.assert_awaited_once()
