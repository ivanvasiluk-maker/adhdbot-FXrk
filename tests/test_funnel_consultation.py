import json
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import aiosqlite
import bot
from core.consultation import consultation_url, DEFAULT_CONSULTATION_URL
from db import get_user, get_user_profile, update_user_profile
import test_launch_fixes_20261011 as fixtures
from test_commercial_sprint12 import seed_useful
from test_dialogue_ux_patch import Message

class FunnelConsultation(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.LaunchJourneys.asyncSetUp
    asyncTearDown=fixtures.LaunchJourneys.asyncTearDown
    user=fixtures.LaunchJourneys.user

    async def seed(self):
        u=await self.user(stage='post_action_reflection',address_mode='ty',address_form='male',today_target='Делать бот',current_task_title='Делать бот',current_state='paused',dialogue_context=json.dumps({'text':'Результат сохранён','markup':{'remove_keyboard':True},'stage':'post_action_reflection'}))
        await seed_useful(self.path,u)
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM attempt_evidence WHERE action_id='seed2'")
            await db.commit()
        await update_user_profile(self.uid,{'last_skill_feedback':{'skill_id':'open_only','completed':True,'continued_after_skill':True,'helpfulness':None}},self.path)
        return await get_user(self.uid,self.path)

    async def callback(self,text,m):
        c=SimpleNamespace(id=uuid.uuid4().hex,from_user=SimpleNamespace(id=self.uid,username='test_user',first_name='Иван'),message=m,data=text,answer=AsyncMock())
        await bot.on_offer_callbacks(c)
        return await get_user(self.uid,self.path)

    def test_booking_is_exact_and_no_claim_about_price(self):
        with patch.dict(os.environ,{},clear=False):
            self.assertTrue(DEFAULT_CONSULTATION_URL.startswith('https://cal.com/'))
        buttons=[b for row in bot.tariff_live_inline_keyboard(self.uid).inline_keyboard for b in row]
        self.assertEqual([b.url for b in buttons if b.url],[consultation_url()])
        self.assertLessEqual(len(buttons),4)
        self.assertIn('Индивидуальный тренинг',bot.tariff_live_text())
        self.assertNotIn('€99',bot.tariff_live_text())
        self.assertNotIn('бесплатная консультация',bot.tariff_live_text().lower())
        self.assertTrue(any('бесплатно' in b.text for b in buttons))
        for invalid in ('http://cal.com/test','https://evil.example/test','https://cal.com@evil.example/test'):
            with patch.dict(os.environ,{'CONSULTATION_URL':invalid}):
                self.assertEqual(consultation_url(),'')

    def test_offer_is_short_observational_and_task_specific(self):
        u={'today_target':'Делать бот'}
        p={'last_skill_feedback':{'continued_after_skill':True,'helpfulness':None}}
        text=bot.offer_screen_text(u,{},p)
        self.assertIn('Делать бот',text)
        self.assertNotIn('Где пока труднее',text)
        self.assertNotIn('Причину ещё уточняем',text)
        self.assertNotIn('помог',text)
        self.assertLess(len(text),1300)
        self.assertIn('Бот остаётся бесплатным',text)

    async def test_first_result_invites_without_closing_or_hijacking_training(self):
        u=await self.seed();before=u['dialogue_context'];m=Message(self.uid)
        self.assertTrue(await bot.maybe_show_help_offer(m,u,substantive=True))
        self.assertEqual(u['stage'],'post_action_reflection')
        self.assertEqual(u['dialogue_context'],before)
        self.assertFalse(bot.day_closed_today(u))
        buttons=[b for row in m.answers[-1][1]['reply_markup'].inline_keyboard for b in row]
        self.assertLessEqual(len(buttons),4)
        self.assertTrue(any(b.url==consultation_url() for b in buttons))
        self.assertTrue(any('бесплатно' in b.text for b in buttons))
        self.assertIn('Делать бот',m.answers[-1][0])
        self.assertNotIn('Вы отметили',m.answers[-1][0])
        self.assertIn('ты отметил',m.answers[-1][0].lower())
        self.assertFalse(await bot.maybe_show_help_offer(m,u,substantive=True))

    async def test_actual_first_attempt_result_reaches_invitation(self):
        u=await self.user(stage='training',address_mode='ty',address_form='male',today_target='Делать бот',current_task_title='Делать бот',daily_skill_id='open_only',current_day_id=f'{self.uid}:1')
        bot.mark_action_card_active(u)
        from db import save_user
        await save_user(u,self.path)
        for text in ('✅ Сделал','🚀 Дело продолжилось'):
            m=Message(self.uid,text);await bot.main_flow(m)
        self.assertTrue(any(consultation_url() in str(kwargs.get('reply_markup')) for _,kwargs in m.answers))
        self.assertEqual((await get_user(self.uid,self.path))['stage'],'post_action_reflection')

    async def test_limit_menu_paginates_and_voluntary_step_still_opens(self):
        u=await self.user(stage='post_action_reflection',today_target='Делать бот')
        with patch.object(bot,'get_honest_day_counts',AsyncMock(return_value={'attempts_today':2,'completed_actions_today':2,'continued_actions_today':2})):
            m=Message(self.uid);await bot.open_next_logical_step(m,u)
        self.assertEqual(u['stage'],'day_core_stop')
        pager_before=json.loads(u['dialogue_context'])['keyboard_pages']
        m=Message(self.uid,'➡️ Ещё варианты');await bot.main_flow(m)
        restored=await get_user(self.uid,self.path)
        pager_after=json.loads(restored['dialogue_context'])['keyboard_pages']
        self.assertNotEqual(pager_before['page'],pager_after['page'])
        m=Message(self.uid,'⚡ Дать короткий навык');await bot.main_flow(m)
        restored=await get_user(self.uid,self.path)
        self.assertEqual(restored['stage'],'closed_day_voluntary_step')
        self.assertEqual(restored['today_target'],'Делать бот')

    async def test_decline_keeps_current_task_and_suppresses_invites(self):
        u=await self.seed();m=Message(self.uid)
        await bot.maybe_show_help_offer(m,u,substantive=True)
        u=await self.callback(bot.OFFER_CALLBACKS['choose_later'],m)
        self.assertEqual(u['stage'],'post_action_reflection')
        self.assertEqual(u['today_target'],'Делать бот')
        p=await get_user_profile(self.uid,self.path)
        self.assertIn('offer_declined_at',p)
        self.assertFalse(await bot.maybe_show_help_offer(m,u,substantive=True))

    async def test_live_calendar_request_receipt_and_free_return(self):
        u=await self.seed();m=Message(self.uid)
        await bot.maybe_show_help_offer(m,u,substantive=True)
        u=await self.callback(bot.OFFER_CALLBACKS['live'],m)
        self.assertEqual(u['stage'],bot.OFFER_MENU_STAGE)
        buttons=[b for row in m.answers[-1][1]['reply_markup'].inline_keyboard for b in row]
        self.assertIn(consultation_url(),[b.url for b in buttons])
        u=await self.callback(bot.OFFER_CALLBACKS['request_live'],m)
        self.assertEqual(u['stage'],'offer_request_form')
        sent=Message(self.uid,'Хочу обсудить прокрастинацию. Контакт: @test_user. Удобно вечером, Вильнюс.')
        sent.bot=SimpleNamespace(send_message=AsyncMock())
        with patch('core.lead_email.email_configured',return_value=False):
            await bot.main_flow(sent)
        sent.bot.send_message.assert_awaited_once()
        self.assertIn('отправил заявку',sent.answers[-1][0])
        async with aiosqlite.connect(self.path) as db:
            row=await (await db.execute("SELECT delivery_status FROM application_receipts")).fetchone()
        self.assertEqual(row[0],'sent')
        u=await self.callback(bot.OFFER_CALLBACKS['continue_training'],m)
        self.assertEqual(u['stage'],'post_action_reflection')
        self.assertEqual(u['today_target'],'Делать бот')
        self.assertTrue(m.answers[-1][1]['reply_markup'].remove_keyboard)

    async def test_failure_does_not_auto_sell(self):
        u=await self.seed()
        await update_user_profile(self.uid,{'last_skill_feedback':{'completed':False,'helpfulness':'not_helped','continued_after_skill':False}},self.path)
        m=Message(self.uid)
        self.assertFalse(await bot.maybe_show_help_offer(m,u,substantive=True))
        self.assertFalse(m.answers)

    async def test_task_recovers_only_from_current_case_and_self_continue_exits(self):
        self.assertEqual(bot.current_task_title({'analysis_json':json.dumps({'behavioral_chain':{'task':'Делать бот'}})}),'Делать бот')
        self.assertEqual(bot.current_task_title({'current_task_title':'Другое дело','analysis_json':json.dumps({'behavioral_chain':{'task':'Старая задача'}})}),'Другое дело')
        await self.user(stage='post_action_reflection',today_target='Делать бот')
        await bot.main_flow(Message(self.uid,'Продолжить задачу самому'))
        self.assertEqual((await get_user(self.uid,self.path))['stage'],'day_menu')

    async def test_help_choose_shows_options_instead_of_repeating_question(self):
        comp={'case_mode':'brief','case_question':'brief_task','behavioral_chain':{}}
        await self.user(stage='reported_case_question',analysis_json=json.dumps(comp))
        for text in ('🎯 Помоги выбрать дело','Написать пост;Открыть проект бота'):
            m=Message(self.uid,text);await bot.main_flow(m)
        u=await get_user(self.uid,self.path);c=json.loads(u['analysis_json'])
        self.assertEqual(c['case_question'],'brief_task_pick')
        option=next(label for label,value in c['task_options'].items() if value=='Открыть проект бота')
        await bot.main_flow(Message(self.uid,option))
        self.assertEqual((await get_user(self.uid,self.path))['today_target'],'Открыть проект бота')
