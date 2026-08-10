import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import bot
from core.experiment_core import BehavioralExperiment
from core.mechanism_model import MechanismHypothesis, SituationSnapshot
from core.outcome_model import ExperimentOutcome
from core.personalization_service import process_experiment_outcome
from core.skill_schema import Skill
from db import (
    create_behavioral_experiment, create_mechanism_hypothesis, create_situation_snapshot,
    init_db, transition_behavioral_experiment,
)


def reviewed_skill() -> Skill:
    return Skill(
        "check_the_facts_light", 2, "Проверить прогноз", "check_the_facts_light", "CBT",
        ("evaluation_avoidance",), ("start",), ("work", "health"), ("acute_crisis",), (),
        (), (), ("open_only",), (1, 2), "Открыть письмо", "Прочитать тему письма",
        "Тема прочитана", ("Что получилось?",), "Два успеха", 2,
        "on_similar_mechanism", ("health",), "CBT_REF", "production",
        {"marsha": "Мягко", "skinny": "Коротко", "beck": "Проверим"},
    )


class PersonalizationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.file = tempfile.NamedTemporaryFile(suffix=".db")
        await init_db(self.file.name)
        situation = await create_situation_snapshot(self.file.name, SituationSnapshot(
            None, 81, None, "письмо не открыто", "прочитать тему", "work", "start", 60, 50, "today",
        ))
        mechanism = await create_mechanism_hypothesis(self.file.name, MechanismHypothesis(
            None, situation, "evaluation_avoidance", "medium", ("избегает письма",),
            ("изменится ли прогноз",), (), "rules", True,
        ))
        experiment = BehavioralExperiment(
            None, 81, situation, "check_the_facts_light", "evaluation_avoidance", "work", 1,
            "Открыть письмо", "Прочитать тему", "Тема прочитана", None, None, "proposed",
            None, "first", "MECHANISM_MATCH", "beck", 0,
        )
        self.experiment_id = await create_behavioral_experiment(
            self.file.name, experiment, mechanism_hypothesis_id=mechanism,
        )
        revision = await transition_behavioral_experiment(
            self.file.name, self.experiment_id, status="accepted", expected_revision=0,
        )
        self.revision = await transition_behavioral_experiment(
            self.file.name, self.experiment_id, status="started", expected_revision=revision,
        )

    async def asyncTearDown(self):
        self.file.close()

    async def test_outcome_drives_mastery_and_one_versioned_policy_decision(self):
        result = await process_experiment_outcome(
            self.file.name,
            outcome=ExperimentOutcome(
                self.experiment_id, "yes", "yes", "better", 60, 30, True, False, None, None,
            ),
            skill=reviewed_skill(), expected_revision=self.revision,
        )
        self.assertEqual(result.decision.reason_code, "SUCCESS_NEEDS_INDEPENDENT_REPETITION")
        self.assertEqual(result.learning.state.status, "LEARNING")
        with sqlite3.connect(self.file.name) as db:
            decision = db.execute(
                "SELECT decision,reason_code,policy_version,ranking_version,skill_version "
                "FROM behavioral_experiment_decisions WHERE experiment_id=?", (self.experiment_id,),
            ).fetchone()
            mastery_events = db.execute(
                "SELECT COUNT(*) FROM skill_mastery_events WHERE experiment_id=?", (self.experiment_id,),
            ).fetchone()[0]
        self.assertEqual(decision, (
            "repeat", "SUCCESS_NEEDS_INDEPENDENT_REPETITION",
            "post-experiment-v1", "ranking-v1", "2.0.0",
        ))
        self.assertGreaterEqual(mastery_events, 2)

    async def test_worse_stops_in_safety_and_never_creates_resume_decision(self):
        with sqlite3.connect(self.file.name) as db:
            db.execute("INSERT INTO flow_states(user_id,current_step,revision) VALUES(81,'outcome_capture',3)")
            db.commit()
        result = await process_experiment_outcome(
            self.file.name,
            outcome=ExperimentOutcome(
                self.experiment_id, "partial", "no", "worse", 50, 80,
                False, False, None, "safety_deterioration",
            ),
            skill=reviewed_skill(), expected_revision=self.revision, expected_flow_revision=3,
        )
        self.assertEqual(result.decision.action.value, "safety")
        self.assertEqual(result.decision.reason_code, "SAFETY_DETERIORATION")
        with sqlite3.connect(self.file.name) as db:
            state = db.execute("SELECT status FROM behavioral_experiments WHERE id=?", (self.experiment_id,)).fetchone()[0]
            flow = db.execute("SELECT current_step FROM flow_states WHERE user_id=81").fetchone()[0]
            decision = db.execute(
                "SELECT decision,next_experiment_id FROM behavioral_experiment_decisions WHERE experiment_id=?",
                (self.experiment_id,),
            ).fetchone()
        self.assertEqual(state, "safety_stopped")
        self.assertEqual(flow, "safety_triage")
        self.assertEqual(decision, ("safety", None))

    def test_feature_gated_daily_selection_uses_ranking_decision(self):
        user = bot.default_user(82)
        user["test_access"] = True
        with patch.object(bot.product_config, "RANKING_ENGINE_ENABLED", True):
            selected = bot.select_daily_skill(user, {"completed_skills_effect_helped": ["open_only"]})
        self.assertIn("ranking_reason_codes", selected)
        self.assertEqual(selected["ranking_policy_version"], "ranking-v1")

    async def test_bot_feature_gate_persists_full_ranked_outcome_chain(self):
        user = bot.default_user(82)
        user["test_access"] = True
        old_path = bot.DB_PATH
        bot.DB_PATH = self.file.name
        try:
            with patch.object(bot.product_config, "RANKING_ENGINE_ENABLED", True), patch.object(
                bot.product_config, "LEARNING_ENGINE_ENABLED", True,
            ):
                selected = bot.select_daily_skill(user, {})
                user["current_skill"] = selected["skill_id"]
                user["daily_skill_id"] = selected["skill_id"]
                user["active_attempt"] = {
                    **bot.default_active_attempt(user), "current_skill_id": selected["skill_id"],
                }
                await bot._start_normalized_experiment_for_day(user, selected)
                experiment_id = user["active_attempt"]["behavioral_experiment_id"]
                await bot._process_normalized_feedback(user, {
                    "skill_id": selected["skill_id"], "completed": True, "partial": False,
                    "helpfulness": "helped", "continued_after_skill": False,
                })
        finally:
            bot.DB_PATH = old_path
        self.assertEqual(user["active_attempt"]["post_experiment_action"], "repeat")
        with sqlite3.connect(self.file.name) as db:
            row = db.execute(
                "SELECT status,decision_reason_code FROM behavioral_experiments WHERE id=?",
                (experiment_id,),
            ).fetchone()
        self.assertEqual(row, ("completed", "SUCCESS_NEEDS_INDEPENDENT_REPETITION"))


if __name__ == "__main__":
    unittest.main()
