import contextlib
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("live_ai_acceptance", Path(__file__).resolve().parents[1]/"scripts/live_ai_acceptance.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class LiveAcceptanceLimits(unittest.TestCase):
    def client(self, create=None):
        reply = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100,completion_tokens=20),model="gpt-4o-mini",choices=[SimpleNamespace(finish_reason="stop",message=SimpleNamespace(content="{}"))])
        create = create or Mock(return_value=reply)
        return live.CappedClient(SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))),create

    def test_request_bound_and_usage_accounting(self):
        client, create = self.client()
        client.create(model="gpt-4o-mini",messages=[{"content":"synthetic"}],max_tokens=9999)
        self.assertEqual(create.call_args.kwargs["max_tokens"],800)
        self.assertEqual(create.call_args.kwargs["timeout"],12)
        self.assertEqual(client.input_tokens,100)
        self.assertAlmostEqual(client.estimated_usd,.000027)

    def test_ninth_request_is_refused_before_provider(self):
        client, create = self.client()
        for _ in range(8):
            client.create(model="gpt-4o-mini",messages=[])
        with self.assertRaises(live.BudgetStop):
            client.create(model="gpt-4o-mini",messages=[])
        self.assertEqual(create.call_count,8)

    def test_failure_is_latched_and_secret_exception_is_not_printed(self):
        client, create = self.client(Mock(side_effect=RuntimeError("DO_NOT_PRINT_SECRET")))
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            for _ in range(2):
                with self.assertRaises(live.BudgetStop):
                    client.create(model="gpt-4o-mini",messages=[])
        self.assertEqual(create.call_count,1)
        self.assertNotIn("DO_NOT_PRINT_SECRET",output.getvalue())

    def test_unknown_price_model_or_large_input_is_not_sent(self):
        for kwargs in ({"model":"other-model","messages":[]},{"model":"gpt-4o-mini","messages":[{"content":"x"*24001}]}):
            client, create = self.client()
            with self.assertRaises(live.BudgetStop):
                client.create(**kwargs)
            create.assert_not_called()
