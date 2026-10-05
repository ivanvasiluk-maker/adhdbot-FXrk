import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
import bot
from db import init_db, migrate_db, default_user, save_user, get_user, get_user_profile
from test_dialogue_ux_patch import Message

class ShortFeedbackJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=self.tmp.name+'/db';self.uid=55331
        await init_db(self.path);await migrate_db(self.path)
        self.p=patch.object(bot,'DB_PATH',self.path);self.p.start()
        u=default_user(self.uid);u.update(stage='training',daily_skill_id='open_only',current_task_title='отчёт',current_next_physical_step='открыть отчёт',current_day_id='55331:1',day_core_skill_id='open_only')
        bot.mark_action_card_active(u);await save_user(u,self.path)
    async def asyncTearDown(self):self.p.stop();self.tmp.cleanup()
    async def send(self,text):
        m=Message(self.uid,text)
        with patch.object(bot,'client',None):await bot.main_flow(m)
        return m,await get_user(self.uid,self.path)
    async def test_done_and_continued_finishes_in_one_choice(self):
        m,u=await self.send('✅ Сделал');self.assertEqual(len(m.answers),1)
        m,u=await self.send('🚀 Продолжил дело')
        self.assertEqual(u['stage'],'post_action_reflection');self.assertEqual(len(m.answers),1)
        p=await get_user_profile(self.uid,self.path);f=p['last_skill_feedback']
        self.assertTrue(f['completed']);self.assertTrue(f['continued_after_skill'])
        self.assertEqual(f['helpfulness'],'unknown')
        self.assertNotIn('уточним',m.answers[0][0].lower())
        m,u=await self.send('🧰 Мои рабочие навыки');self.assertIn('Помогли продолжить',m.answers[0][0])
        m,u=await self.send('🏁 Хватит на сегодня');self.assertEqual(u['stage'],'training_main')
    async def test_success_next_does_not_repeat_intake(self):
        await self.send('✅ Сделал');await self.send('🚀 Дело продолжилось')
        with patch.object(bot,'open_next_logical_step',AsyncMock()) as next_step:
            await self.send('⚡ Ещё один шаг');next_step.assert_awaited_once()
    async def test_closed_day_failure_adaptation_keeps_day_closed(self):
        u=await get_user(self.uid,self.path)
        u.update(stage='closed_day_voluntary_step',day_closed=1,today_closed=1,day_status='closed',last_day_closed_at=bot.local_date_for_user(u),closed_day_additional_active=1)
        await save_user(u,self.path)
        await self.send('❌ Не получилось');m,u=await self.send('❓ Неясно, что делать')
        self.assertEqual(u['stage'],'closed_day_voluntary_tiny');self.assertTrue(bot.day_closed_today(u))
        self.assertEqual(u['day_core_skill_id'],'open_only')
        m,u=await self.send('✅ Получилось сделать');self.assertEqual(u['stage'],'minimal_feedback_help')
        m,u=await self.send('🙂 Стало легче');self.assertEqual(u['stage'],'post_action_reflection')
        self.assertTrue(bot.day_closed_today(u))

    async def test_relief_does_not_imply_continuation(self):
        await self.send('✅ Сделал');m,u=await self.send('🙂 Стало легче')
        p=await get_user_profile(self.uid,self.path);f=p['last_skill_feedback']
        self.assertEqual(f['helpfulness'],'helped');self.assertIsNone(f['continued_after_skill'])
        self.assertNotIn(f['experiment_result'],{'STRONG_SUCCESS','WEAK_SUCCESS'})
        self.assertEqual(len(m.answers),1)
    async def test_partial_is_not_complete_or_continued(self):
        await self.send('🟡 Частично');m,u=await self.send('😐 Без изменений')
        p=await get_user_profile(self.uid,self.path);f=p['last_skill_feedback']
        self.assertTrue(f['partial']);self.assertFalse(f['completed']);self.assertIsNone(f['continued_after_skill'])
        self.assertEqual(len(m.answers),1);self.assertIn('частичное',m.answers[0][0])
    async def test_worse_is_saved_then_recovery_without_extra_question(self):
        await self.send('✅ Сделал');m,u=await self.send('😣 Стало хуже')
        self.assertEqual(len(m.answers),1);self.assertIn('остановим',m.answers[0][0])
        p=await get_user_profile(self.uid,self.path);self.assertIsNone(p['last_skill_feedback']['continued_after_skill'])
        self.assertTrue(bot.should_block_skill_for_repetition(u,p['last_skill_feedback']['skill_id']))
        m,u=await self.send('Уточнить, что стало хуже');self.assertEqual(u['stage'],'recovery_context')
    async def test_failure_adapts_without_analysis_and_preserves_failure(self):
        _,u=await self.send('❌ Не получилось');old=bot.active_attempt(u)['attempt_id']
        with patch.object(bot,'run_analysis',AsyncMock()) as analysis:
            m,u=await self.send('😵 Слишком сложно');analysis.assert_not_awaited()
        self.assertEqual(u['stage'],'downscale_action');self.assertEqual(len(m.answers),1)
        self.assertNotEqual(bot.active_attempt(u)['attempt_id'],old)
        self.assertEqual(bot.active_attempt(u)['effect_status'],'unknown')
        p=await get_user_profile(self.uid,self.path);self.assertFalse(p['last_skill_feedback']['completed'])
        self.assertEqual(p['last_skill_feedback']['helpfulness'],'unknown')
        self.assertEqual(u['day_core_skill_id'],'open_only')
    async def test_confusion_is_short_and_does_not_claim_execution(self):
        m,u=await self.send('я запуталась')
        self.assertIn('Сейчас только одно',m.answers[0][0]);self.assertLess(len(m.answers[0][0]),160)
        self.assertEqual(bot.active_attempt(u)['attempt_status'],'not_tried')
        self.assertEqual({b.text for row in m.answers[0][1]['reply_markup'].keyboard for b in row},{'✅ Получилось сделать','❌ Не получилось'})
    async def test_unknown_reply_does_not_record_result(self):
        await self.send('✅ Сделал');m,u=await self.send('всё хорошо')
        self.assertEqual(u['stage'],'minimal_feedback_help')
        p=await get_user_profile(self.uid,self.path);self.assertFalse(p.get('last_skill_feedback'))
    async def test_normalized_boundary_keeps_unknown_self_report(self):
        u=await get_user(self.uid,self.path)
        u['active_attempt']={'behavioral_experiment_id':1,'behavioral_experiment_revision':1,'current_skill_id':'open_only'}
        registry=SimpleNamespace(get=lambda sid: object())
        with patch.object(bot,'ACTIVE_FILE_SKILL_REGISTRY',None), patch.object(bot,'SKILL_REGISTRY',registry), patch.object(bot,'log_event',AsyncMock()), patch.object(bot,'process_experiment_outcome',AsyncMock(side_effect=RuntimeError('test boundary'))) as process:
            await bot._process_normalized_feedback(u,{'skill_id':'open_only','completed':True,'helpfulness':'unknown','continued_after_skill':True})
        process.assert_awaited_once()
        self.assertEqual(process.await_args.kwargs['outcome'].emotional_change,'unknown')

    async def test_old_partial_screen_remains_supported(self):
        u=await get_user(self.uid,self.path);u['stage']='feedback_partial_text'
        f=bot.minimal_feedback_base(u,source='partial');f.update(completed=False,partial=True);bot.set_minimal_feedback(u,f);await save_user(u,self.path)
        m,u=await self.send('Открыл файл, дальше остановился');self.assertEqual(u['stage'],'minimal_feedback_help')
        await self.send('🙂 Стало легче');p=await get_user_profile(self.uid,self.path)
        self.assertIn('Открыл файл',p['last_skill_feedback']['user_feedback'])
