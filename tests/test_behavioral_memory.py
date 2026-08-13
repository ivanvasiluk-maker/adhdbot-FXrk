import json
import sqlite3
import tempfile
import unittest

from core.experiment_core import BehavioralExperiment
from core.mechanism_model import MechanismHypothesis, SituationSnapshot
from core.outcome_model import ExperimentOutcome
from db import (
    capture_experiment_outcome, correct_behavioral_pattern, create_behavioral_experiment,
    create_mechanism_hypothesis, create_situation_snapshot, get_behavioral_memory, init_db,
    purge_expired_operational_context, store_operational_context,
    transition_behavioral_experiment,
)


class BehavioralMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.file = tempfile.NamedTemporaryFile(suffix=".db")
        await init_db(self.file.name)

    async def asyncTearDown(self):
        self.file.close()

    async def _complete(self, *, independent=False, success=True):
        situation_id = await create_situation_snapshot(self.file.name, SituationSnapshot(
            None, 77, None, "начать отчёт", "открыть документ", "work", "start", 50, 40, "today",
        ))
        mechanism_id = await create_mechanism_hypothesis(self.file.name, MechanismHypothesis(
            None, situation_id, "executive_start_deficit", "medium", ("документ не открыт",), (), (), "rules", False,
        ))
        experiment_id = await create_behavioral_experiment(self.file.name, BehavioralExperiment(
            None, 77, situation_id, "open_only", "executive_start_deficit", "work", 1,
            "Открыть документ", "начать отчёт", "документ открыт", None, None, "proposed",
            None, "first", "MECHANISM_MATCH", "beck", 0,
        ), mechanism_hypothesis_id=mechanism_id)
        revision = await transition_behavioral_experiment(self.file.name, experiment_id, status="accepted", expected_revision=0)
        revision = await transition_behavioral_experiment(self.file.name, experiment_id, status="started", expected_revision=revision)
        await capture_experiment_outcome(self.file.name, ExperimentOutcome(
            experiment_id, "yes" if success else "no", "yes" if success else "not_applicable",
            "better" if success else "same", 50, 30 if success else 50, success, independent,
            "короткая заметка", None if success else "too_hard",
        ), expected_revision=revision)
        return experiment_id

    async def test_similar_situation_returns_working_skill_and_barriers_with_evidence(self):
        successful_id = await self._complete(independent=True)
        failed_id = await self._complete(success=False)
        memory = await get_behavioral_memory(
            self.file.name, user_id=77, mechanism_code="executive_start_deficit", context_domain="work",
        )
        self.assertEqual(memory["barriers"], ["too_hard"])
        self.assertEqual(memory["working_skills"][0]["effectiveness_band"], "working")
        self.assertEqual(
            set(memory["mechanism_evidence_refs"]),
            {f"experiment:{successful_id}", f"experiment:{failed_id}"},
        )

    async def test_user_can_correct_and_delete_pattern_without_deleting_experiment(self):
        experiment_id = await self._complete(independent=True)
        await correct_behavioral_pattern(
            self.file.name, user_id=77, pattern_code="start_block", summary="Мне помогает видимый файл",
            correction_id="telegram-update-12",
        )
        with sqlite3.connect(self.file.name) as db:
            summary, refs = db.execute(
                "SELECT summary,evidence_refs FROM behavioral_patterns WHERE user_id=77"
            ).fetchone()
        self.assertEqual(summary, "Мне помогает видимый файл")
        self.assertEqual(json.loads(refs), ["user_correction:telegram-update-12"])
        await correct_behavioral_pattern(
            self.file.name, user_id=77, pattern_code="start_block", summary="",
            correction_id="telegram-update-13", delete=True,
        )
        with sqlite3.connect(self.file.name) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM behavioral_patterns").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM behavioral_experiments WHERE id=?", (experiment_id,)).fetchone()[0], 1)

    async def test_raw_context_is_separate_and_expires(self):
        await store_operational_context(self.file.name, user_id=77, raw_context="temporary details", ttl_seconds=60)
        self.assertEqual(await purge_expired_operational_context(self.file.name, now="9999-12-31T00:00:00+00:00"), 1)


if __name__ == "__main__":
    unittest.main()
