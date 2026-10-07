import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import bot
from core.training_track import pressure, next_format, context, begin_moment, restore_training, action_instruction
from db import init_db, migrate_db, default_user, save_user, get_user, get_user_profile
from test_dialogue_ux_patch import Message

class TrackRules(unittest.TestCase):
    def test_pressure_uses_questions_templates_and_brief_replies(self):
        self.assertTrue(pressure(['Что?','Почему?','Как?'],[]))
        self.assertTrue(pressure(['Сейчас только одно: открой файл и оставь одну строку.']*2,[]))
        self.assertTrue(pressure([], [True,False,True]))
        self.assertFalse(pressure(['Что?'], [True]))
    def test_format_does_not_repeat_last_two(self):
        u={};names=[next_format(u) for _ in range(6)]
        for i in range(2,len(names)):self.assertNotIn(names[i],names[i-2:i])
    def test_nominal_minimum_becomes_concrete_first_action(self):
        self.assertEqual(action_instruction('Одна видимая подсказка.', '1. Оставь записку рядом с файлом.'), 'Оставь записку рядом с файлом.')
    def test_rollover_never_restores_yesterdays_attempt(self):
        u={'day_core_skill_date':'2026-10-05','day_core_skill_id':'open_only','current_action_id':'old'}
        begin_moment(u,'2026-10-05');u['current_action_id']='today'
        restore_training(u,'2026-10-06');self.assertEqual(u['current_action_id'],'today')
        self.assertNotIn('moment_training',context(u))

class DailyTrackJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=self.tmp.name+'/db';self.uid=66314
        await init_db(self.path);await migrate_db(self.path)
        self.p=patch.object(bot,'DB_PATH',self.path);self.p.start()
        u=default_user(self.uid);u.update(stage='training_main',name='Иван',current_task_title='отчёт',plan_json=json.dumps(['open_only','bad_draft']),daily_skill_id='open_only')
        bot.replace_day_core_skill(u,'open_only');bot.mark_action_card_active(u)
        m=bot.dialogue_message(Message(self.uid),u)
        await m.answer('Основная тренировка: открыть отчёт.',reply_markup=bot.kb_active_skill)
        await save_user(u,self.path);self.before=await get_user(self.uid,self.path)
    async def asyncTearDown(self):self.p.stop();self.tmp.cleanup()
    async def send(self,text):
        m=Message(self.uid,text)
        with patch.object(bot,'client',None):await bot.main_flow(m)
        return m,await get_user(self.uid,self.path)
    async def test_next_step_keeps_daily_core_despite_other_recommendation(self):
        u=await get_user(self.uid,self.path)
        with patch.object(bot,'get_honest_day_counts',AsyncMock(return_value={'attempts_today':0})),patch.object(bot,'should_switch_to_consolidation',return_value=False),patch.object(bot,'daily_conclusion',return_value={'next_skill_id':'bad_draft'}):
            await bot.open_next_logical_step(Message(self.uid),u)
        self.assertEqual(u['daily_skill_id'],self.before['day_core_skill_id'])
        self.assertEqual(u['day_core_skill_id'],self.before['day_core_skill_id'])
        self.assertEqual(u['plan_json'],self.before['plan_json'])
    async def test_moment_result_then_return_restores_pending_training_after_reload(self):
        await self.send('Помоги мне сейчас начать письмо, боюсь ошибиться и откладываю работу')
        u=await get_user(self.uid,self.path)
        self.assertIn('moment_training',context(u))
        if u['stage']=='awaiting_barrier_choice':await self.send(next(iter(bot.BARRIER_BUTTONS)))
        u=await get_user(self.uid,self.path);self.assertEqual(u['stage'],'training')
        await self.send('✅ Получилось сделать');m,u=await self.send('🙂 Стало легче')
        self.assertIn('Основная тренировка дня остаётся',m.answers[-1][0])
        p=await get_user_profile(self.uid,self.path);self.assertEqual(p['last_skill_feedback']['source'],'moment_help')
        m,u=await self.send('Вернуться к тренировке')
        for key in ('plan_json','day_core_skill_id','day_core_round_count','current_action_id','stage','daily_skill_status'):
            self.assertEqual(u[key],self.before[key],key)
        self.assertEqual(bot.active_attempt(u),bot.active_attempt(self.before))
        self.assertNotIn('moment_training',context(u));self.assertIn('Основная тренировка: открыть отчёт.',m.answers[0][0])
    async def test_resending_moment_card_cannot_replace_daily_core(self):
        u=await get_user(self.uid,self.path);begin_moment(u,bot.local_date_for_user(u))
        original=u['day_core_skill_id']
        bot.replace_day_core_skill(u,'bad_draft');self.assertEqual(u['day_core_skill_id'],original)
        bot.apply_skill_rebuild(u,'bad_draft');self.assertEqual(u['plan_json'],self.before['plan_json'])
        u.update(current_skill='bad_draft',pending_skill_id='bad_draft',daily_skill_id='bad_draft')
        await save_user(u,self.path)
        with patch.object(bot,'maybe_resume_pending_stuck_validation',AsyncMock(return_value=False)):
            await bot.send_current_skill(self.uid,Message(self.uid),u)
        self.assertEqual(u['day_core_skill_id'],original)
        self.assertEqual(u['plan_json'],self.before['plan_json'])

    async def test_mode_choice_preserves_original_training_before_prompt(self):
        await self.send('Помоги мне');u=await get_user(self.uid,self.path)
        self.assertEqual(context(u)['moment_training']['fields']['stage'],self.before['stage'])
        await self.send('⚡ Сделать что-то сейчас')
        m,u=await self.send('Вернуться к тренировке')
        self.assertEqual(u['current_action_id'],self.before['current_action_id'])
        self.assertEqual(u['stage'],self.before['stage'])

    async def test_health_moment_preserves_warning_and_returns(self):
        m,u=await self.send('Мне тревожно и плохо уже неделю, какие лекарства принимать?')
        self.assertIn(u['stage'],{'request_context','request_support'})
        self.assertIn('moment_training',context(u));self.assertEqual(u['plan_json'],self.before['plan_json'])
        m,u=await self.send('Вернуться к тренировке');self.assertEqual(u['current_action_id'],self.before['current_action_id'])
    async def test_boredom_gives_choices_then_changes_format_not_core(self):
        m,u=await self.send('скучно');self.assertEqual(u['stage'],'training_format')
        self.assertNotIn('?',m.answers[0][0]);self.assertNotIn('подробнее',m.answers[0][0])
        m,u=await self.send('⚡ 30 секунд');self.assertEqual(u['stage'],'training')
        self.assertIn('30 секунд',m.answers[0][0]);self.assertEqual(u['day_core_skill_id'],self.before['day_core_skill_id'])
        self.assertEqual(u['plan_json'],self.before['plan_json'])
        self.assertEqual(bot.active_attempt(u)['effect_status'],'unknown')
    async def test_feedback_and_application_own_bored_words(self):
        await self.send('✅ Сделал');m,u=await self.send('скучно')
        self.assertEqual(u['stage'],'minimal_feedback_help')
        self.assertNotIn('Похоже, этот формат',m.answers[0][0])
        u['stage']='offer_request_form';await save_user(u,self.path)
        with patch.object(bot,'notify_offer_request',AsyncMock()) as notify:
            m,u=await self.send('скучно');notify.assert_not_awaited()
        self.assertNotEqual(u['stage'],'training_format')
    async def test_done_button_not_swallowed_by_question_pressure(self):
        u=await get_user(self.uid,self.path);u['stage']='training'
        c=context(u);c['recent']=['Что?','Почему?','Как?'];u['dialogue_context']=json.dumps(c);await save_user(u,self.path)
        m,u=await self.send('✅ Получилось сделать');self.assertEqual(u['stage'],'minimal_feedback_help')
    async def test_skip_button_retains_priority_over_format_pressure(self):
        u=await get_user(self.uid,self.path);u['stage']='training'
        c=context(u);c['recent']=['Что?','Почему?','Как?'];u['dialogue_context']=json.dumps(c);await save_user(u,self.path)
        m,u=await self.send('Пропустить')
        self.assertEqual(u['stage'],'skip_options');self.assertIn('Пропуск',m.answers[0][0])

    async def test_yesterdays_format_button_does_not_start_an_attempt(self):
        u=await get_user(self.uid,self.path);u['stage']='training_format'
        c=context(u);c['training_format_date']='2000-01-01';u['dialogue_context']=json.dumps(c);await save_user(u,self.path)
        m,u=await self.send('⚡ 30 секунд')
        self.assertEqual(u['stage'],'training_main')
        self.assertEqual(u['current_action_id'],self.before['current_action_id'])
        self.assertIn('не актуален',m.answers[0][0])

    async def test_pressure_changes_format_instead_of_open_question(self):
        u=await get_user(self.uid,self.path);c=context(u);c['recent']=['Что?','Почему?','Как?'];u['dialogue_context']=json.dumps(c);await save_user(u,self.path)
        m,u=await self.send('не знаю');self.assertEqual(u['stage'],'training_format');self.assertNotIn('?',m.answers[0][0])
