"""PATCH-66: onboarding, Telegram identity, chain and persistent preferences."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import test_launch_fixes_20261011 as fixtures
from test_dialogue_ux_patch import Message
import bot
import texts
from db import get_user, get_user_profile

class SalesDialogue(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.LaunchJourneys.asyncSetUp
    asyncTearDown=fixtures.LaunchJourneys.asyncTearDown
    user=fixtures.LaunchJourneys.user
    async def test_new_onboarding_selects_trainer_before_address(self):
        await self.user(stage='ask_name')
        m=Message(self.uid,'Иван');await bot.main_flow(m)
        self.assertEqual((await get_user(self.uid,bot.DB_PATH))['stage'],'await_trainer')
        self.assertIn('виртуальные тренеры',m.answers[-1][0])
        m=Message(self.uid,'Скинни');await bot.main_flow(m)
        self.assertEqual((await get_user(self.uid,bot.DB_PATH))['stage'],'ask_address')
        self.assertTrue(any('Я Скинни' in a[0] for a in m.answers))
        await bot.main_flow(Message(self.uid,'На вы'))
        await bot.main_flow(Message(self.uid,'Мужской'))
        self.assertEqual((await get_user(self.uid,bot.DB_PATH))['stage'],'privacy_consent')
    async def test_telegram_identity_is_automatic(self):
        u=await self.user(stage='offer_request_form')
        m=Message(self.uid,'СДВГ')
        with patch.object(bot,'notify_offer_request',AsyncMock(return_value=True)) as send,patch.object(bot,'log_event',AsyncMock()):
            await bot.handle_application_input(m,u,m.text)
            send.assert_awaited_once()
            self.assertEqual(send.await_args.args[3],'СДВГ')
    async def test_notification_has_russian_fields_and_profile_link(self):
        u=await self.user(name='Иван')
        m=Message(self.uid);m.from_user.username='ivan';m.from_user.first_name='Иван'
        result=bot.offer_request_notification_text(u,'group','СДВГ',m)
        self.assertIn('Telegram: @ivan',result)
        self.assertIn(f'tg://user?id={self.uid}',result)
        self.assertNotIn('User ID',result)
    async def test_support_opt_out_is_persisted_without_support_claim(self):
        await self.user(stage='social_support_await',pending_plan_change=json.dumps({'type':'social_support','return_stage':'waiting_next_day'}))
        m=Message(self.uid,'Больше не спрашивать об опорах')
        await bot.main_flow(m)
        profile=await get_user_profile(self.uid,bot.DB_PATH)
        self.assertTrue(profile['social_support_opt_out'])
        self.assertFalse(profile.get('social_support_available'))
        self.assertTrue(any('Больше не буду' in a[0] for a in m.answers))
    def test_public_description_has_no_age_badge(self):
        self.assertNotIn('18+',bot.PUBLIC_BOT_DESCRIPTION+bot.PUBLIC_BOT_SHORT_DESCRIPTION+''.join(texts.ONBOARDING_SCREENS))
        self.assertIn('18 лет',texts.privacy_notice_text())
    def test_chain_does_not_invent_relief(self):
        result=texts.behavioral_chain_text('Нужно написать отчёт','смотрю видео')
        self.assertIn('Поведенческая цепочка',result)
        self.assertNotIn('облегчение',result)
        self.assertIn('ещё не уточнено',texts.behavioral_chain_text())
    def test_internal_code_cannot_be_conclusion(self):
        result=texts.preliminary_diagnosis_conclusion_text('default_start_block',first_check='Открыть файл')
        self.assertNotIn('default',result)
        self.assertIn('Рабочая гипотеза',result)
        self.assertIn('Что будем делать',result)
