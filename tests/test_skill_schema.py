import unittest

from core.skill_schema import Skill, SkillRegistry, SkillRegistryError
from skills import PRODUCTION_SKILL_IDS, SKILL_REGISTRY, rankable_skill_ids


class SkillSchemaTests(unittest.TestCase):
    def test_legacy_adapter_does_not_fabricate_library_cards(self):
        self.assertTrue(SKILL_REGISTRY.all())
        self.assertFalse(any(skill.id.startswith("library_contour_") for skill in SKILL_REGISTRY.all()))
        self.assertGreaterEqual(len(PRODUCTION_SKILL_IDS), 30)
        self.assertLessEqual(len(PRODUCTION_SKILL_IDS), 50)
        self.assertTrue(all(skill.quality_status == "production" for skill in SKILL_REGISTRY.rankable()))
        self.assertFalse(any(skill.id.startswith("library_contour_") for skill in SKILL_REGISTRY.rankable()))

    def test_reviewed_requires_explicit_tester_flag_and_feature_flag(self):
        production = rankable_skill_ids(tester=False)
        tester = rankable_skill_ids(tester=True)
        self.assertTrue(production <= tester)
        if SKILL_REGISTRY.include_reviewed:
            self.assertGreater(len(tester), len(production))
        else:
            self.assertEqual(tester, production)

    def test_every_production_skill_has_simplify_and_outcome_criterion(self):
        for skill in SKILL_REGISTRY.rankable():
            self.assertTrue(skill.fallback_skills, skill.id)
            self.assertTrue(skill.min_variant.strip(), skill.id)
            self.assertTrue(skill.completion_criterion.strip(), skill.id)

    def test_invalid_cross_reference_fails_registry_startup(self):
        invalid = Skill(
            "bad", 2, "Bad", "bad", "OTHER", ("overwhelm",), ("start",), ("work",),
            (), (), (), (), ("missing",), (1,), "min", "standard", "done", (), "twice", 2,
            "repeat", (), "review", "production",
            {"marsha": "a", "skinny": "b", "beck": "c"},
        )
        with self.assertRaisesRegex(SkillRegistryError, "missing references"):
            SkillRegistry([invalid])

    def test_legacy_view_preserves_old_card_shape(self):
        card = SKILL_REGISTRY.legacy_view()["open_only"]
        self.assertIn("name", card)
        self.assertIn("how", card)
        self.assertIn("minimum", card)


if __name__ == "__main__":
    unittest.main()
