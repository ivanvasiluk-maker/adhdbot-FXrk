import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_patch_sequence import LEDGER, validate
from scripts.check_patch_commits import validate_commits


class PatchRolloutTests(unittest.TestCase):
    def test_complete_ordered_ledger_is_valid(self):
        self.assertEqual(validate(), [])
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in data["patches"]], [f"PATCH-{i:02d}" for i in range(18)])

    def test_missing_or_reordered_patch_is_rejected(self):
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        data["patches"][1], data["patches"][2] = data["patches"][2], data["patches"][1]
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory, "sequence.json")
            broken.write_text(json.dumps(data), encoding="utf-8")
            self.assertTrue(validate(broken))

    def test_every_patch_has_dedicated_acceptance_commands(self):
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        self.assertTrue(all(item["tests"] for item in data["patches"]))
        self.assertEqual(data["patches"][-1]["tests"], ["python scripts/regression_gate.py"])
        self.assertEqual(data["enforced_after_commit"], "path-introduction:patches/sequence.json")
        self.assertTrue(data["allow_single_squash_commit"])

    def test_commit_checker_rejects_multiple_owners_and_backwards_order(self):
        self.assertTrue(validate_commits([("a" * 40, "PATCH-07: change PATCH-08 too")]))
        self.assertTrue(validate_commits([
            ("a" * 40, "PATCH-14: learning"),
            ("b" * 40, "PATCH-13: offer"),
        ]))

    def test_commit_checker_allows_future_patch_numbers(self):
        self.assertEqual(validate_commits([
            ("a" * 40, "PATCH-17: gate"),
            ("b" * 40, "PATCH-18: registry"),
            ("c" * 40, "PATCH-20: offer paths"),
        ]), [])


if __name__ == "__main__":
    unittest.main()
