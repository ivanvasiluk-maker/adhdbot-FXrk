import copy
import datetime as dt
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from aiogram.methods import SendMessage, EditMessageText, SendPhoto
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, MessageEntity
from core.addressing import render_address, address_instructions
from core.outgoing_dialogue import OutgoingDialogueMiddleware
from core.dialogue_ux import plain_text
from db import default_user, init_db, migrate_db, save_user, get_user
from reactivation_service import can_send_reactivation
import bot
from test_dialogue_ux_patch import Message


class AddressCopyTests(unittest.TestCase):
    def test_all_six_preferences(self):
        for mode in ('ty', 'vy'):
            for form in ('male', 'female', 'neutral'):
                with self.subTest(mode=mode, form=form):
                    text = render_address('Ты уже попробовал. Готов? Открой файл и напиши одну строку.', {'address_mode':mode, 'address_form':form})
                    if mode == 'vy':
                        self.assertEqual(text, 'Вы уже попробовали. Готовы? Откройте файл и напишите одну строку.')
                    elif form == 'female':
                        self.assertEqual(text, 'Ты уже попробовала. Готова? Открой файл и напиши одну строку.')
                    elif form == 'male':
                        self.assertEqual(text, 'Ты уже попробовал. Готов? Открой файл и напиши одну строку.')
                    else:
                        self.assertEqual(text, 'Уже получилось попробовать. Можно попробовать? Открой файл и напиши одну строку.')
                    self.assertEqual(render_address(text, {'address_mode':mode, 'address_form':form}), text)

    def test_polite_source_becomes_informal_without_mixing(self):
        self.assertEqual(render_address('Вы описали вашу задачу. Хотите продолжить? Выберите шаг.', {'address_mode':'ty','address_form':'female'}), 'Ты описала твою задачу. Хочешь продолжить? Выбери шаг.')

    def test_user_words_links_and_third_person_are_not_rewritten(self):
        text = 'Вы сказали: «я устала, а он начал». “Ты сделал”. https://example.org/START Я записал. Он сделал. СДВГ.'
        rendered = render_address(text, {'address_mode':'ty','address_form':'female'})
        for part in ('«я устала, а он начал»', '“Ты сделал”', 'https://example.org/START', 'Я записал.', 'Он сделал.', 'СДВГ.'):
            self.assertIn(part, rendered)

    def test_known_terms_are_plain_and_agreement_is_preserved(self):
        text = plain_text('Рабочая гипотеза. Основной паттерн. START. Body doubling. beta-доступ.')
        self.assertEqual(text, 'Рабочая версия. Что чаще мешает. начало. Работа рядом с другим человеком. пробный доступ.')
        self.assertEqual(plain_text('«START body doubling»'), '«START body doubling»')

    def test_no_new_user_reminders_before_choice(self):
        u=default_user(99013)
        self.assertEqual(u['notifications_enabled'], 0)
        self.assertEqual(u['notification_consent'], 0)

    def test_ai_instruction_uses_explicit_choice(self):
        text=address_instructions({'address_mode':'ty','address_form':'female'})
        self.assertIn('только «ты»',text)
        self.assertIn('женский род',text)

    def test_pause_blocks_proactive_messages(self):
        u=default_user(99013)
        u.update(notifications_enabled=1, reminder_mode='paused')
        allowed, reason, _=can_send_reactivation(u,now=dt.datetime(2026,10,1,10,tzinfo=dt.timezone.utc))
        self.assertFalse(allowed)
        self.assertEqual(reason,'reminders_paused')


class OutgoingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=self.tmp.name+'/test.db'
        await init_db(self.path)
        await migrate_db(self.path)
        self.u=default_user(99013)
        self.u.update(address_mode='ty',address_form='female',stage='analysis_clarify_questions',pending_feedback_json='{"pending":1}', dialogue_context=json.dumps({'text':'Что помешало продолжить?', 'stage':'analysis_clarify_questions'}))
        await save_user(self.u,self.path)
        self.layer=OutgoingDialogueMiddleware(self.path)
        self.request=AsyncMock(return_value=True)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_direct_callback_background_and_caption_share_preferences(self):
        for method in (SendMessage(chat_id=99013,text='Вы уже попробовали. Готовы?'),EditMessageText(chat_id=99013,message_id=5,text='Вы уже попробовали. Готовы?'),SendPhoto(chat_id=99013,photo='file-id',caption='Вы уже попробовали. Готовы?')):
            await self.layer(self.request,None,method)
            sent=self.request.call_args.args[1]
            text=getattr(sent,'text',None) or sent.caption
            self.assertEqual(text,'Ты уже попробовала. Готова?')

    async def test_callback_ids_and_source_objects_stay_unchanged(self):
        markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ Сделал',callback_data='experiment.done')]])
        method=SendMessage(chat_id=99013,text='Готов?',reply_markup=markup)
        before=(method.text, method.reply_markup.model_dump())
        await self.layer(self.request,None,method)
        sent=self.request.call_args.args[1]
        self.assertEqual(sent.reply_markup.inline_keyboard[0][0].callback_data,'experiment.done')
        self.assertEqual(sent.reply_markup.inline_keyboard[0][0].text,'✅ Получилось сделать')
        self.assertEqual((method.text, method.reply_markup.model_dump()),before)

    async def test_settings_change_does_not_consume_pending_answer(self):
        original=await get_user(99013,self.path)
        with patch.object(bot,'DB_PATH',self.path),patch.object(bot,'log_event',AsyncMock()):
            await bot.main_flow(Message(uid=99013,text='/address'))
            choice=Message(uid=99013,text='Вы · женский род')
            await bot.main_flow(choice)
            self.assertIn('Что помешало продолжить?',choice.answers[-1][0])
        saved=await get_user(99013,self.path)
        self.assertEqual(saved['address_mode'],'vy')
        self.assertEqual(saved['address_form'],'female')
        self.assertEqual(saved['pending_feedback_json'],original['pending_feedback_json'])
        self.assertEqual(saved['stage'],original['stage'])
        await self.layer(self.request,None,SendMessage(chat_id=99013,text='Ты уже сделал.'))
        self.assertEqual(self.request.call_args.args[1].text,'Вы уже сделали.')

    async def test_preserves_explicit_entity_offsets(self):
        method=SendMessage(chat_id=99013,text='Готов?',entities=[MessageEntity(type='bold',offset=0,length=5)])
        await self.layer(self.request,None,method)
        self.assertEqual(self.request.call_args.args[1].text,'Готов?')

    async def test_group_and_unknown_recipient_are_not_personalized(self):
        for uid in (-123,99111):
            method=SendMessage(chat_id=uid,text='Готов?')
            await self.layer(self.request,None,method)
            self.assertEqual(self.request.call_args.args[1],method)
