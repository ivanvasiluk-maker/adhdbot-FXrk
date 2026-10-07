import unittest
from core.product_metrics import product_metrics
from core.commercial import support_ceiling
from core.trainer_voice import VoiceContent, render_message
from core.addressing import render_address
from core.contextual_memory import similar
from core.conversation_intent import resolve_intent

TODAY='2026-10-07'
def row(a='a',**kw):
    r=dict(action_id=a,user_id=1,skill_id='s',calendar_date='2026-10-05',completed=1,
           helpfulness='helped',continued=0,mechanism='entry',context_domain='work',target_function='START')
    r.update(kw);return r

class FinalAudit(unittest.TestCase):
    def test_question_identity_is_scoped_to_user(self):
        records=[dict(user_id=uid,assessment_id='question1',calendar_date=TODAY,confirmed=answer)
                 for uid,answer in ((1,True),(2,False))]
        metrics=product_metrics([1,2],[],TODAY,model_assessments=records,memory_assessments=records)
        for key in ('model_accuracy','memory_value'):
            self.assertEqual(metrics[key],dict(numerator=1,denominator=2,rate=.5))
    def test_draft_is_not_activation(self):
        r=row();r.update(completed=None,helpfulness=None,continued=None)
        self.assertEqual(product_metrics([1],[r],TODAY)['activation']['numerator'],0)
        r['shown_at']='2026-10-05T10:00:00Z'
        self.assertEqual(product_metrics([1],[r],TODAY)['activation']['numerator'],1)
    def test_report_proves_activation_without_old_display_stamp(self):
        self.assertEqual(product_metrics([1],[row()],TODAY)['activation']['numerator'],1)
    def test_recovered_pattern_does_not_sell_old_ceiling(self):
        rr=[row(str(i),calendar_date=f'2026-10-0{4+i}') for i in range(3)]
        chain=dict(confirmed=True,source_refs=['0','1','2'],mechanism='entry',domain='work')
        self.assertTrue(support_ceiling(rr,TODAY,[chain]))
        rr.append(row('recovered',calendar_date=TODAY,continued=1))
        self.assertFalse(support_ceiling(rr,TODAY,[chain]))
    def test_different_context_does_not_erase_confirmed_pattern(self):
        rr=[row(str(i),calendar_date=f'2026-10-0{4+i}') for i in range(3)]
        rr.append(row('other',calendar_date=TODAY,continued=1,context_domain='home'))
        self.assertTrue(support_ceiling(rr,TODAY,[dict(confirmed=True,source_refs=['0','1','2'],mechanism='entry',domain='work')]))
    def test_current_worse_does_not_prompt_ceiling(self):
        rr=[row(str(i),calendar_date=f'2026-10-0{4+i}') for i in range(3)]
        rr.append(row('worse',calendar_date=TODAY,helpfulness='worse'))
        self.assertFalse(support_ceiling(rr,TODAY,[dict(confirmed=True,source_refs=['0','1','2'],mechanism='entry',domain='work')]))
    def test_summary_does_not_invent_stay_failure(self):
        for trainer in ('skinny','marsha','beck'):
            c=VoiceContent('summary',facts={'start_result':'STRONG_SUCCESS','start_skill_name':'Первый шаг'})
            text=render_message(trainer,c).text
            self.assertIn('пока не известен',text)
            self.assertNotIn('не удержал',text)
    def test_both_successes_are_not_described_as_failure(self):
        for trainer in ('skinny','marsha','beck'):
            c=VoiceContent('summary',facts={'start_result':'STRONG_SUCCESS','stay_result':'STRONG_SUCCESS'})
            self.assertIn('тоже подтверждён',render_message(trainer,c).text)
    def test_address_preserves_quote_and_url(self):
        for form in ('male','female','neutral'):
            text=render_address('Ты можешь попробовать. «ты меня не понял» https://example.com/ty',{'address_mode':'vy','address_form':form})
            self.assertIn('Вы можете',text)
            self.assertIn('«ты меня не понял»',text)
            self.assertIn('https://example.com/ty',text)
    def test_declining_application_remains_application_input(self):
        self.assertEqual(resolve_intent('ты меня не понял','offer_request_form'),'APPLICATION_DATA')
    def test_latest_worse_excludes_familiar_skill(self):
        rr=[row('old',continued=1,reported_at='2026-10-05T10:00:00Z'),row('new',helpfulness='worse',reported_at='2026-10-07T10:00:00Z')]
        self.assertIsNone(similar(rr,mechanism='entry',target='START',domain='work',available={'s'}))
