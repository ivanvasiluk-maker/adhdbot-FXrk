"""PATCH-67: varied multi-turn offline dialogues with state and evidence assertions.

Telegram and model services are substituted; these are not 20 live-user trials.
"""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import aiosqlite
import test_launch_fixes_20261011 as fixtures
from test_dialogue_ux_patch import Message
import bot
from db import get_user, get_user_profile
from core.experiment_dialogue import reported_chain, render_chain
from core.addressing import render_address

class ContextAudit(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixtures.LaunchJourneys.asyncSetUp
    asyncTearDown=fixtures.LaunchJourneys.asyncTearDown
    user=fixtures.LaunchJourneys.user

    async def trial(self, stage='training', trainer='marsha'):
        return await self.user(stage=stage,trainer_key=trainer,current_skill='visible_next_step',pending_skill_id='visible_next_step',today_target='Презентация руководителю',has_started_training=1,is_test_user=1,active_attempt={},current_action_id=None,day_core_skill_id='visible_next_step',day_core_skill_date=bot.local_date_for_user({'timezone':'Europe/Vilnius'}))

    async def say(self,text):
        m=Message(self.uid,text)
        with patch.object(bot,'run_analysis',AsyncMock()) as analyze:
            await bot.main_flow(m)
            analyze.assert_not_awaited()
        u=await get_user(self.uid,bot.DB_PATH)
        self.assertEqual(u['today_target'],'Презентация руководителю')
        self.assertEqual(u['day_core_skill_id'],'visible_next_step')
        return m,u

    async def test_01_boring_skill_is_not_new_diagnosis(self):
        await self.trial();m,u=await self.say('Мне скучно делать этот навык')
        self.assertEqual(u['stage'],'skill_feedback_adapt');self.assertIn('скука',m.answers[-1][0].lower())
        _,u=await self.say('Тревога');self.assertEqual(u['stage'],'training');self.assertNotEqual(bot.current_skill_for_action(u),'visible_next_step')
    async def test_02_self_criticism_is_remaining_target(self):
        await self.trial('minimal_feedback_help');_,u=await self.say('самокритика не остановилась совсем')
        self.assertEqual(u['stage'],'skill_feedback_adapt');_,u=await self.say('Да, мешает')
        self.assertEqual(bot.current_skill_for_action(u),'self_criticism_to_instruction')
    async def test_03_thoughts_can_remain_while_task_progresses(self):
        await self.trial('post_action_reflection');await self.say('самокритика осталась')
        m,u=await self.say('Есть, но дело идёт');self.assertIn('разные результаты',m.answers[-1][0]);self.assertEqual(u['stage'],'training_main')
    async def test_04_not_helped_preserves_unknown_completion(self):
        await self.trial('minimal_feedback_next');await self.say('не помогло')
        ctx=bot.dialogue_context((await get_user(self.uid,bot.DB_PATH))['dialogue_context'])
        self.assertIsNone(ctx['experiment_feedback_observations'][-1]['completed'])
    async def test_05_not_done_requests_adaptation(self):
        await self.trial();_,u=await self.say('не получилось')
        self.assertEqual(u['stage'],'skill_feedback_adapt');await self.say('Уменьшить шаг')
    async def test_06_hard_skill_changes_entry(self):
        await self.trial();await self.say('слишком сложно');_,u=await self.say('Другой способ')
        self.assertNotEqual(bot.current_skill_for_action(u),'visible_next_step')
    async def test_07_different_skill_request_does_not_erase_map(self):
        await self.trial();_,u=await self.say('хочу другой способ')
        self.assertEqual(u['stage'],'skill_feedback_adapt')
    async def test_08_worsening_pauses_without_false_completed_result(self):
        await self.trial('minimal_feedback_help');_,u=await self.say('стало хуже')
        self.assertEqual(u['stage'],'intent_paused')
        self.assertIsNone(bot.dialogue_context(u['dialogue_context'])['experiment_feedback_observations'][-1]['completed'])
    async def test_09_no_need_pauses(self):
        await self.trial('minimal_feedback_next');_,u=await self.say('не надо');self.assertEqual(u['stage'],'intent_paused')
    async def test_10_remove_is_stop_not_new_problem(self):
        await self.trial('request_context');_,u=await self.say('убери');self.assertEqual(u['stage'],'intent_paused')
    async def test_11_stop_preserves_resume_snapshot(self):
        await self.trial('minimal_feedback_help');await self.say('хочу остановиться');_,u=await self.say('Продолжить')
        self.assertEqual(u['stage'],'minimal_feedback_help')
    async def test_12_show_offer_precedes_feedback_router(self):
        await self.trial('minimal_feedback_help')
        with patch.object(bot,'force_show_offer',AsyncMock()) as show:
            await self.say('/show_offer');show.assert_awaited_once()
    async def test_13_show_offer_after_attempt_is_navigation(self):
        await self.trial('minimal_feedback_next')
        with patch.object(bot,'force_show_offer',AsyncMock()) as show:
            await self.say('/show_offer');show.assert_awaited_once()
    async def test_14_unknown_command_never_becomes_feedback(self):
        await self.trial('minimal_feedback_help');m,u=await self.say('/unknown')
        self.assertEqual(u['stage'],'minimal_feedback_help');self.assertIn('Неизвестная команда',m.answers[-1][0])
    async def test_15_reset_then_start_is_fresh(self):
        await self.trial();await bot.main_flow(Message(self.uid,'/reset_me'))
        async with aiosqlite.connect(bot.DB_PATH) as db:
            count=(await (await db.execute('SELECT COUNT(*) FROM users WHERE user_id=?',(self.uid,))).fetchone())[0]
        self.assertEqual(count,0)
        await bot.cmd_start(Message(self.uid,'/start'));u=await get_user(self.uid,bot.DB_PATH)
        self.assertEqual(u['stage'],'ask_name');self.assertFalse(u['today_target'])
    async def test_16_repeated_check_button_does_not_restart(self):
        await self.trial('post_action_reflection');await self.say('Проверить');_,u=await self.say('Проверить')
        self.assertEqual(u['stage'],'skill_feedback_adapt')
    async def test_17_free_detail_stays_in_experiment(self):
        await self.trial();await self.say('не помогло');_,u=await self.say('Я сделал шаг, но продолжаю бояться реакции начальника')
        self.assertEqual(u['stage'],'skill_feedback_adapt');self.assertIn('начальника',bot.dialogue_context(u['dialogue_context'])['experiment_feedback_detail'])
    async def test_18_offer_return_preserves_feedback_stage(self):
        u=await self.trial('minimal_feedback_help');before=(u['stage'],u['today_target'],u['day_core_skill_id'])
        m=Message(self.uid)
        await bot.force_show_offer(m,u,'manual_command');self.assertTrue(await bot.restore_offer_return(m,u))
        self.assertEqual((u['stage'],u['today_target'],u['day_core_skill_id']),before)
    async def test_19_application_without_username_is_delivered_once(self):
        u=await self.trial('offer_request_form');m=Message(self.uid);m.bot=SimpleNamespace(send_message=AsyncMock())
        with patch.object(bot,'log_event',AsyncMock()):
            self.assertTrue(await bot.notify_offer_request(m,u,'group','СДВГ'))
            self.assertTrue(await bot.notify_offer_request(m,u,'group','СДВГ'))
        self.assertEqual(m.bot.send_message.await_count,1)
        async with aiosqlite.connect(bot.DB_PATH) as db:
            row=await (await db.execute('SELECT request_text,delivery_status FROM application_receipts WHERE user_id=?',(self.uid,))).fetchone()
        self.assertEqual(row,('СДВГ','sent'))
    async def test_20_delivery_exception_is_not_success_or_blind_retry(self):
        u=await self.trial('offer_request_form');m=Message(self.uid);m.bot=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError))
        with patch.object(bot,'log_event',AsyncMock()):
            self.assertFalse(await bot.notify_offer_request(m,u,'group','СДВГ'));self.assertFalse(await bot.notify_offer_request(m,u,'group','СДВГ'))
        self.assertEqual(m.bot.send_message.await_count,1)
    async def test_21_three_trainers_use_same_task_with_different_practice(self):
        outputs=[]
        for trainer in ('marsha','skinny','beck'):
            u=await self.trial(trainer=trainer)
            outputs.append(bot.format_skill_card(u,bot.SKILLS_DB['visible_next_step'],u['today_target']))
        self.assertEqual(len(set(outputs)),3)
        self.assertIn('прогноз',outputs[2]);self.assertIn('Критерий завершения',outputs[1]);self.assertIn('самокритичная',outputs[0])
    def test_22_six_link_chain_uses_reported_episode(self):
        text='Открываю пустую презентацию. До сдачи два дня. Думаю «Сделаю плохо» и «руководитель решит, что я некомпетентный». Тревога и напряжение. Закрываю документ и переключаюсь на YouTube. Сразу становится легче, тревога временно снижается. Работа не выполнена, появляется самокритика.'
        chain=reported_chain(text)
        self.assertFalse(chain['missing']);self.assertIn('два дня',chain['situation']);self.assertIn('некомпетентный',chain['thoughts']);self.assertIn('YouTube',chain['behavior'])
    def test_23_no_unreported_relief_or_thoughts(self):
        chain=reported_chain('Нужно написать отчёт. Смотрю YouTube вместо работы.')
        self.assertIsNone(chain['immediate']);self.assertIsNone(chain['thoughts']);self.assertIn('Уточним',render_chain(chain))
    def test_24_addressing_repairs_known_bad_agreement(self):
        self.assertIn('будете готовы',render_address('когда будешь готовы',{'address_mode':'vy','address_form':'male'}))
        self.assertIn('подумала',render_address('что ты об этом подумали?',{'address_mode':'ty','address_form':'female'}))

    async def test_25_twenty_distinct_tasks_keep_context_through_adaptation_and_pause(self):
        tasks = [
            'Презентация руководителю', 'Оплатить счета', 'Написать отчёт', 'Подготовиться к экзамену',
            'Собрать вещи для поездки', 'Ответить на сложное письмо', 'Убрать кухню', 'Исправить ошибку бота',
            'Заполнить заявку', 'Собрать документы', 'Начать тренировку', 'Позвонить коллеге',
            'Написать страницу книги', 'Завершить рабочие заметки', 'Записаться на курс', 'Разобрать почту',
            'Сверить таблицу расходов', 'Записаться к врачу', 'Подготовить домашнее задание', 'Спланировать выходной',
        ]
        reports = [('Мне скучно делать этот навык','Скука'),('самокритика осталась','Да, мешает'),
                   ('Этот способ не помог','Уменьшить шаг'),('слишком сложно','Другой способ'),
                   ('хочу другой способ','Другой способ')]
        for i,task in enumerate(tasks):
            with self.subTest(task=task):
                u=await self.trial('training_main',('marsha','skinny','beck')[i%3])
                u['today_target']=task
                from db import save_user
                await save_user(u,bot.DB_PATH)
                report,choice=reports[i%len(reports)]
                with patch.object(bot,'run_analysis',AsyncMock()) as analysis:
                    await bot.main_flow(Message(self.uid,report))
                    await bot.main_flow(Message(self.uid,choice))
                    await bot.main_flow(Message(self.uid,'не надо'))
                    analysis.assert_not_awaited()
                saved=await get_user(self.uid,bot.DB_PATH)
                self.assertEqual(saved['today_target'],task)
                self.assertEqual(saved['stage'],'intent_paused')
                self.assertEqual(saved['day_core_skill_id'],'visible_next_step')
                self.assertLess(len(saved['dialogue_context']),50000)

    async def test_26_resume_after_reported_worsening_does_not_replay_failed_card(self):
        await self.trial();await self.say('Мне стало хуже после упражнения')
        m,u=await self.say('Продолжить')
        self.assertEqual(u['stage'],'recovery_context')
        self.assertNotIn('🧩',m.answers[-1][0])
        profile=await get_user_profile(self.uid,bot.DB_PATH)
        self.assertEqual(profile['last_skill_feedback']['helpfulness'],'worse')
        self.assertIsNone(profile['last_skill_feedback']['completed'])

    async def test_27_free_worsening_keeps_already_confirmed_execution(self):
        u=await self.trial('minimal_feedback_help')
        feedback=bot.minimal_feedback_base(u,source='action_done')
        feedback.update(completed=True,partial=False)
        bot.set_minimal_feedback(u,feedback)
        from db import save_user
        await save_user(u,bot.DB_PATH)
        await self.say('Мне стало хуже после упражнения')
        profile=await get_user_profile(self.uid,bot.DB_PATH)
        self.assertTrue(profile['last_skill_feedback']['completed'])
        self.assertEqual(profile['last_skill_feedback']['helpfulness'],'worse')
        self.assertIsNone(profile['last_skill_feedback']['continued_after_skill'])

    async def test_28_adaptation_creates_evidence_for_selected_skill(self):
        await self.trial();await self.say('самокритика осталась')
        with patch.object(bot,'_start_normalized_experiment_for_day',AsyncMock()) as start:
            await self.say('Да, мешает')
            start.assert_awaited_once()
            self.assertEqual(start.await_args.args[1]['skill_id'],'self_criticism_to_instruction')

    async def test_29_legacy_adaptation_releases_stale_router_without_losing_facts(self):
        u=await self.trial()
        session=bot.new_skiller_session(bot.SkillerDialogState.EXPERIMENT_FEEDBACK)
        session['case_facts']=['Пишу презентацию и боюсь оценки']
        bot._save_skiller_session(u,session)
        from db import save_user
        await save_user(u,bot.DB_PATH)
        await self.say('самокритика осталась');_,u=await self.say('Да, мешает')
        current=bot._skiller_session(u)
        self.assertEqual(current['state'],'DAY_OPEN')
        self.assertEqual(current['case_facts'],session['case_facts'])
        card=bot.build_current_skill_text(bot.SKILLS_DB['visible_next_step'],u={'trainer_key':'beck'})
        self.assertIn('прогноз',card)
    def test_30_dates_are_not_emotional_intensity(self):
        self.assertIsNone(reported_chain('Нужно написать отчёт до 10/10. Тревога.')['intensity'])
        self.assertIn('7/10',reported_chain('Тревога 7/10.')['intensity'])
