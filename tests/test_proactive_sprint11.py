import asyncio
import datetime as dt
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import bot
from core.proactive import intention, due, protected
from db import default_user, init_db, migrate_db, save_user, get_user, get_user_profile
from test_dialogue_ux_patch import Message

class ProactiveRules(unittest.TestCase):
    def setUp(self): self.u = dict(stage='training', timezone='Europe/Vilnius'); self.now = dt.datetime(2026,10,6,8,tzinfo=dt.timezone.utc)
    def test_explicit_time(self):
        p = intention('Напомни в 14:30 открыть отчёт', self.u, self.now)
        self.assertEqual(p['planned_action'], 'открыть отчёт'); self.assertEqual(p['status'], 'draft')
    def test_context_without_explicit_remind_is_only_draft(self):
        p = intention('После обеда сделаю письмо', self.u, self.now)
        self.assertEqual(p['approx_time'], '13:00'); self.assertEqual(p['status'], 'draft')
    def test_generic_statement_never_becomes_reminder(self):
        for text in ('сделаю письмо', 'надо открыть отчёт', 'спасибо', 'после обеда'):
            self.assertIsNone(intention(text,self.u,self.now))
    def test_invalid_time(self): self.assertIsNone(intention('Напомни в 25:90 открыть отчёт',self.u,self.now))
    def test_elapsed_time_shows_next_date(self):
        p = intention('в 10:00 открыть отчёт',self.u,self.now); self.assertTrue(p['due_at'].startswith('2026-10-07'))
    def test_tomorrow_date(self):
        self.assertTrue(intention('Завтра в 18:00 открыть отчёт',self.u,self.now)['due_at'].startswith('2026-10-07'))
    def test_only_confirmed_due_and_unexpired(self):
        p = intention('в 14:00 открыть отчёт',self.u,self.now)
        later = self.now + dt.timedelta(hours=4)
        self.assertFalse(due(p,later)); p['status']='confirmed'; self.assertTrue(due(p,later))
        self.assertFalse(due(p,later+dt.timedelta(hours=10))); p['status']='sent'; self.assertFalse(due(p,later))
    def test_pending_and_safety_own_input(self):
        for stage in ('offer_request_form','minimal_feedback_help','day_review_state','intent_paused','chain_edit_text','safety_mode'):
            self.assertTrue(protected(dict(self.u,stage=stage)))
        self.assertTrue(protected(dict(self.u,pending_feedback_json={'completed':True})))
    def test_one_time_mode_never_enables_regular_messages(self):
        for typ in ('morning','evening','reactivation'):
            self.assertFalse(bot.reminder_mode_allows({'reminder_mode':'intent_only'},typ,'2026-10-06'))

class ProactiveJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=self.tmp.name+'/db'; self.uid=91101
        await init_db(self.path); await migrate_db(self.path)
        self.p=patch.object(bot,'DB_PATH',self.path); self.p.start()
        u=default_user(self.uid); u.update(name='Иван',username='tester',stage='training_main',chat_id=self.uid,notifications_enabled=0,day_date=bot.local_date_for_user(u))
        u['last_user_activity_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=8)).isoformat()
        await save_user(u,self.path)
    async def asyncTearDown(self): self.p.stop(); self.tmp.cleanup()
    async def send(self,text):
        m=Message(self.uid,text)
        with patch.object(bot,'client',None): await bot.main_flow(m)
        return m,await get_user(self.uid,self.path)
    async def cb(self,kind,value,token=None):
        p=await get_user_profile(self.uid,self.path)
        token=token or p['planned_intention']['token']
        c=SimpleNamespace(from_user=SimpleNamespace(id=self.uid),data=f'proactive:{kind}:{token}:{value}',answer=AsyncMock(),message=SimpleNamespace(answer=AsyncMock()))
        await bot.on_proactive_callback(c); return c
    async def draft(self): await self.send('Напомни завтра в 14:00 открыть отчёт')
    async def tick(self,fake):
        with patch.object(bot.asyncio,'sleep',AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError): await bot.background_checkins(fake)
    async def make_due(self):
        await self.draft(); await self.cb('plan','yes')
        u=await get_user(self.uid,self.path); p=await get_user_profile(self.uid,self.path)
        p['planned_intention']['due_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=5)).astimezone(bot.local_now_for_user(u).tzinfo).isoformat()
        u['profile_json']=p; await save_user(u,self.path)
    async def test_draft_does_not_enable_notifications_or_replace_stage(self):
        _,u=await self.send('После обеда сделаю письмо')
        self.assertEqual(u['stage'],'training_main'); self.assertEqual(u['notifications_enabled'],0)
    async def test_confirm_only_this_intention(self):
        await self.draft(); await self.cb('plan','yes')
        u=await get_user(self.uid,self.path)
        self.assertEqual(u['reminder_mode'],'intent_only'); self.assertEqual(u['stage'],'training_main')
    async def test_decline_and_repeat_button(self):
        await self.draft(); await self.cb('plan','no'); c=await self.cb('plan','yes')
        self.assertEqual((await get_user(self.uid,self.path))['notifications_enabled'],0); c.message.answer.assert_not_awaited()
    async def test_cancel_confirmed_reminder(self):
        await self.draft(); await self.cb('plan','yes'); await self.cb('cancel','no')
        self.assertEqual((await get_user_profile(self.uid,self.path))['planned_intention']['status'],'cancelled')
    async def test_callback_after_stage_change_is_stale(self):
        await self.draft(); u=await get_user(self.uid,self.path); u['stage']='minimal_feedback_help'; await save_user(u,self.path)
        c=await self.cb('plan','yes'); c.message.answer.assert_not_awaited()
    async def test_contextual_delivery_once_preserves_stage(self):
        await self.make_due(); fake=SimpleNamespace(send_message=AsyncMock())
        await self.tick(fake); await self.tick(fake)
        self.assertEqual(fake.send_message.await_count,1); self.assertIn('открыть отчёт',fake.send_message.await_args.args[1])
        u=await get_user(self.uid,self.path); self.assertEqual(u['stage'],'training_main'); self.assertEqual(u['proactive_count_today'],1)
    async def test_failed_delivery_is_not_repeated_each_tick(self):
        await self.make_due(); fake=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError('test failure')))
        await self.tick(fake); await self.tick(fake); self.assertEqual(fake.send_message.await_count,1)
    async def test_disabled_or_paused_prevents_all_delivery(self):
        await self.make_due(); u=await get_user(self.uid,self.path); u['reminder_mode']='paused'; await save_user(u,self.path)
        fake=SimpleNamespace(send_message=AsyncMock()); await self.tick(fake); fake.send_message.assert_not_awaited()
    async def test_pending_feedback_is_never_interrupted(self):
        await self.make_due(); u=await get_user(self.uid,self.path); u['stage']='minimal_feedback_help'; await save_user(u,self.path)
        fake=SimpleNamespace(send_message=AsyncMock()); await self.tick(fake); fake.send_message.assert_not_awaited()
    async def test_total_cap_covers_intention(self):
        await self.make_due(); u=await get_user(self.uid,self.path); u.update(proactive_count_today=2,proactive_count_date=bot.local_date_for_user(u)); await save_user(u,self.path)
        fake=SimpleNamespace(send_message=AsyncMock()); await self.tick(fake); fake.send_message.assert_not_awaited()
    async def test_result_is_not_guided_skill_success(self):
        await self.make_due(); fake=SimpleNamespace(send_message=AsyncMock()); await self.tick(fake)
        await self.cb('result','yes'); u=await get_user(self.uid,self.path)
        self.assertFalse(u['skill_attempts']); self.assertEqual(u['done_count'],0)
    async def test_adaptation_preserves_question_and_counts_prompt(self):
        u=await get_user(self.uid,self.path); u['unanswered_proactive_count']=2
        fake=SimpleNamespace(send_message=AsyncMock()); await bot.ask_reminder_overload(fake,u)
        self.assertEqual(u['stage'],'training_main'); self.assertEqual(u['proactive_count_today'],1)
        self.assertFalse(bot.should_ask_reminder_overload(u))
        token=(await get_user_profile(self.uid,self.path))['proactive_adaptation']['token']
        await self.cb('adapt','14',token)
        after=await get_user(self.uid,self.path); self.assertEqual(after['reminder_mode'],'custom')
        self.assertEqual((await get_user_profile(self.uid,self.path))['reminder_hour'],14)
    async def test_no_now_blocks_every_category_today(self):
        m=Message(self.uid); u=await get_user(self.uid,self.path)
        await bot.handle_reactivation_reply(m,u,'💤 Не сейчас','💤 не сейчас')
        self.assertTrue(bot._user_no_reminders_today(u,bot.local_date_for_user(u)))
