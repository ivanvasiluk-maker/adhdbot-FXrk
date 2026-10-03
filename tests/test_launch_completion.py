import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import bot
from db import init_db, migrate_db, default_user, save_user, get_user, update_user_profile, get_user_profile
from test_dialogue_ux_patch import Message
from core.learning_engine import ExperimentEvidence, update_learning_model

class ConclusionTests(unittest.TestCase):
    def test_all_short_screens_share_conflicting_evidence(self):
        model = update_learning_model({}, ExperimentEvidence('open_only', True, 'helped', 'stopped_after_step'), day='2026-10-03')
        profile = {'learning_model':model, 'last_day_review':{'function':'start'},
                   'last_skill_feedback':{'completed':True,'helpfulness':'helped','continued_after_skill':False}}
        c = bot.daily_conclusion({}, profile)
        self.assertEqual(c['primary_bottleneck'], 'unclear')
        expected = bot.render_daily_conclusion(c)
        self.assertEqual(bot.short_daily_map_text(profile, {}, {}), expected)
        self.assertEqual(bot.day1_profile_card_text({}, profile, 1), expected)
        self.assertEqual(bot.offer_short_conclusion_text({}, {}, profile), expected)
        self.assertIn('дело не продолжилось', expected)
        self.assertIsNone(c['best_supported_skill'])

    def test_negative_skill_is_not_next_test_or_best(self):
        model = update_learning_model({}, ExperimentEvidence('one_tab_focus', True, 'helped', 'continued_target_task'), day='2026-10-03')
        model = update_learning_model(model, ExperimentEvidence('one_tab_focus', True, 'worse', 'stopped_after_step'), day='2026-10-03')
        c = bot.daily_conclusion({}, {'learning_model':model,'last_day_review':{'function':'stay'},'last_skill_feedback':{'helpfulness':'worse'}})
        self.assertIsNone(c['best_supported_skill'])
        self.assertIsNone(c['next_skill_id'])
        self.assertIn('не повторяем', c['next_test'])

    def test_prices_are_configured_and_no_guaranteed_month_package(self):
        self.assertEqual(bot.HUMAN_SKILL_SESSION_EUR_LABEL, '99')
        self.assertEqual(bot.GROUP_PROGRAM_PRICE_LABEL, '240')
        self.assertIn('от €99', bot.tariff_live_text())
        self.assertIn('согласуем до оплаты', bot.tariff_live_text())
        self.assertNotIn('€200', bot.short_offer_text())

class CompletionJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/test.db'
        await init_db(self.path)
        await migrate_db(self.path)
        self.pathpatch = patch.object(bot,'DB_PATH',self.path)
        self.pathpatch.start()
        self.uid = 77331
        u = default_user(self.uid)
        u.update(stage='training',daily_skill_id='open_only',current_task_title='написать клиенту',current_day_id='77331:1')
        bot.mark_action_card_active(u)
        await save_user(u,self.path)

    async def asyncTearDown(self):
        self.pathpatch.stop()
        self.tmp.cleanup()

    async def send(self,text):
        m = Message(uid=self.uid,text=text)
        with patch.object(bot,'client',None):
            await bot.main_flow(m)
        return m, await get_user(self.uid,self.path)

    async def test_worse_has_immediate_recovery_and_no_same_skill(self):
        await self.send('✅ Сделал')
        m,u = await self.send('Стало хуже')
        self.assertIn('остановим', '\n'.join(x[0] for x in m.answers))
        labels = [b.text for row in m.answers[-1][1]['reply_markup'].keyboard for b in row]
        self.assertIn('Уточнить, что стало хуже',labels)
        _,u = await self.send('Уточнить, что стало хуже')
        self.assertEqual(u['stage'],'recovery_context')
        p = await get_user_profile(self.uid,self.path)
        self.assertEqual(p['last_skill_feedback']['helpfulness'],'worse')
        self.assertIsNone(p['last_skill_feedback']['continued_after_skill'])
        _,u = await self.send('Не сейчас')
        self.assertEqual(u['stage'],'training_main')

    async def test_recovery_uses_new_state_facts_not_failed_task(self):
        await self.send('✅ Сделал')
        await self.send('Стало хуже')
        await self.send('Уточнить, что стало хуже')
        _,u = await self.send('Усилилась тревога, чувствую сильное напряжение')
        self.assertIn(u['stage'],{'request_support','request_context'})
        self.assertNotEqual(u['stage'],'training')

    async def test_partial_waits_for_description_and_retains_it(self):
        _,u = await self.send('🟡 Частично')
        self.assertEqual(u['stage'],'feedback_partial_text')
        _,u = await self.send('Открыл файл, но на первой строке остановился')
        self.assertEqual(u['stage'],'minimal_feedback_help')
        await self.send('Не помогло')
        await self.send('Остановился после шага')
        p = await get_user_profile(self.uid,self.path)
        self.assertTrue(p['last_skill_feedback']['partial'])
        self.assertFalse(p['last_skill_feedback']['completed'])
        self.assertIn('первой строке', p['last_skill_feedback']['user_feedback'])

    async def test_offer_restores_exact_pending_question_after_reload(self):
        await self.send('✅ Сделал')
        u = await get_user(self.uid,self.path)
        original = bot.dialogue_context(u['dialogue_context'])
        original_attempt = dict(bot.active_attempt(u))
        original_state = u['current_state']
        with patch.object(bot,'build_skill_map_data',AsyncMock(return_value={})),patch.object(bot,'build_profile_map_summary',return_value={}):
            await bot.show_day3_offer(bot.dialogue_message(Message(uid=self.uid),u),u,'test',mode='manual')
        u = await get_user(self.uid,self.path)
        self.assertEqual(u['stage'],bot.OFFER_MENU_STAGE)
        m = Message(uid=self.uid)
        await bot.leave_offer_menu(m,u,'test_decline')
        u = await get_user(self.uid,self.path)
        self.assertEqual(u['stage'],'minimal_feedback_help')
        self.assertEqual(u['current_state'],original_state)
        self.assertEqual(bot.active_attempt(u),original_attempt)
        self.assertIn(original['text'],m.answers[-1][0])
        _,u = await self.send('Помогло')
        self.assertEqual(u['stage'],'minimal_feedback_next')

    async def test_day1_offer_does_not_own_pending_dialogue_or_repeat_day2(self):
        await self.send('✅ Сделал')
        await self.send('Помогло')
        m,u = await self.send('Продолжил задачу')
        self.assertTrue(any('Если такой формат помогает' in x[0] for x in m.answers))
        self.assertEqual(u['stage'],'post_action_reflection')
        self.assertIn('Предлагаю следующий шаг',bot.dialogue_context(u['dialogue_context'])['text'])
        _,u = await self.send('Не сейчас')
        self.assertEqual(u['stage'],'training_main')
        u['day']=2
        await save_user(u,self.path)
        self.assertFalse(await bot.maybe_show_help_offer(Message(uid=self.uid),u))

    async def test_final_summary_before_offer(self):
        u = await get_user(self.uid,self.path)
        u['plan_json']=json.dumps(['open_only'])
        await update_user_profile(self.uid, {'last_skill_feedback':{'completed':True,'helpfulness':'helped','continued_after_skill':True}},self.path)
        m = Message(uid=self.uid)
        await bot.finalize_day_review(bot.dialogue_message(m,u),u,{'function':'stay','barrier':'отвлечения'},'test')
        texts=[x[0] for x in m.answers]
        final=next(i for i,x in enumerate(texts) if 'Итог программы' in x)
        offer=next(i for i,x in enumerate(texts) if 'Если такой формат помогает' in x)
        self.assertLess(final,offer)
        self.assertIn('Самостоятельно:',texts[final])

    async def test_day3_summary_uses_feedback_before_support_offer(self):
        u = await get_user(self.uid,self.path)
        u['day']=3
        u['first_start_date']=(bot.dt.date.fromisoformat(bot.local_date_for_user(u))-bot.dt.timedelta(days=2)).isoformat()
        u['plan_json']=json.dumps(['open_only']*28)
        await save_user(u,self.path)
        await update_user_profile(self.uid, {'last_skill_feedback':{'completed':True,'helpfulness':'some','continued_after_skill':False}}, self.path)
        u = await get_user(self.uid,self.path)
        m = Message(uid=self.uid)
        with patch.object(bot,'sync_calendar_day',return_value=3):
            await bot.finalize_day_review(bot.dialogue_message(m,u),u,{'function':'stay','barrier':'отвлечения'},'test')
        texts = [x[0] for x in m.answers]
        self.assertIn('дело не продолжилось',texts[0])
        self.assertTrue(any('Группа' in x and 'от €99' in x for x in texts[1:]))
        self.assertFalse(any('устойчиво изменить' in x for x in texts))
        _,u = await self.send('📚 Подробнее')
        self.assertEqual(u['day'],3)

    async def test_direct_invitation_click_back_restores_pending_feedback(self):
        from types import SimpleNamespace
        await self.send('✅ Сделал')
        before = await get_user(self.uid,self.path)
        c = SimpleNamespace(data=bot.OFFER_CALLBACKS['group'],from_user=SimpleNamespace(id=self.uid),message=Message(uid=self.uid),answer=AsyncMock(),id='test_group_callback')
        await bot.on_offer_callbacks(c)
        u = await get_user(self.uid,self.path)
        self.assertEqual(u['stage'],bot.OFFER_MENU_STAGE)
        c = SimpleNamespace(data=bot.OFFER_CALLBACKS['back'],from_user=SimpleNamespace(id=self.uid),message=Message(uid=self.uid),answer=AsyncMock(),id='test_back_callback')
        await bot.on_offer_callbacks(c)
        u = await get_user(self.uid,self.path)
        self.assertEqual(u['stage'],before['stage'])
        self.assertEqual(u['pending_feedback_json'],before['pending_feedback_json'])
        self.assertIn('Насколько это помогло?',c.message.answers[-1][0])
