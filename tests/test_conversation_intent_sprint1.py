import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import bot
from core.conversation_intent import resolve_intent
from db import default_user, init_db, migrate_db, save_user, get_user
from test_dialogue_ux_patch import Message

class IntentTests(unittest.TestCase):
    def test_explicit_mode_and_unknown(self):
        for text, expected in [('Почему я всегда откладываю отчёт?', 'UNDERSTAND'), ('Помоги мне сейчас начать отчёт.', 'ACT_NOW'), ('Мне надо сейчас начать отчёт', 'ACT_NOW'), ('Нужна помощь', 'CHOOSE_MODE'), ('стоп', 'STOP'), ('Продолжить', 'CONTINUE'), ('Что произошло вчера в офисе', 'OTHER')]:
            self.assertEqual(resolve_intent(text, 'training_main'), expected, text)

    def test_pending_input_outranks_words(self):
        self.assertEqual(resolve_intent('Почему мне тяжело', 'offer_request_form'), 'APPLICATION_DATA')
        self.assertEqual(resolve_intent('Иван Василюк', 'offer_request_form'), 'APPLICATION_DATA')
        self.assertEqual(resolve_intent('всё хорошо', 'minimal_feedback_help'), 'FEEDBACK_REPLY')
        self.assertEqual(resolve_intent('Почему не получилось', 'feedback_partial_text'), 'FEEDBACK_REPLY')
        self.assertEqual(resolve_intent('хочу понять', 'ask_name'), 'PENDING_REPLY')
        self.assertEqual(resolve_intent('стоп', 'minimal_feedback_next'), 'STOP')

class IntentJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=self.tmp.name+'/db'
        await init_db(self.path);await migrate_db(self.path)
        self.patcher=patch.object(bot,'DB_PATH',self.path);self.patcher.start()
        u=default_user(81819);u.update(stage='training_main',name='Иван',username='ivan_test',current_action_id='keep_action')
        await save_user(u,self.path)
    async def asyncTearDown(self):
        self.patcher.stop();self.tmp.cleanup()
    async def send(self,text):
        m=Message(uid=81819,text=text);m.from_user.username="ivan_test"
        with patch.object(bot,'client',None):await bot.main_flow(m)
        return m,await get_user(81819,self.path)
    async def test_understand_has_no_exercise_until_selected(self):
        with patch.object(bot,'handle_action_request',AsyncMock()) as action:
            m,u=await self.send('Почему я постоянно откладываю отчёт, хотя хочу начать работу?')
            action.assert_not_awaited()
        self.assertEqual(u['stage'],'intent_understand_result')
        self.assertIn('Моя рабочая версия',m.answers[-1][0])
        self.assertIn('Мне достаточно',str(m.answers[-1][1]))
        self.assertEqual(u['current_action_id'],'keep_action')
    async def test_understand_missing_case_waits(self):
        m,u=await self.send('Почему я всегда так делаю?')
        self.assertEqual(u['stage'],'intent_understand_context')
        m,u=await self.send('Перед большим отчётом мне трудно начать и я откладываю работу')
        self.assertEqual(u['stage'],'intent_understand_result')
    async def test_act_now_uses_case_without_mode_question(self):
        with patch.object(bot,'run_analysis',AsyncMock()) as analysis:
            m,u=await self.send('Помоги мне сейчас начать отчёт.')
            analysis.assert_awaited_once()
            self.assertEqual(analysis.await_args.args[2],'Помоги мне сейчас начать отчёт.')
    async def test_application_bypasses_router_and_details(self):
        u=await get_user(81819,self.path);u['stage']='offer_request_form';await save_user(u,self.path)
        with patch.object(bot,'notify_offer_request',AsyncMock(return_value=True)) as notify,patch.object(bot,'run_analysis',AsyncMock()) as analysis:
            m,u=await self.send('Почему я откладываю работу, хочу записаться')
            notify.assert_awaited_once();analysis.assert_not_awaited()
        self.assertEqual(u['last_offer_action'],'offer_request_submitted')
        self.assertIn('Почему я откладываю',notify.await_args.args[3])
    async def test_feedback_is_not_success_from_everything_fine(self):
        u=await get_user(81819,self.path);u['stage']='minimal_feedback_help';await save_user(u,self.path)
        m,u=await self.send('всё хорошо')
        self.assertEqual(u['stage'],'minimal_feedback_help')
        self.assertIn('Что получилось после шага',m.answers[-1][0])
    async def test_pause_reload_resume_exact_pending_question(self):
        u=await get_user(81819,self.path);u['stage']='minimal_feedback_next'
        u['pending_feedback_json']=json.dumps({'completed':True,'helpfulness':'helped'})
        u['dialogue_context']=json.dumps({'text':'Что произошло дальше?', 'stage':'minimal_feedback_next', 'markup':bot.kb_minimal_feedback_next.model_dump(mode='json'),'inline':False})
        await save_user(u,self.path)
        m,paused=await self.send('стоп');self.assertEqual(paused['stage'],'intent_paused')
        m,resumed=await self.send('Продолжить');self.assertEqual(resumed['stage'],'minimal_feedback_next')
        self.assertEqual(resumed['pending_feedback_json'],u['pending_feedback_json'])
        self.assertEqual(resumed['current_action_id'],'keep_action')
        self.assertEqual(m.answers[-1][0],'Что произошло дальше?')
    async def test_ambiguous_help_asks_only_mode(self):
        m,u=await self.send('Помоги мне');self.assertEqual(u['stage'],'intent_choose')
        self.assertIn('Что сейчас полезнее?',m.answers[-1][0])
