import json
import tempfile
import time
import unittest
from pathlib import Path

from core.skill_registry import FileSkillRegistry, SkillLibraryError


def card(skill_id, *, status="production", mechanism="evaluation_avoidance", fallback=("safe_fallback",)):
    return {
        "skill_id": skill_id, "version": "1.0.0", "status": status,
        "title": "Проверить прогноз", "short_title": "Проверка", "source_family": "CBT",
        "mechanisms": [mechanism], "action_targets": ["start"], "contexts": ["work"],
        "contraindications": ["acute_crisis"], "safety_level": "standard",
        "prerequisites": [], "fallback_skills": list(fallback), "next_skills": [],
        "difficulty_levels": [{"level": 1}, {"level": 2}],
        "variants": {"minimum": "Открыть контакт", "standard": "Сделать короткий звонок"},
        "minimum_successes": 2,
        "mastery_criteria": {"successful_practice_count": 2, "independent_use_count": 2},
        "maintenance_rule": "on_similar_mechanism", "generalization_contexts": ["health"],
        "completion_criteria": "Совершено одно проверяемое действие",
        "feedback_schema": {"action_started": "required"},
        "source_references": [{"internal_ref": "CBT_BEHAVIORAL_EXPERIMENTS_01"}],
        "reviewer_status": "reviewed",
        "trainer_texts": {"marsha": "Мягко", "skinny": "Коротко", "beck": "Проверим"},
    }


class FileSkillRegistryTests(unittest.TestCase):
    def load(self, rows, *, fail_closed=True):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        Path(directory.name, "cards.json").write_text(json.dumps(rows), encoding="utf-8")
        return FileSkillRegistry.load(directory.name, fail_closed=fail_closed)

    def test_candidates_are_indexed_and_versions_are_addressable(self):
        fallback = card("safe_fallback", status="experimental", fallback=())
        registry = self.load([fallback, card("cbt_check_prediction")])
        selected = registry.get_candidates("evaluation_avoidance", "work", "start")
        self.assertEqual([item.id for item in selected], ["cbt_check_prediction"])
        self.assertIsNotNone(registry.get("cbt_check_prediction", "1.0.0"))
        self.assertEqual(registry.get_next_level("cbt_check_prediction", 1), 2)

    def test_invalid_experimental_is_isolated(self):
        invalid = card("draft", status="experimental", mechanism="invented", fallback=())
        registry = self.load([invalid], fail_closed=True)
        self.assertIsNone(registry.get("draft"))
        self.assertTrue(registry.explain_validation_error("draft"))

    def test_invalid_production_fails_closed(self):
        invalid = card("bad", mechanism="invented")
        with self.assertRaises(SkillLibraryError):
            self.load([invalid])

    def test_manifest_separates_versions(self):
        first = card("safe_fallback", status="experimental", fallback=())
        second = dict(first, version="1.1.0")
        registry = self.load([first, second])
        self.assertEqual(len(registry.manifest()["cards"]), 2)

    def test_three_hundred_experimental_cards_load_without_duplicates(self):
        rows = [card(f"draft_{number:03d}", status="experimental", fallback=()) for number in range(300)]
        started = time.monotonic()
        registry = self.load(rows)
        self.assertEqual(len(registry.manifest()["cards"]), 300)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(registry.get_candidates("evaluation_avoidance", "work", "start"), ())
        self.assertEqual(registry.contour_counts()["experimental"], 300)
        self.assertEqual(registry.contour_counts()["production"], 0)


if __name__ == "__main__":
    unittest.main()
