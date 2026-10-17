import unittest
from core.case_plan import case_plan

class Plan(unittest.TestCase):
    def test_perfection_requires_reported_evaluation_fear(self):
        plan=case_plan({'reported_difficulty':'Боюсь сделать плохо','behavioral_chain':{'task':'Написать письмо'}})
        self.assertEqual(plan['candidates'][0],'bad_first_step')
        plan=case_plan({'reported_difficulty':'Страх и скука','behavioral_chain':{'task':'Начать работу'}})
        self.assertNotEqual(plan['candidates'][0],'bad_first_step')

    def test_absent_or_negated_relief_is_not_inferred(self):
        for value in ('','Отвлекался','Облегчения не было','Не стало легче','Никакого облегчения'):
            with self.subTest(value=value):
                plan=case_plan({'behavioral_chain':{'task':'Открыть документ','immediate':value}})
                self.assertFalse(plan['relief_reported'])
                self.assertNotIn('закрепляется',plan['hypothesis'])

    def test_relationship_does_not_get_a_work_avoidance_explanation(self):
        plan=case_plan({'request_kind':'relationship','reported_difficulty':'Боюсь реакции','behavioral_chain':{'task':'Поговорить'}})
        self.assertEqual(plan['candidates'],['visible_next_step'])
        self.assertNotIn('откладыван',plan['hypothesis'])
