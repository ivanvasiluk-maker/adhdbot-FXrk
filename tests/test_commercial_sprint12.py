import datetime as dt
import unittest
from unittest.mock import patch, AsyncMock
import bot
from core.commercial import commercial_blocked, offer_evidence
from core.product_metrics import product_metrics, text_reduction

NOW = dt.datetime(2026,10,6,20,tzinfo=dt.timezone.utc)

def row(action='a', **patches):
    return dict(user_id=1,action_id=action,skill_id='s',calendar_date='2026-10-06',
                completed=1,partial=0,helpfulness='helped',continued=1,
                independent=0,context_domain='work',source='feedback',**patches)

class CommercialRules(unittest.TestCase):
    def test_same_local_day_blocks_across_utc_boundary(self):
        self.assertTrue(commercial_blocked({'timezone':'Asia/Tokyo'},
            {'offer_seen_at':'2026-10-06T16:00:00+00:00'},now=NOW))
    def test_distress_blocks(self):
        for p in ({'last_skill_feedback':{'helpfulness':'worse'}},
                  {'last_skill_feedback':{'completed':True,'helpfulness':'not_helped','continued_after_skill':False}},
                  {'last_day_review':{'state':'почти не было сил'}},
                  {'last_day_review':{'state':'напряжённо'}}):
            self.assertTrue(commercial_blocked({},p,now=NOW))
    def test_safety_blocks(self):
        for u in ({'crisis_mode':1},{'stage':'safety_mode'},{'stage':'offer_request_form'}):
            self.assertTrue(commercial_blocked(u,{},now=NOW))
    def test_decline_blocks_seven_days(self):
        p={'offer_declined_at':NOW.isoformat()}
        self.assertTrue(commercial_blocked({},p,now=NOW+dt.timedelta(days=6)))
        self.assertFalse(commercial_blocked({},p,now=NOW+dt.timedelta(days=8)))
    def test_completion_without_benefit_not_proof(self):
        r=row();r.update(helpfulness=None,continued=None)
        self.assertEqual(offer_evidence([r],'2026-10-06')['useful_applications'],0)
    def test_duplicate_not_second_success(self):
        self.assertEqual(offer_evidence([row(),row()],'2026-10-06')['useful_applications'],1)
    def test_future_excluded(self):
        r=row();r['calendar_date']='2026-10-07'
        self.assertEqual(offer_evidence([r],'2026-10-06')['useful_applications'],0)
    def test_latest_worse_blocks_even_with_old_success(self):
        r=row('b');r['helpfulness']='worse';r['reported_at']='z'
        self.assertTrue(offer_evidence([row(),r],'2026-10-06')['latest_worse'])

class AutomaticGate(unittest.IsolatedAsyncioTestCase):
    async def test_one_success_soft_only(self):
        with patch.object(bot,'autonomy_rows',AsyncMock(return_value=[row()])):
            u={'user_id':1,'stage':'training','timezone':'UTC'}
            self.assertTrue(await bot.automatic_support_allowed(u,{},first=True))
            self.assertFalse(await bot.automatic_support_allowed(u,{}))
    async def test_substantive_flag_cannot_bypass_evidence(self):
        with patch.object(bot,'get_user_profile',AsyncMock(return_value={})), patch.object(bot,'autonomy_rows',AsyncMock(return_value=[])):
            self.assertFalse(await bot.maybe_show_help_offer(AsyncMock(),{'user_id':1,'day':1},substantive=True))
    async def test_auto_show_does_not_change_state_without_proof(self):
        u={'user_id':1,'stage':'training'}
        with patch.object(bot,'get_user_profile',AsyncMock(return_value={})), patch.object(bot,'autonomy_rows',AsyncMock(return_value=[])):
            self.assertFalse(await bot.show_day3_offer(AsyncMock(),u,'test'))
            self.assertEqual(u['stage'],'training')

class Metrics(unittest.TestCase):
    def test_empty_is_unknown(self):
        self.assertTrue(all(v['rate'] is None for v in product_metrics([],[],'2026-10-06').values()))
    def test_duplicate_and_qa_excluded(self):
        a=row();q=row('q');q['user_id']=2
        m=product_metrics([1],[a,a,q],'2026-10-06')
        self.assertEqual(m['behaviour_change'],{'numerator':1,'denominator':1,'rate':1.0})
        self.assertEqual(m['skill_adoption']['numerator'],1)
        self.assertIsNone(m['model_accuracy']['rate'])
    def test_transfer_same_skill_known_contexts(self):
        b=row('b');b.update(context_domain='home',independent=1,calendar_date='2026-10-05')
        m=product_metrics([1],[row(),b],'2026-10-06')
        for key in ('skill_adoption','generalisation','independent_use','retention'):
            self.assertEqual(m[key]['rate'],1)
    def test_unknown_context_not_transfer(self):
        b=row('b');b['context_domain']='other'
        self.assertEqual(product_metrics([1],[row(),b],'2026-10-06')['generalisation']['numerator'],0)
    def test_assessments_deduplicated(self):
        a=dict(user_id=1,assessment_id='a',confirmed=False,calendar_date='2026-10-06')
        self.assertEqual(product_metrics([1],[],'2026-10-06',model_assessments=[a,a])['model_accuracy']['rate'],0)
    def test_paired_text_samples(self):
        self.assertEqual(text_reduction({'s':100},{'s':30}),.7)
        self.assertIsNone(text_reduction({'s':0},{'s':0}))
        with self.assertRaises(ValueError): text_reduction({'s':100},{'other':30})

class DecisionsPersist(unittest.IsolatedAsyncioTestCase):
    async def test_decline_survives_reload_and_save(self):
        import tempfile
        from db import init_db, migrate_db, default_user, save_user, get_user, get_user_profile
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db'
            await init_db(path); await migrate_db(path)
            u=default_user(31201);u['stage']='training_main'
            await save_user(u,path)
            with patch.object(bot,'DB_PATH',path):
                await bot.remember_offer_decision(u,declined=True)
                await save_user(u,path)
                restored=await get_user(u['user_id'],path)
                profile=await get_user_profile(u['user_id'],path)
                self.assertIn('offer_declined_at',profile)
                self.assertTrue(commercial_blocked(restored,profile))
    async def test_first_invitation_has_no_price_and_leaves_question(self):
        import tempfile
        from db import init_db, migrate_db, default_user, save_user, get_user_profile
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db';await init_db(path);await migrate_db(path)
            u=default_user(31202);u.update(stage='post_action_reflection',day=2,dialogue_context='reflection')
            await save_user(u,path);m=AsyncMock()
            with patch.object(bot,'DB_PATH',path), patch.object(bot,'autonomy_rows',AsyncMock(return_value=[row()])), patch.object(bot,'ENABLE_GROUP_OFFER',True):
                self.assertTrue(await bot.maybe_show_help_offer(m,u))
                self.assertEqual(u['stage'],'post_action_reflection')
                self.assertEqual(u['dialogue_context'],'reflection')
                args=m.answer.call_args
                self.assertNotIn('€',args.args[0])
                self.assertNotIn('€',str(args.kwargs['reply_markup']))
                self.assertFalse(await bot.maybe_show_help_offer(m,u,final=True))
                self.assertIn('offer_seen_at',await get_user_profile(u['user_id'],path))


async def seed_useful(path, user):
    import aiosqlite
    from core.attempt_evidence import ensure_schema
    async with aiosqlite.connect(path) as db:
        await ensure_schema(db)
        for action in ('seed1', 'seed2'):
            await db.execute("INSERT INTO attempt_evidence (user_id,action_id,skill_id,calendar_date,completed,helpfulness,continued) VALUES (?,?,?,?,1,'helped',1)",
                             (user['user_id'], action, 'open_only', bot.local_date_for_user(user)))
        await db.commit()

class SupportContext(unittest.TestCase):
    def test_ceiling_needs_confirmed_executed_recurrence(self):
        from core.commercial import support_ceiling
        rows=[]
        for i in range(3):
            r=row(str(i));r.update(continued=0,calendar_date=f'2026-10-0{4+i}');rows.append(r)
        chain=dict(confirmed=True,source_refs=['0','1','2'])
        self.assertIn('3 выполненных',support_ceiling(rows,'2026-10-06',[chain]))
        self.assertEqual(support_ceiling(rows,'2026-10-06',[dict(chain,confirmed=False)]),'')
        rows[0]['completed']=0
        self.assertEqual(support_ceiling(rows,'2026-10-06',[chain]),'')
    def test_duplicate_refs_and_same_date_not_ceiling(self):
        from core.commercial import support_ceiling
        rr=[row(str(i)) for i in range(3)]
        for r in rr:r['continued']=0
        self.assertEqual(support_ceiling(rr,'2026-10-06',[dict(confirmed=True,source_refs=['0','1','2','2'])]),'')

class WeeklyInvitation(unittest.IsolatedAsyncioTestCase):
    async def test_review_precedes_optional_offer(self):
        calls=[]
        async def report(*args):calls.append('review')
        async def offer(*args,**kwargs):calls.append('offer')
        with patch.object(bot,'send_weekly_summary',report), patch.object(bot,'show_day3_offer',offer), patch.object(bot,'ENABLE_GROUP_OFFER',True):
            await bot.show_weekly_with_support(AsyncMock(),{})
        self.assertEqual(calls,['review','offer'])
