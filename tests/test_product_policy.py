import unittest

from core.product_policy import evaluate_feature


class ProductPolicyTests(unittest.TestCase):
    def test_missing_goal_is_rejected(self):
        self.assertEqual(evaluate_feature({"success_metric": "activation"})["reason_code"], "MISSING_BEHAVIORAL_GOAL")

    def test_missing_measurement_is_rejected(self):
        self.assertEqual(evaluate_feature({"behavioral_goal": "start"})["reason_code"], "MISSING_MEASURABLE_EFFECT")

    def test_measurable_constitutional_feature_is_allowed(self):
        decision = evaluate_feature({"behavioral_goal": "recover", "success_metric": "24h recovery rate"})
        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["reason_code"], "ALLOWED")


if __name__ == "__main__":
    unittest.main()
