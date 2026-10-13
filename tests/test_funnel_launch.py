"""PATCH-68: sales modal, four-button navigation and factual summaries."""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import test_launch_fixes_20261011 as fixtures
from test_dialogue_ux_patch import Message
import bot
from db import get_user
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from core.lead_email import email_configured, send_lead_email

class Funnel(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.LaunchJourneys.asyncSetUp
    asyncTearDown=fixtures.LaunchJourneys.asyncTearDown
    user=fixtures.LaunchJourneys.user

    async def test_large_reply_menu_is_complete_across_pages(self):
        u=await self.user(stage='training_main',today_target='Маркетинг')
        labels=['Вариант '+str(i) for i in range(8)]
        m=Message(self.uid)
        await bot.dialogue_message(m,u).answer('Выберите вариант',reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x)] for x in labels],resize_keyboard=True))
        seen=set(b.text for row in m.answers[-1][1]['reply_markup'].keyboard for b in row)
        for _ in range(2):
            n=Message(self.uid,'➡️ Ещё варианты')
            await bot.main_flow(n)
            markup=n.answers[-1][1]['reply_markup']
            self.assertLessEqual(sum(map(len,markup.keyboard)),4)
            seen.update(b.text for row in markup.keyboard for b in row)
        self.assertTrue(set(labels)<=seen)
        self.assertEqual((await get_user(self.uid,bot.DB_PATH))['stage'],'training_main')

    async def test_offer_owns_stale_training_and_start_messages(self):
        for text in ('✅ Получилось сделать','Мне скучно','/start'):
            await self.user(stage=bot.OFFER_MENU_STAGE,today_target='Маркетинг')
            with patch.object(bot,'run_analysis',AsyncMock()) as analyze:
                m=Message(self.uid,text);await bot.main_flow(m)
                analyze.assert_not_awaited()
                self.assertIn('Сейчас открыты',m.answers[-1][0])
            self.assertEqual((await get_user(self.uid,bot.DB_PATH))['stage'],bot.OFFER_MENU_STAGE)

    async def test_old_training_callback_cannot_close_offer(self):
        u=await self.user(stage=bot.OFFER_MENU_STAGE)
        c=SimpleNamespace(answer=AsyncMock())
        self.assertFalse(await bot.claim_callback_once(c,u,'training'))
        c.answer.assert_awaited_once()

    def test_commercial_menus_have_four_buttons_and_no_direct_dm(self):
        for factory in (bot.offer_inline_keyboard,bot.offer_details_inline_keyboard,bot.tariff_group_inline_keyboard,bot.tariff_live_inline_keyboard):
            markup=factory(self.uid)
            buttons=[b for row in markup.inline_keyboard for b in row]
            self.assertLessEqual(len(buttons),4)
            self.assertFalse(any(b.url for b in buttons))
            self.assertTrue(any('бесплатно' in b.text.lower() for b in buttons))

    def test_task_label_excludes_barrier_but_source_is_untouched(self):
        text='Мне нужно сегодня заниматься маркетингом, вместо этого я лежу и не могу себя заставить'
        result=bot.extract_task_context_from_text(text)
        self.assertEqual(result['current_task_name'],'сегодня заниматься маркетингом')
        self.assertNotIn('лежу',bot.current_task_title({'today_target':text}))
        self.assertIn('лежу',text)

    def test_recorded_task_progress_is_visible_without_inventing_relief(self):
        u={'profile_json':{'last_skill_feedback':{'skill_id':'visible_next_step','completed':True,'continued_after_skill':True,'helpfulness':None}}}
        conclusion=bot.daily_conclusion(u,u['profile_json'])
        self.assertIn('помог продолжить',conclusion['helpful'])
        self.assertNotIn('стало легче',conclusion['helpful'])

    def test_individual_group_and_consent_are_clear(self):
        self.assertIn('Индивидуальный тренинг',bot.tariff_live_text())
        self.assertIn('утренние и вечерние',bot.tariff_group_text())
        self.assertIn('соглашаетесь',bot.offer_request_form_text('Индивидуально'))
        self.assertIn('после отдельного согласования',bot.offer_request_form_text('Подбор коллеги'))

    async def test_email_is_disabled_until_exact_configured_recipient(self):
        with patch.dict(os.environ,{'LEAD_EMAIL_TO':'','LEAD_EMAIL_FROM':'','LEAD_SMTP_HOST':''}):
            self.assertFalse(email_configured())
            with self.assertRaises(RuntimeError):
                await send_lead_email('Заявка')
        with patch.dict(os.environ,{'LEAD_EMAIL_TO':'ivan@example.com\nBcc:other@example.com','LEAD_EMAIL_FROM':'bot@example.com','LEAD_SMTP_HOST':'smtp.example.com'}):
            self.assertFalse(email_configured())

    async def test_email_copy_failure_does_not_repeat_telegram(self):
        u=await self.user(stage=bot.OFFER_MENU_STAGE,dialogue_context=json.dumps({'application_request_id':'case-68'}))
        m=Message(self.uid);m.bot=SimpleNamespace(send_message=AsyncMock())
        with patch('core.lead_email.email_configured',return_value=True),patch('core.lead_email.send_lead_email',AsyncMock(side_effect=TimeoutError)) as email:
            self.assertTrue(await bot.notify_offer_request(m,u,'group','Тренинг'))
            self.assertTrue(await bot.notify_offer_request(m,u,'group','Тренинг'))
            m.bot.send_message.assert_awaited_once();email.assert_awaited_once()

    async def test_email_acceptance_is_success_when_telegram_fails_without_retry(self):
        import aiosqlite
        u=await self.user(stage=bot.OFFER_MENU_STAGE,dialogue_context=json.dumps({'application_request_id':'email-fallback-68'}))
        m=Message(self.uid);m.bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError))
        with patch('core.lead_email.email_configured',return_value=True),patch('core.lead_email.send_lead_email',AsyncMock()) as email:
            self.assertTrue(await bot.notify_offer_request(m,u,'group','СДВГ, вечер'))
            self.assertTrue(await bot.notify_offer_request(m,u,'group','СДВГ, вечер'))
            m.bot.send_message.assert_awaited_once()
            email.assert_awaited_once()
        async with aiosqlite.connect(bot.DB_PATH) as db:
            rows=await (await db.execute('SELECT lead_id,delivery_status FROM application_receipts ORDER BY lead_id')).fetchall()
        self.assertEqual(rows,[('email-fallback-68','unconfirmed'),('email-fallback-68:email','sent')])

    async def test_both_delivery_failures_do_not_claim_success(self):
        u=await self.user(stage=bot.OFFER_MENU_STAGE,dialogue_context=json.dumps({'application_request_id':'both-fail-68'}))
        m=Message(self.uid);m.bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError))
        with patch('core.lead_email.email_configured',return_value=True),patch('core.lead_email.send_lead_email',AsyncMock(side_effect=TimeoutError)) as email:
            for _ in range(2):
                sent=await bot.notify_offer_request(m,u,'group','СДВГ, вечер')
                self.assertFalse(sent)
                self.assertIn('не подтверждена',bot.offer_request_submitted_text(sent))
            m.bot.send_message.assert_awaited_once()
            email.assert_awaited_once()

    async def test_optional_step_gets_live_attempt_without_reopening_day(self):
        today=bot.local_date_for_user({'timezone':'Europe/Vilnius'})
        u=await self.user(stage='day_core_stop',day_closed=1,today_closed=1,day_status='closed',last_day_closed_at=today+'T10:00:00',current_skill='visible_next_step',today_target='Маркетинг',active_attempt={'is_closed':True,'day_closed':True,'attempt_id':'old'})
        m=Message(self.uid)
        await bot.open_closed_day_voluntary_step(m,u)
        self.assertFalse(bot.active_attempt(u)['is_closed'])
        self.assertNotEqual(bot.active_attempt(u)['attempt_id'],'old')
        self.assertTrue(bot.day_closed_today(u))

    def test_no_reported_slip_or_self_criticism_cannot_select_them(self):
        u={'today_target':'Написать пост','current_task_title':'Написать пост'}
        for sid in ('restart_after_slip','self_criticism_to_instruction'):
            self.assertNotEqual(bot._first_non_repeating_skill_id(u,[sid],[]),sid)

    async def test_large_inline_menu_pages_preserve_callbacks(self):
        from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton
        u=await self.user(stage='training_main')
        m=Message(self.uid);m.edit_text=m.answer
        markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i),callback_data='original:'+str(i))] for i in range(7)])
        await bot.dialogue_message(m,u).answer('Выберите',reply_markup=markup)
        seen=set()
        for _ in range(3):
            current=m.answers[-1][1]['reply_markup']
            buttons=[b for row in current.inline_keyboard for b in row]
            self.assertLessEqual(len(buttons),4)
            seen.update(b.callback_data for b in buttons)
            callback=SimpleNamespace(from_user=m.from_user,data=buttons[-1].callback_data,message=m,answer=AsyncMock())
            await bot.on_menu_page(callback)
        self.assertTrue({'original:'+str(i) for i in range(7)}<=seen)

    async def test_action_router_and_fsm_cannot_abandon_offer(self):
        await self.user(stage=bot.OFFER_MENU_STAGE)
        for handler in (bot.on_skiller_action_callback,bot.on_flow_state_callback):
            c=SimpleNamespace(from_user=SimpleNamespace(id=self.uid),data='experiment.result.done',answer=AsyncMock())
            await handler(c)
            self.assertEqual((await get_user(self.uid,bot.DB_PATH))['stage'],bot.OFFER_MENU_STAGE)

    async def test_application_start_requires_explicit_exit(self):
        await self.user(stage='offer_request_form')
        m=Message(self.uid,'/start');await bot.main_flow(m)
        self.assertEqual((await get_user(self.uid,bot.DB_PATH))['stage'],'offer_request_form')
        self.assertIn('/cancel',m.answers[-1][0])

    def test_marketing_case_chain_preserves_reported_apathy_and_distinguishes_action(self):
        from core.experiment_dialogue import reported_chain
        text='Мне нужно сегодня заниматься маркетингом, вместо этого я лежу и не могу себя заставить, у меня какая-то апатия. Я постоянно отвлекаюсь.'
        chain=reported_chain(text)
        self.assertIn('маркетингом',chain['situation'])
        self.assertNotIn('лежу',chain['situation'])
        self.assertIn('апатия',chain['feelings'])
        self.assertEqual(chain['behavior'],'я лежу')
        self.assertIsNone(chain['immediate'])
        self.assertIsNone(chain['thoughts'])

    async def test_support_button_opens_modal_and_removes_training_keyboard(self):
        from aiogram.types import ReplyKeyboardRemove
        await self.user(stage='training_main',has_started_training=1,today_target='Маркетинг')
        m=Message(self.uid,'🤝 Поддержка психолога')
        await bot.main_flow(m)
        self.assertTrue(any(isinstance(kwargs.get('reply_markup'),ReplyKeyboardRemove) for _,kwargs in m.answers))
        self.assertEqual((await get_user(self.uid,bot.DB_PATH))['stage'],bot.OFFER_MENU_STAGE)
        self.assertLessEqual(sum(len(row) for row in m.answers[-1][1]['reply_markup'].inline_keyboard),4)

    async def test_https_email_uses_exact_recipient_and_request_identity(self):
        from unittest.mock import MagicMock,Mock
        response=SimpleNamespace(raise_for_status=Mock(),json=AsyncMock(return_value={'id':'accepted-email'}))
        request=MagicMock();request.__aenter__=AsyncMock(return_value=response);request.__aexit__=AsyncMock(return_value=False)
        client=MagicMock();client.post.return_value=request
        session=MagicMock();session.__aenter__=AsyncMock(return_value=client);session.__aexit__=AsyncMock(return_value=False)
        values={'LEAD_EMAIL_PROVIDER':'resend','RESEND_API_KEY':'unit-test-placeholder','LEAD_EMAIL_TO':'owner@example.com','LEAD_EMAIL_FROM':'bot@example.com'}
        with patch.dict(os.environ,values),patch('core.lead_email.aiohttp.ClientSession',return_value=session):
            self.assertTrue(email_configured())
            await send_lead_email('Заявка',idempotency_key='lead-68')
        args=client.post.call_args
        self.assertEqual(args.args[0],'https://api.resend.com/emails')
        self.assertEqual(args.kwargs['json']['to'],['owner@example.com'])
        self.assertEqual(args.kwargs['headers']['Idempotency-Key'],'skiller-lead-lead-68')
        self.assertFalse(args.kwargs['allow_redirects'])

    async def test_email_provider_must_confirm_acceptance(self):
        from unittest.mock import MagicMock,Mock
        response=SimpleNamespace(raise_for_status=Mock(),json=AsyncMock(return_value={}))
        request=MagicMock();request.__aenter__=AsyncMock(return_value=response);request.__aexit__=AsyncMock(return_value=False)
        client=MagicMock();client.post.return_value=request
        session=MagicMock();session.__aenter__=AsyncMock(return_value=client);session.__aexit__=AsyncMock(return_value=False)
        values={'LEAD_EMAIL_PROVIDER':'resend','RESEND_API_KEY':'unit-test-placeholder','LEAD_EMAIL_TO':'owner@example.com','LEAD_EMAIL_FROM':'bot@example.com'}
        with patch.dict(os.environ,values),patch('core.lead_email.aiohttp.ClientSession',return_value=session):
            with self.assertRaises(RuntimeError):
                await send_lead_email('Заявка')
