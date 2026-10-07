import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import aiosqlite

import bot
from core.behavior_review import recurrence, render_chain, render_week, week_facts
from core.training_track import context
from db import init_db, migrate_db, default_user, save_user, get_user, get_user_profile
from test_dialogue_ux_patch import Message


def row(action, day='2026-10-06', **values):
    item=dict(action_id=action, calendar_date=day, skill_id='open_without_timer', context_domain='work',
              mechanism='unclear_next_action', target_function='START', completed=0, partial=0, continued=None,
              helpfulness=None, source='skill_card', result='not_completed')
    item.update(values);return item


def repeated():
    return [row('a','2026-10-04'),row('b','2026-10-05'),row('c')]


class ReviewFacts(unittest.TestCase):
    def test_repeat_requires_distinct_reports_and_different_days(self):
        self.assertIsNone(recurrence([row('a')]*4,today='2026-10-06'))
        self.assertIsNone(recurrence([row(str(i)) for i in range(4)],today='2026-10-06'))
        self.assertEqual(recurrence(repeated(),today='2026-10-06')['count'],3)

    def test_unknown_execution_relief_and_context_do_not_make_chain(self):
        rows=[row(str(i),f'2026-10-0{i+1}',completed=None,result='started',helpfulness='helped') for i in range(3)]
        self.assertIsNone(recurrence(rows,today='2026-10-06'))
        self.assertIsNone(recurrence([dict(r,context_domain='other') for r in repeated()],today='2026-10-06'))
        self.assertIsNone(recurrence(repeated(),today='2026-10-06',rejected={'unclear_next_action'}))

    def test_no_invented_feelings_benefits_or_cost(self):
        chain=recurrence(repeated(),today='2026-10-06')
        self.assertFalse(chain['thought']);self.assertFalse(chain['immediate']);self.assertFalse(chain['later'])
        text=render_chain(chain)
        self.assertIn('пока неизвестно',text)
        self.assertNotIn('страх',text);self.assertNotIn('YouTube',text)

    def test_relief_without_continuation_is_separate_known_fact(self):
        chain=recurrence([dict(r,completed=1,result='completed',continued=0,helpfulness='helped') for r in repeated()],today='2026-10-06')
        self.assertIn('«стало легче»: 3',chain['immediate'])
        self.assertIn('не продолжилось',chain['behavior'])

    def test_seven_local_dates_inclusive_ignore_old_future_invalid_and_views(self):
        rows=[row('first','2026-09-30'),row('old','2026-09-29'),row('today'),row('future','2026-10-07'),row('bad','bad'),
              row('view',completed=None,result='started')]
        facts=week_facts(rows+rows,today='2026-10-06')
        self.assertEqual(facts['attempts'],2)

    def test_week_counts_partial_execution_relief_continuation_and_unknown(self):
        rows=[row('a',completed=1,result='completed',helpfulness='helped',continued=None),
              row('b',completed=0,partial=1,result='partial',continued=1),
              row('worse',completed=None,result='reported',helpfulness='worse')]
        facts=week_facts(rows,today='2026-10-06')
        self.assertEqual((facts['attempts'],facts['completed'],facts['partial'],facts['relief'],facts['continued']), (3,1,1,1,1))
        self.assertEqual(facts['execution_unknown'],1)
        self.assertEqual(facts['continuation_unknown'],2)
        text=render_week(rows,'2026-10-06',{'open_without_timer':'Открыть дело'})
        self.assertNotIn('история роста',text); self.assertNotIn('ты видишь, как меняешься',text)
        self.assertIn('не доказывают',text)

    def test_week_shows_only_confirmed_relevant_unrejected_chain(self):
        chain={**recurrence(repeated(),today='2026-10-06'),'confirmed':False,'break_at':'action'}
        titles={'open_without_timer':'Открыть дело'}
        self.assertNotIn('Сценарий, который вы подтвердили',render_week(repeated(),'2026-10-06',titles,chains=[chain]))
        chain['confirmed']=True
        text=render_week(repeated(),'2026-10-06',titles,chains=[chain])
        self.assertIn('Сценарий, который вы подтвердили',text);self.assertIn('короткий возврат',text)
        self.assertNotIn('Сценарий, который вы подтвердили',render_week(repeated(),'2026-10-06',titles,chains=[chain],rejected={'unclear_next_action'}))

    def test_partial_with_unknown_continuation_is_not_confirmed_failure(self):
        facts=week_facts([row('part',partial=1,continued=None)],today='2026-10-06')
        self.assertFalse(facts['checks'])

    def test_worse_overrides_selected_next_check(self):
        chain={**recurrence(repeated(),today='2026-10-06'),'confirmed':True,'break_at':'action'}
        text=render_week(repeated()+[row('worse',completed=None,helpfulness='worse')],'2026-10-06',{'open_without_timer':'Открыть дело'},chains=[chain])
        self.assertIn('Что проверим дальше\nУточнить, что стало хуже',text)


class ChainJourneys(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=self.tmp.name+'/db';self.uid=55881
        await init_db(self.path);await migrate_db(self.path)
        self.p=patch.object(bot,'DB_PATH',self.path);self.p.start()
        u=default_user(self.uid);today=bot.local_date_for_user(u)
        from datetime import date,timedelta
        rows=[]
        for i in range(3):
            day=(date.fromisoformat(today)-timedelta(days=i)).isoformat()
            rows.append(dict(row(str(i),day),completed=False,partial=False,started_at=day,completed_at=day))
        u.update(stage='post_action_reflection',current_skill='open_without_timer',daily_skill_id='open_without_timer',
                 current_task_title='PRIVATE TASK',current_task_context='work',skill_attempts=rows,plan_json=json.dumps(['open_without_timer']*28))
        await save_user(u,self.path)

    async def asyncTearDown(self):
        self.p.stop();self.tmp.cleanup()

    async def send(self,text):
        m=Message(self.uid,text)
        with patch.object(bot,'client',None):await bot.main_flow(m)
        return m,await get_user(self.uid,self.path)

    async def callback(self,action,value='0',token=None):
        u=await get_user(self.uid,self.path);panel=context(u)['chain_ui']
        c=SimpleNamespace(from_user=SimpleNamespace(id=self.uid),data=f"chain:{token or panel['token']}:{action}:{value}",
            answer=AsyncMock(),message=SimpleNamespace(answer=AsyncMock()))
        await bot.on_chain_callback(c);return c

    async def open(self):
        _,u=await self.send('🔎 Повторяющийся сценарий')
        return u

    async def test_open_is_optional_and_does_not_touch_attempt_or_plan(self):
        before=await get_user(self.uid,self.path);u=await self.open()
        self.assertEqual(u['stage'],before['stage']);self.assertEqual(u['plan_json'],before['plan_json'])
        self.assertEqual(u['skill_attempts'],before['skill_attempts'])
        self.assertFalse((await get_user_profile(self.uid,self.path)).get('behavioral_chains'))
        draft=context(u)['chain_ui']['draft']
        self.assertFalse(draft['confirmed']);self.assertNotIn('PRIVATE TASK',str(draft))

    async def test_confirm_persisted_and_point_requires_confirmation(self):
        await self.open();c=await self.callback('points');c.message.answer.assert_not_awaited()
        await self.callback('confirm');await self.callback('points');await self.callback('point','action')
        u=await get_user(self.uid,self.path);chains=(await get_user_profile(self.uid,self.path))['behavioral_chains']
        self.assertEqual(len(chains),1);chain=next(iter(chains.values()))
        self.assertTrue(chain['confirmed']);self.assertEqual(chain['break_at'],'action')
        self.assertEqual(len(u['skill_attempts']),3);self.assertEqual(u['stage'],'post_action_reflection')

    async def test_edit_one_field_and_reconfirm_not_rediagnose(self):
        await self.open();await self.callback('confirm');await self.callback('edit');await self.callback('field','thought')
        _,u=await self.send('Я подумал, что не знаю первого шага')
        self.assertEqual(u['stage'],'post_action_reflection');self.assertFalse(context(u)['chain_ui']['draft']['confirmed'])
        self.assertFalse(next(iter((await get_user_profile(self.uid,self.path))['behavioral_chains'].values()))['confirmed'])
        await self.callback('confirm')
        chain=next(iter((await get_user_profile(self.uid,self.path))['behavioral_chains'].values()))
        self.assertIn('не знаю',chain['thought']);self.assertEqual(chain['user_fields'],['thought'])

    async def test_decline_suppresses_same_proposal_and_keeps_evidence(self):
        await self.open();await self.callback('decline')
        u=await get_user(self.uid,self.path)
        self.assertIsNone(await bot.chain_proposal(u));self.assertEqual(len(u['skill_attempts']),3)
        self.assertNotIn('chain_ui',context(u))

    async def test_old_buttons_cannot_duplicate_confirmation(self):
        u=await self.open();old=context(u)['chain_ui']['token']
        await self.callback('confirm');c=await self.callback('confirm',token=old)
        c.message.answer.assert_not_awaited();self.assertEqual(len((await get_user_profile(self.uid,self.path))['behavioral_chains']),1)

    async def test_new_attempt_or_date_invalidates_panel(self):
        await self.open();u=await get_user(self.uid,self.path);bot.mark_action_card_active(u);await save_user(u,self.path)
        c=await self.callback('confirm');c.message.answer.assert_not_awaited()
        u=await get_user(self.uid,self.path);panel=context(u)['chain_ui'];panel['date']='2000-01-01'
        ctx=context(u);ctx['chain_ui']=panel;u['dialogue_context']=json.dumps(ctx);await save_user(u,self.path)
        c=await self.callback('confirm');c.message.answer.assert_not_awaited()

    async def test_closed_day_is_not_opened_by_chain_or_week(self):
        u=await get_user(self.uid,self.path);u.update(stage='day_core_stop',day_closed=1,today_closed=1,day_status='closed',last_day_closed_at=bot.local_date_for_user(u));await save_user(u,self.path)
        await self.open();await self.callback('confirm');await self.callback('close')
        m,u=await self.send('📅 Итоги недели')
        self.assertTrue(bot.day_closed_today(u));self.assertEqual(len(u['skill_attempts']),3)
        self.assertIn('Попыток с ответом: 3',str(m.answers))

    async def test_application_and_safety_keep_priority(self):
        await self.open();u=await get_user(self.uid,self.path);u['stage']='offer_request_form';await save_user(u,self.path)
        c=await self.callback('confirm');c.message.answer.assert_not_awaited()
        u=await get_user(self.uid,self.path);u.update(stage='post_action_reflection',safety_mode='active');await save_user(u,self.path)
        with patch.object(bot,'repeat_active_safety_screen',AsyncMock()) as safety:
            await self.callback('confirm');safety.assert_awaited_once()

    async def test_editor_cancel_restores_stage_and_keeps_plan(self):
        await self.open();await self.callback('field','thought');_,u=await self.send('Не сейчас')
        self.assertEqual(u['stage'],'post_action_reflection');self.assertEqual(u['plan_json'],json.dumps(['open_without_timer']*28))
        self.assertNotIn('chain_ui',context(u))

    async def test_confirmed_chain_can_be_reopened_without_duplicate(self):
        await self.open();await self.callback('confirm');await self.callback('close')
        u=await self.open()
        self.assertTrue(context(u)['chain_ui']['draft']['confirmed'])
        await self.callback('points');await self.callback('point','before')
        self.assertEqual(len((await get_user_profile(self.uid,self.path))['behavioral_chains']),1)

    async def test_week_ignores_duplicate_legacy_events(self):
        from db import log_event
        for _ in range(4):await log_event(self.uid,'training','done',{},self.path)
        m,u=await self.send('📅 Итоги недели')
        self.assertIn('Попыток с ответом: 3',str(m.answers))
        self.assertNotIn('PRIVATE TASK',str(m.answers))

    async def test_voice_edit_is_supported(self):
        await self.open();await self.callback('field','thought')
        u=await get_user(self.uid,self.path)
        m=SimpleNamespace(voice=SimpleNamespace(file_id='voice'),answer=AsyncMock())
        with patch.object(bot,'whisper_transcribe',AsyncMock(return_value='Я боялся ошибки')),patch.object(bot,'log_event',AsyncMock()):
            text=await bot.transcribe_voice_for_current_prompt(m,u)
        self.assertEqual(text,'Я боялся ошибки')

    async def test_editor_rollover_does_not_restore_old_screen(self):
        await self.open();await self.callback('field','thought')
        u=await get_user(self.uid,self.path);ctx=context(u);ctx['chain_ui']['date']='2000-01-01';u['dialogue_context']=json.dumps(ctx);await save_user(u,self.path)
        _,u=await self.send('Моё уточнение')
        self.assertEqual(u['stage'],'training_main');self.assertNotIn('chain_ui',context(u))

    async def test_refusal_not_offered_again_after_another_same_observation(self):
        await self.open();await self.callback('decline')
        u=await get_user(self.uid,self.path);u['skill_attempts'].append(dict(u['skill_attempts'][0],action_id='extra'));await save_user(u,self.path)
        self.assertIsNone(await bot.chain_proposal(u))
        # An explicit manual request can still reopen the refused proposal.
        u=await self.open();self.assertIn('chain_ui',context(u))

    async def test_navigation_from_editor_is_not_saved_as_a_thought(self):
        await self.open();await self.callback('field','thought');m,u=await self.send('📅 Итоги недели')
        self.assertEqual(u['stage'],'post_action_reflection')
        self.assertIn('Итоги недели',str(m.answers));self.assertNotIn('chain_ui',context(u))
