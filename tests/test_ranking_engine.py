import unittest

from core.ranking_engine import (
    POLICY_VERSION, PersonalSkillState, RankingInput, choose_skill, explain_decision_for_user,
)
from core.skill_schema import Skill


def skill(skill_id, mechanisms, *, contexts=("work",), contraindications=(), difficulty=(1, 2), quality="production"):
    return Skill(
        skill_id, 2, skill_id, skill_id, "OTHER", tuple(mechanisms), ("start",), tuple(contexts),
        tuple(contraindications), (), (), (), ("fallback",) if skill_id != "fallback" else ("new",),
        tuple(difficulty), "minimum", "standard", "criterion", (), "mastery", 2, "maintain",
        tuple(contexts), "test", quality, {"marsha": "m", "skinny": "s", "beck": "b"},
    )


class RankingEngineTests(unittest.TestCase):
    def setUp(self):
        self.skills = [
            skill("fallback", ("overwhelm",)),
            skill("working", ("overwhelm",)),
            skill("new", ("overwhelm",)),
            skill("curriculum", ("attention_drift",)),
            skill("unsafe", ("overwhelm",), contraindications=("acute_panic",)),
        ]

    def data(self, **changes):
        base = dict(
            mechanism_probabilities={"overwhelm": 1.0, "attention_drift": 0.0}, action_phase="start",
            context_domain="work", requested_difficulty=1, trainer_style="marsha",
            policy_version=POLICY_VERSION,
        )
        base.update(changes)
        return RankingInput(**base)

    def test_contextual_reuse_working_skill_beats_new(self):
        state = PersonalSkillState("working", mastery_status="GENERALIZING", effectiveness_band="working")
        decision, ranked = choose_skill(self.skills, self.data(personal_states={"working": state}))
        self.assertEqual(decision.selected_skill_id, "working")
        self.assertIn("REUSE_WORKING_SKILL", decision.reason_codes)
        self.assertTrue(ranked[0].breakdown)

    def test_recent_failed_exact_variant_is_rejected(self):
        state = PersonalSkillState("working", effectiveness_band="working", recent_failed_exact_variant=True)
        decision, _ = choose_skill(self.skills, self.data(personal_states={"working": state}))
        self.assertNotEqual(decision.selected_skill_id, "working")
        rejected = {item.skill_id: item.reason_codes for item in decision.rejected_top_candidates}
        self.assertIn("RECENT_EXACT_VARIANT_FAILED", rejected["working"])

    def test_curriculum_bonus_loses_to_mechanism_match(self):
        decision, _ = choose_skill(self.skills, self.data(curriculum_skill_ids=("curriculum",)))
        self.assertNotEqual(decision.selected_skill_id, "curriculum")
        self.assertIn("MECHANISM_MATCH", decision.reason_codes)

    def test_safety_contraindication_excludes_candidate(self):
        decision, _ = choose_skill(self.skills, self.data(
            active_contraindications=frozenset({"acute_panic"}),
            personal_states={"unsafe": PersonalSkillState("unsafe", effectiveness_band="working")},
        ))
        self.assertNotEqual(decision.selected_skill_id, "unsafe")
        rejected = {item.skill_id: item.reason_codes for item in decision.rejected_top_candidates}
        self.assertIn("SAFETY_CONTRAINDICATION", rejected["unsafe"])

    def test_same_input_and_policy_is_deterministic_and_explanation_hides_codes(self):
        first, _ = choose_skill(self.skills, self.data())
        second, _ = choose_skill(self.skills, self.data())
        self.assertEqual(first, second)
        text = explain_decision_for_user(first)
        self.assertNotIn("MECHANISM_MATCH", text)
        self.assertNotIn("score", text.lower())

    def test_decision_keeps_top_eligible_losers_for_audit(self):
        decision, _ = choose_skill(self.skills, self.data())
        losers = {item.skill_id: item.reason_codes for item in decision.rejected_top_candidates}
        self.assertTrue(losers)
        self.assertTrue(any("LOWER_POLICY_RANK" in codes for codes in losers.values()))


if __name__ == "__main__":
    unittest.main()
