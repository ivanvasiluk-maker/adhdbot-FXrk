import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import bot
from core.contextual_memory import similar, veto
from core.learning_engine import update_hypothesis_scores, update_learning_model, ExperimentEvidence
from core.personal_working_model import update_working_model, render_working_model
from core.training_track import context
from db import init_db, migrate_db, default_user, save_user, get_user, get_user_profile, update_user_profile
from test_dialogue_ux_patch import Message


def row(action='a', **values):
    return dict(action_id=action, skill_id='open_only', mechanism='unclear_next_action', target_function='START',
                context_domain='work', completed=1, partial=0, continued=1, helpfulness=None, result='completed',
                reported_at='2026-10-01', shown_at='2026-10-01', **values)


class MemoryFacts(unittest.TestCase):
    def match(self, rows, **overrides):
        args = dict(mechanism='unclear_next_action', target='START', domain='work', available={'open_only'})
        args.update(overrides)
        return similar(rows, **args)

    def test_exact_context_distinct_successes_only(self):
        a, b, c = row(), row('b'), row('c')
        b['context_domain'] = 'home'; c['completed'] = None; c['continued'] = None; c['helpfulness'] = 'helped'
        self.assertEqual(self.match([a, a, b, c])['count'], 1)
        self.assertIsNone(self.match([a], target='RETURN'))
        self.assertIsNone(self.match([a], domain='other'))
        self.assertIsNone(self.match([a], mechanism='attention_drift'))

    def test_recent_worse_or_failure_prevents_repeat(self):
        a, b = row(), row('b'); b.update(reported_at='2026-10-02', helpfulness='worse')
        self.assertIsNone(self.match([a,b]))
        b.update(helpfulness=None, result='not_completed')
        self.assertIsNone(self.match([a,b]))

    def test_disabled_skill_and_alias(self):
        self.assertIsNone(self.match([row()], available=set()))
        result = self.match([row()], available={'open_without_timer'}, aliases={'open_only':'open_without_timer'})
        self.assertEqual(result['skill_id'], 'open_without_timer')

    def test_veto_removes_leader_without_erasing_skill_evidence(self):
        profile = dict(learning_model={'primary_hypothesis':'overload', 'hypothesis_scores':{'overload':.9}},
                       personal_working_model={'helpful_interventions':{'Шаг':3}})
        result = veto(profile, ['overload'], reason='wrong_problem')
        self.assertEqual(result['hypothesis_scores']['overload'], 0)
        self.assertIsNone(result['primary_hypothesis'])
        self.assertEqual(result['personal_working_model']['helpful_interventions'], {'Шаг':3})
        self.assertEqual(profile['learning_model']['hypothesis_scores']['overload'], .9)

    def test_negation_is_not_positive_evidence(self):
        scores = update_hypothesis_scores({'overload':.7}, ['Страх ошибки, а не перегруз'])
        self.assertLess(scores['overload'], .7)
        self.assertGreater(scores['fear_of_failure'], 0)
        scores = update_hypothesis_scores({'fear_of_failure':.7}, ['дело не в страхе оценки'])
        self.assertLess(scores['fear_of_failure'], .7)

    def test_correction_and_dedup_survive_future_results(self):
        old = dict(explicit_user_correction='Не перегруз', hypothesis_rejected=True)
        args = dict(barrier='перегруз', skill_title='Шаг', context='work', successful=True, evidence_ref='a')
        model = update_working_model(old, **args).as_dict()
        repeated = update_working_model(model, **args).as_dict()
        self.assertEqual(repeated['evidence_count'], 1)
        self.assertEqual(repeated['explicit_user_correction'], 'Не перегруз')
        self.assertIn('Предыдущую гипотезу вы отвергли', render_working_model(repeated))
        args['evidence_ref'] = 'b'
        self.assertEqual(update_working_model(repeated, **args).as_dict()['explicit_user_correction'], 'Не перегруз')

    def test_unknown_effect_is_not_failure(self):
        model = update_working_model({}, barrier='', skill_title='Шаг', context='work', successful=None, evidence_ref='x').as_dict()
        self.assertFalse(model['failed_skills']); self.assertFalse(model['successful_skills'])

    def test_veto_survives_ordinary_feedback(self):
        current = veto({'learning_model':{'hypothesis_scores':{'overload':.9}}}, ['overload'], reason='wrong_problem')['learning_model']
        updated = update_learning_model(current, ExperimentEvidence('open_only', True, after_action='continued_target_task'), observations=['перегруз'])
        self.assertEqual(updated['hypothesis_scores']['overload'],0)
        self.assertNotEqual(updated['primary_hypothesis'],'overload')

    def test_refined_result_replaces_unknown_without_second_attempt(self):
        args = dict(barrier='', skill_title='Шаг', context='work', evidence_ref='x')
        model = update_working_model({}, successful=None, **args).as_dict()
        updated = update_working_model(model, successful=True, **args).as_dict()
        self.assertEqual(updated['evidence_count'],1)
        self.assertEqual(updated['successful_skills']['Шаг'],1)
        args['evidence_ref']='y'
        updated = update_working_model(updated, successful=None, **args).as_dict()
        self.assertIn('1 из 2 попыток',render_working_model(updated))


class MemoryJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = self.tmp.name+'/db'; self.uid = 55771
        await init_db(self.path); await migrate_db(self.path)
        self.patch = patch.object(bot, 'DB_PATH', self.path); self.patch.start()
        u = default_user(self.uid)
        u.update(stage='confirm_analysis', current_task_context='work', current_skill='open_without_timer',
                 daily_skill_id='open_without_timer', plan_json=json.dumps(['open_without_timer']*28),
                 analysis_json=json.dumps({'selected_barrier':'overload', 'selected_skill':'open_without_timer', 'specific_pattern':'перегруз'}))
        await save_user(u,self.path)
        await update_user_profile(self.uid, {'main_hypothesis':'перегруз', 'primary_hypothesis':'overload',
            'learning_model':{'primary_hypothesis':'overload', 'hypothesis_scores':{'overload':.9}}},self.path)

    async def asyncTearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    async def send(self,text):
        m = Message(self.uid,text)
        with patch.object(bot,'client',None):
            await bot.main_flow(m)
        return m,await get_user(self.uid,self.path)

    async def test_polite_disagreement_then_wrong_problem_persisted(self):
        _,u = await self.send('Вы меня не поняли')
        self.assertEqual(u['stage'],'misunderstood_reason')
        _,u = await self.send('1. Не та проблема')
        profile = await get_user_profile(self.uid,self.path)
        self.assertTrue(profile['rejected_hypotheses']['overload']['hypothesis_rejected'])
        self.assertEqual(profile['learning_model']['hypothesis_scores']['overload'],0)
        self.assertEqual(profile['main_hypothesis'],'')
        self.assertTrue(json.loads(u['analysis_json'])['hypothesis_rejected'])
        self.assertEqual(u['plan_json'],json.dumps(['open_without_timer']*28))

    async def test_not_laziness_does_not_invent_fear(self):
        await self.send('Вы меня не поняли')
        m,u = await self.send('4. Это не про лень')
        profile = await get_user_profile(self.uid,self.path)
        self.assertNotIn('fear_of_evaluation',str(profile))
        self.assertEqual(u['stage'],'misunderstood_problem_await')
        self.assertNotIn('страх оценки',str(m.answers))

    async def test_rejected_prompt_is_not_given_to_analyzer(self):
        await self.send('Вы меня не поняли'); await self.send('1. Не та проблема')
        u = await get_user(self.uid,self.path)
        analyzer = AsyncMock(return_value={'bucket':'mixed','selected_skill':'open_without_timer'})
        with patch.object(bot,'ai_analyze_comprehensive',analyzer), patch.object(bot,'answer_with_keyboard',AsyncMock()):
            await bot.rebuild_analysis_lightweight(Message(self.uid,''),u,'нет ясного первого шага','wrong_problem')
        self.assertNotIn('перегруз',analyzer.await_args.args[0])
        await save_user(u,self.path)
        self.assertTrue((await get_user_profile(self.uid,self.path))['rejected_hypotheses'])

    async def prepare_memory(self):
        u = await get_user(self.uid,self.path)
        u.update(stage='intent_act_context',analysis_json=json.dumps({'selected_barrier':'unclear_next_action','selected_skill':'open_without_timer'}),
                 skill_attempts=[dict(row('past'),skill_id='open_without_timer',source='skill_card', calendar_date='2026-10-01', completed=True, partial=False, continued_target_task=True)])
        await save_user(u,self.path)
        u = await get_user(self.uid,self.path)
        await bot.start_intent_action(Message(self.uid,''),u)
        return await get_user(self.uid,self.path)

    async def test_similarity_requires_confirmation_without_new_attempt(self):
        u = await self.prepare_memory()
        self.assertEqual(u['stage'],'intent_memory_confirm')
        self.assertEqual(len(u['skill_attempts']),1)
        plan=u['plan_json']
        _,u = await self.send('Да, похоже на прошлый случай')
        self.assertEqual(u['stage'],'training'); self.assertEqual(u['plan_json'],plan)
        self.assertEqual(len(u['skill_attempts']),2)
        # old reply cannot consume a second memory proposal or create an extra attempt
        self.assertFalse(await bot.handle_memory_confirmation(Message(self.uid,''),u,'Да, похоже на прошлый случай'))

    async def test_decline_keeps_history_and_requests_new_situation(self):
        await self.prepare_memory()
        _,u = await self.send('Это сейчас не подходит')
        self.assertEqual(u['stage'],'intent_act_context'); self.assertEqual(len(u['skill_attempts']),1)
        self.assertNotIn('pending_memory',context(u))

    async def test_stale_date_cannot_start_old_memory(self):
        u = await self.prepare_memory(); ctx=context(u);ctx['pending_memory']['date']='2000-01-01'
        u['dialogue_context']=json.dumps(ctx);await save_user(u,self.path)
        _,u = await self.send('Да, похоже на прошлый случай')
        self.assertEqual(u['stage'],'intent_act_context');self.assertEqual(len(u['skill_attempts']),1)

    async def test_changed_attempt_rejects_pending_memory(self):
        u = await self.prepare_memory(); u['active_attempt']['attempt_id']='new-action'; await save_user(u,self.path)
        _,u = await self.send('Да, похоже на прошлый случай')
        self.assertEqual(u['stage'],'intent_act_context'); self.assertEqual(len(u['skill_attempts']),1)

    async def test_safety_owns_confirmation_and_disagreement(self):
        u = await self.prepare_memory(); u['safety_mode']='active'; await save_user(u,self.path)
        _,u = await self.send('Да, похоже на прошлый случай')
        self.assertEqual(u['safety_mode'],'active')
        _,u = await self.send('Вы меня не поняли')
        self.assertNotEqual(u['stage'],'misunderstood_reason')
        self.assertEqual(u['safety_mode'],'active')
        self.assertEqual(len(u['skill_attempts']),1)

    async def test_map_after_rejection_keeps_skill_facts(self):
        await self.send('Вы меня не поняли'); await self.send('1. Не та проблема')
        u = await get_user(self.uid,self.path); m = Message(self.uid,'')
        await bot.send_user_map(m,u,'full_map')
        output=' '.join(text for text,_ in m.answers)
        self.assertIn('Предыдущий вывод вы отвергли',output)
        self.assertIn('Навыки и результаты сохранены',output)
        self.assertNotIn('перегруз',output.lower())

    async def test_closed_day_correction_survives_save_and_next_attempt(self):
        u = await get_user(self.uid,self.path)
        u.update(stage='personal_model_correction', day_closed=1, today_closed=1, day_status='closed', last_day_closed_at=bot.local_date_for_user(u))
        await save_user(u,self.path)
        _,u = await self.send('Дело не в перегрузе, мне непонятен первый шаг')
        profile = await get_user_profile(self.uid,self.path)
        self.assertEqual(profile['main_hypothesis'],'')
        self.assertIn('непонятен',profile['personal_working_model']['explicit_user_correction'])
        self.assertTrue(bot.day_closed_today(u))

    async def test_application_owns_disagreement_input(self):
        u = await get_user(self.uid,self.path); u['stage']='offer_request_form'; await save_user(u,self.path)
        with patch.object(bot,'notify_offer_request',AsyncMock()) as notify:
            _,u = await self.send('Вы меня не поняли')
            notify.assert_not_awaited()
        self.assertNotEqual(u['stage'],'misunderstood_reason')

    async def test_wrong_skill_excluded_without_failed_attempt(self):
        await self.send('Вы меня не поняли'); _,u = await self.send('3. Не тот навык')
        profile = await get_user_profile(self.uid,self.path)
        self.assertIn('open_without_timer',bot.not_fit_today_skills(u,profile))
        self.assertFalse(u['skill_attempts'])

    async def test_daily_summary_does_not_revive_rejected_review_barrier(self):
        await self.send('Вы меня не поняли'); await self.send('1. Не та проблема')
        u = await get_user(self.uid,self.path); profile = await get_user_profile(self.uid,self.path)
        profile['last_day_review']={'barrier':'перегруз'}
        summary = bot.daily_conclusion(u,profile)
        self.assertIn('отвергнут',summary['primary_hypothesis'])
        self.assertNotIn('перегруз',summary['primary_hypothesis'])
