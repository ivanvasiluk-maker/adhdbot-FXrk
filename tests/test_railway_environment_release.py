import unittest
from unittest.mock import patch

from core.product_config import deployment_environment, assert_production_payment_safety


class RailwayEnvironmentRelease(unittest.TestCase):
    def test_provider_production_without_app_env_rejects_test_payment(self):
        with patch.dict("os.environ", {"RAILWAY_ENVIRONMENT_NAME":"production"}, clear=True):
            self.assertEqual(deployment_environment(), "production")
            with self.assertRaises(RuntimeError):
                assert_production_payment_safety(payment_accept_any=True)
            assert_production_payment_safety(payment_accept_any=False)

    def test_explicit_development_does_not_override_provider_production(self):
        with patch.dict("os.environ", {"APP_ENV":"development", "RAILWAY_ENVIRONMENT_NAME":"production"}, clear=True):
            self.assertEqual(deployment_environment(), "production")
            with self.assertRaises(RuntimeError):
                assert_production_payment_safety(payment_accept_any=True)

    def test_legacy_provider_name_and_staging(self):
        for key in ("RAILWAY_ENVIRONMENT_NAME", "RAILWAY_ENVIRONMENT"):
            with patch.dict("os.environ", {key:"production"}, clear=True):
                self.assertEqual(deployment_environment(), "production")
            with patch.dict("os.environ", {key:"staging"}, clear=True):
                self.assertEqual(deployment_environment(), "staging")
                assert_production_payment_safety(payment_accept_any=True)

    def test_local_development_and_explicit_production(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(deployment_environment(), "development")
        with patch.dict("os.environ", {"APP_ENV":"prod"}, clear=True):
            self.assertEqual(deployment_environment(), "production")
