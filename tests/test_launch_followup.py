"""PATCH-65: application fragments, refusal and stale return callbacks."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import test_launch_fixes_20261011 as fixtures
from test_dialogue_ux_patch import Message
import bot
from db import get_user

class Followup(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LaunchJourneys.asyncSetUp
    asyncTearDown = fixtures.LaunchJourneys.asyncTearDown
    user = fixtures.LaunchJourneys.user
    async def test_application_keeps_name_short_request_and_contact(self):
        u = await self.user(stage='offer_request_form')
        with patch.object(bot, 'notify_offer_request', AsyncMock(return_value=True)) as notify, patch.object(bot, 'log_event', AsyncMock()):
            for text in ('Иван', 'СДВГ', '@ivam'):
                m = Message(self.uid, text)
                await bot.handle_application_input(m, u, text)
            self.assertEqual(notify.await_args.args[3], 'Иван\nСДВГ\n@ivam')
            self.assertEqual(notify.await_count, 1)
    async def test_short_request_with_existing_contact(self):
        u = await self.user(stage='offer_request_form')
        m = Message(self.uid, 'СДВГ');m.from_user.username='ivan'
        with patch.object(bot, 'notify_offer_request', AsyncMock(return_value=True)) as notify, patch.object(bot, 'log_event', AsyncMock()):
            await bot.handle_application_input(m,u,m.text)
            notify.assert_awaited_once()
    async def test_contact_alone_does_not_submit(self):
        u = await self.user(stage='offer_request_form')
        with patch.object(bot, 'notify_offer_request', AsyncMock()) as notify:
            await bot.handle_application_input(Message(self.uid,'@ivam'),u,'@ivam')
            notify.assert_not_awaited()
    async def test_refusal_does_not_become_task(self):
        await self.user(stage='await_training_target',today_target='отчёт')
        with patch.object(bot, 'show_skill_for_current_task', AsyncMock()) as show:
            m=Message(self.uid,'Не хочу делать задания этого бота')
            await bot.main_flow(m)
            show.assert_not_awaited()
            self.assertIn('остановимся',m.answers[-1][0])
            self.assertEqual((await get_user(self.uid,bot.DB_PATH))['today_target'],'отчёт')
    async def test_stale_offer_exit_does_not_restore_screen(self):
        await self.user(stage='training_main')
        c=SimpleNamespace(from_user=SimpleNamespace(id=self.uid),data=bot.OFFER_CALLBACKS['continue_training'],message=Message(self.uid),answer=AsyncMock())
        with patch.object(bot,'claim_callback_once',AsyncMock(return_value=True)),patch.object(bot,'handle_safety_callback',AsyncMock(return_value=False)),patch.object(bot,'leave_offer_menu',AsyncMock()) as leave:
            await bot.on_offer_callbacks(c)
            leave.assert_not_awaited()
            self.assertFalse(c.message.answers)
