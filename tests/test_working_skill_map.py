import sqlite3
import tempfile
import unittest

from core.ranking_engine import PersonalSkillState, RankingInput, choose_skill
from core.skill_schema import Skill
from core.working_skill_map import SkillMapEntry, build_working_skill_map, render_working_skill_map
from db import get_disabled_skill_ids, init_db, set_skill_recommendation_disabled


def entry(skill_id="open_only", *, band="working", successes=1, refs=("experiment:7",), mastery=""):
    return SkillMapEntry(
        skill_id, "Открыть только файл", "work", band, mastery, 2, successes, 1,
        "2026-08-06T10:00:00+00:00", refs,
    )


def production_skill(skill_id):
    return Skill(
        skill_id, 2, skill_id, skill_id, "OTHER", ("overwhelm",), ("start",), ("work",),
        (), (), (), (), (), (1,), "min", "standard", "done", (), "mastery", 2,
        "maintain", ("work",), "test", "production",
        {"marsha": "m", "skinny": "s", "beck": "b"},
    )


class WorkingSkillMapTests(unittest.TestCase):
    def test_working_requires_success_and_experiment_evidence(self):
        valid = entry()
        no_success = entry("no_success", successes=0)
        no_evidence = entry("no_evidence", refs=("user_correction:1",))
        result = build_working_skill_map((valid, no_success, no_evidence))
        self.assertEqual([item.skill_id for item in result.works_for_me], ["open_only"])

    def test_renderer_answers_what_helps_without_ids_scores_or_percentages(self):
        result = build_working_skill_map((entry(),))
        text = render_working_skill_map(result)
        self.assertIn("Что помогает именно мне", text)
        self.assertIn("Открыть только файл", text)
        self.assertIn("Самостоятельно: 1", text)
        self.assertIn("не предлагать", text)
        self.assertNotIn("open_only", text)
        self.assertNotIn("score", text.lower())
        self.assertNotIn("%", text)

    def test_unreliable_is_neutral_not_fit_section(self):
        result = build_working_skill_map((entry(band="unreliable", successes=0),))
        text = render_working_skill_map(result)
        self.assertIn("Пока не подошло", text)
        self.assertIn("подобрать другой способ", text)

    def test_disabled_recommendation_is_rejected_by_ranking(self):
        skills = [production_skill("disabled"), production_skill("allowed")]
        data = RankingInput(
            {"overwhelm": 1.0}, "start", "work", 1, "marsha",
            personal_states={"disabled": PersonalSkillState("disabled", recommendation_disabled=True)},
        )
        decision, _ = choose_skill(skills, data)
        self.assertEqual(decision.selected_skill_id, "allowed")
        rejected = {item.skill_id: item.reason_codes for item in decision.rejected_top_candidates}
        self.assertIn("USER_DISABLED_RECOMMENDATION", rejected["disabled"])


class SkillPreferencePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_can_disable_and_reenable_without_deleting_evidence(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            with sqlite3.connect(file.name) as db:
                db.execute(
                    """INSERT INTO user_skill_effectiveness
                       (user_id,skill_id,mechanism_code,context_domain,attempts_count,successes_count,
                        independent_successes,worse_count,last_used_at,effectiveness_band,evidence_refs_json)
                       VALUES(1,'open_only','overwhelm','work',1,1,1,0,'2026-08-06','working','[\"experiment:1\"]')"""
                )
                db.commit()
            await set_skill_recommendation_disabled(
                file.name, user_id=1, skill_id="open_only", disabled=True, correction_id="update-1",
            )
            self.assertEqual(await get_disabled_skill_ids(file.name, user_id=1), frozenset({"open_only"}))
            await set_skill_recommendation_disabled(
                file.name, user_id=1, skill_id="open_only", disabled=False, correction_id="update-2",
            )
            self.assertEqual(await get_disabled_skill_ids(file.name, user_id=1), frozenset())
            with sqlite3.connect(file.name) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM user_skill_effectiveness").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
