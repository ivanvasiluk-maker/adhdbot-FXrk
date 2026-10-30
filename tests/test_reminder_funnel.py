"""PATCH-86: consent, persisted snooze and task-check journeys, no live API."""
import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import bot
from core import reminder_flow as flow
from db import get_user, get_user_profile, save_user
from test_dialogue_ux_patch import Message
import test_proactive_sprint11 as fixtures

class Reminders(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ProactiveJourneys.asyncSetUp
    asyncTearDown = fixtures.ProactiveJourneys.asyncTearDown
    send = fixtures.ProactiveJourneys.send
    tick = fixtures.ProactiveJourneys.tick

    async def state(self):
        return (await get_user_profile(self.uid, self.path)).get(flow.KEY) or {}

    async def cb(self, value, token=None):
        state = await self.state()
        c = SimpleNamespace(from_user=SimpleNamespace(id=self.uid), data=f'remind:{token or state["token"]}:{value}',
                            answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
        await bot.on_reminder_callback(c)
        return c

    async def due(self, kind='task'):
        u = await get_user(self.uid, self.path)
        p = await get_user_profile(self.uid, self.path)
        p[flow.KEY] = dict(token='current', **{kind:dict(title='Написать черновик', status='confirmed',
            due_at=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=1)).isoformat())})
        u.update(profile_json=p, notifications_enabled=1, reminder_mode='intent_only')
        await save_user(u,self.path)
        return u

    def daytime(self, u=None):
        return dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=2))).replace(hour=15, minute=0)

    async def test_menu_never_enables_messages_without_opt_in(self):
        m,u = await self.send('⏰ Напоминания и дело дня')
        self.assertEqual(u['notifications_enabled'],0)
        self.assertEqual(u['stage'],'training_main')
        self.assertIn('Приглашать в 19:00',str(m.answers))
        await self.cb('daily')
        u=await get_user(self.uid,self.path)
        self.assertEqual(u['reminder_mode'],'custom')
        self.assertTrue((await self.state())['invites'])

    async def test_task_collects_time_and_requires_confirmation(self):
        await self.send('/day_task')
        await self.send('Написать черновик')
        with patch.object(bot,'local_now_for_user',self.daytime):
            m,u=await self.send('19:30')
        self.assertEqual(u['notifications_enabled'],0)
        self.assertIn('самостоятельно',str(m.answers))
        clock=SimpleNamespace(datetime=SimpleNamespace(now=lambda tz:self.daytime()), timezone=dt.timezone, timedelta=dt.timedelta)
        with patch.object(flow,'dt',clock): await self.cb('confirm')
        s=await self.state(); u=await get_user(self.uid,self.path)
        self.assertEqual(s['task']['title'],'Написать черновик')
        self.assertEqual(s['task']['status'],'confirmed')
        self.assertEqual(u['reminder_mode'],'intent_only')
        self.assertEqual(u['stage'],'training_main')

    async def test_natural_task_and_invalid_or_elapsed_time(self):
        await self.send('Дело дня: Закончить презентацию')
        with patch.object(bot,'local_now_for_user',self.daytime):
            for text in ('25:00','07:30','21:00','08:30','позже'):
                m,_=await self.send(text)
                self.assertIn('Нужно будущее время',str(m.answers))
        self.assertIsNone((await self.state()).get('task'))
        await self.send('/cancel')
        self.assertIsNone((await self.state()).get('draft'))

    async def test_due_survives_reload_and_is_delivered_once(self):
        await self.due()
        fake=SimpleNamespace(send_message=AsyncMock())
        with patch.object(bot,'local_now_for_user',self.daytime):
            await self.tick(fake); await self.tick(fake)
        self.assertEqual(fake.send_message.await_count,1)
        self.assertIn('Написать черновик',fake.send_message.await_args.args[1])
        self.assertEqual((await self.state())['task']['status'],'sent')
        self.assertEqual((await get_user(self.uid,self.path))['stage'],'training_main')

    async def test_explicit_snooze_is_not_blocked_by_two_hour_passive_cooldown(self):
        u=await self.due('snooze')
        u['last_user_activity_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        await save_user(u,self.path)
        fake=SimpleNamespace(send_message=AsyncMock())
        with patch.object(bot,'local_now_for_user',self.daytime): await self.tick(fake)
        self.assertEqual(fake.send_message.await_count,1)

    async def test_snooze_choices_and_duplicate_click(self):
        await self.send('/reminders'); await self.cb('daily')
        for minutes in ('60','90'):
            state=await self.state(); token=state['token']
            with patch.object(bot,'local_now_for_user',self.daytime):
                c=await self.cb(minutes)
                duplicate=await self.cb(minutes,token)
            after=await self.state()
            expected=self.daytime()+dt.timedelta(minutes=int(minutes))
            self.assertEqual(dt.datetime.fromisoformat(after['snooze']['due_at']).hour,expected.hour)
            self.assertIn('15 минут',str(c.message.answer.await_args))
            duplicate.message.answer.assert_not_awaited()

    async def test_task_snooze_keeps_task_check(self):
        await self.due(); fake=SimpleNamespace(send_message=AsyncMock())
        with patch.object(bot,'local_now_for_user',self.daytime):
            await self.tick(fake); await self.cb('90')
        s=await self.state()
        self.assertEqual(s['task']['status'],'confirmed')
        self.assertEqual(s['task']['title'],'Написать черновик')
        self.assertIsNone(s.get('snooze'))

    async def test_result_is_self_report_not_skill_success_and_offer_is_optional(self):
        await self.due(); fake=SimpleNamespace(send_message=AsyncMock())
        with patch.object(bot,'local_now_for_user',self.daytime): await self.tick(fake)
        with patch.object(bot,'show_day3_offer',AsyncMock()) as offer:
            c=await self.cb('done')
            offer.assert_not_awaited()
            self.assertIn('Узнать о группе',str(c.message.answer.await_args))
            await self.cb('group')
            offer.assert_awaited_once()
        u=await get_user(self.uid,self.path)
        self.assertEqual(u['done_count'],0)
        self.assertFalse(u['skill_attempts'])
        self.assertEqual((await self.state())['task']['result'],'done')

    async def test_stop_invalidates_old_buttons_and_cancels_schedules(self):
        await self.send('/reminders'); token=(await self.state())['token']
        await self.send('/stop')
        c=await self.cb('daily',token)
        c.message.answer.assert_not_awaited()
        self.assertEqual((await get_user(self.uid,self.path))['notifications_enabled'],0)
        self.assertIsNone((await self.state()).get('task'))

    async def test_callback_off_works_during_feedback_and_cannot_reenable(self):
        await self.send('/reminders'); token=(await self.state())['token']
        u=await get_user(self.uid,self.path); u['stage']='minimal_feedback_help'; await save_user(u,self.path)
        blocked=await self.cb('daily'); blocked.message.answer.assert_not_awaited()
        u=await get_user(self.uid,self.path); u.update(stage='safety_mode',safety_mode='active'); await save_user(u,self.path)
        await self.cb('off',token)
        self.assertEqual((await get_user(self.uid,self.path))['notifications_enabled'],0)

    async def test_closed_day_quiet_hours_cap_and_safety_block_delivery(self):
        fake=SimpleNamespace(send_message=AsyncMock())
        for field,value in [('notifications_enabled',0),('reminder_mode','paused'),('stage','safety_mode'),('proactive_count_today',2),('today_closed',1)]:
            u=await self.due()
            u.update(stage='training_main',today_closed=0,proactive_count_today=0,proactive_count_date=bot.local_date_for_user(u))
            u[field]=value
            if field=='today_closed':
                u['day_status']='closed'; u['closed_day_date']=bot.local_date_for_user(u)
            await save_user(u,self.path)
            with patch.object(bot,'local_now_for_user',self.daytime): await self.tick(fake)
        fake.send_message.assert_not_awaited()
        u=await self.due()
        with patch.object(bot,'local_now_for_user',lambda u:self.daytime().replace(hour=23)):
            await self.tick(fake)
        fake.send_message.assert_not_awaited()

    async def test_failed_send_is_claimed_without_repeat_spam(self):
        await self.due()
        fake=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError('synthetic failure')))
        with patch.object(bot,'local_now_for_user',self.daytime):
            await self.tick(fake); await self.tick(fake)
        self.assertEqual(fake.send_message.await_count,1)
        self.assertEqual((await self.state())['task']['status'],'dispatching')

    async def test_draft_cannot_consume_other_stage_or_global_commands(self):
        await self.send('/day_task')
        u=await get_user(self.uid,self.path); u['stage']='minimal_feedback_help'; await save_user(u,self.path)
        self.assertFalse(await flow.handle_text(bot,Message(self.uid,'Стало легче'),u,'Стало легче'))
        self.assertEqual((await self.state())['draft'],'task')

    async def test_daily_invite_has_requested_buttons_and_no_sale(self):
        await self.send('/reminders'); await self.cb('daily')
        u=await get_user(self.uid,self.path)
        fake=SimpleNamespace(send_message=AsyncMock())
        await bot.send_background_keyboard(fake,u,'old',None,'custom_checkin')
        text=str(fake.send_message.await_args)
        for label in ('Хочу тренироваться','Напомнить через час','Через 1,5 часа','Отключить напоминания'):
            self.assertIn(label,text)
        self.assertNotIn('240',text)

    async def test_expired_task_never_sends_a_late_check(self):
        u=await self.due()
        p=await get_user_profile(self.uid,self.path)
        p[flow.KEY]['task']['due_at']=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=5)).isoformat()
        u['profile_json']=p; await save_user(u,self.path)
        fake=SimpleNamespace(send_message=AsyncMock())
        with patch.object(bot,'local_now_for_user',self.daytime): await self.tick(fake)
        fake.send_message.assert_not_awaited()
        self.assertEqual((await self.state())['task']['status'],'expired')

    async def test_postpone_respects_night_and_total_daily_cap(self):
        await self.send('/reminders'); await self.cb('daily')
        with patch.object(bot,'local_now_for_user',lambda u:self.daytime().replace(hour=20)):
            c=await self.cb('90')
        c.message.answer.assert_not_awaited()
        u=await get_user(self.uid,self.path)
        u.update(proactive_count_today=2,proactive_count_date=bot.local_date_for_user(u)); await save_user(u,self.path)
        with patch.object(bot,'local_now_for_user',self.daytime): c=await self.cb('60')
        c.message.answer.assert_not_awaited()
        self.assertIsNone((await self.state()).get('snooze'))

    async def test_active_form_is_not_interrupted_by_background(self):
        await self.due()
        await self.send('/day_task')
        fake=SimpleNamespace(send_message=AsyncMock())
        with patch.object(bot,'local_now_for_user',self.daytime): await self.tick(fake)
        fake.send_message.assert_not_awaited()
