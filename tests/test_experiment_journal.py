import tempfile
import unittest

from core.experiment_core import BehavioralExperiment
from core.experiment_journal import (
    anonymized_journal_export, build_experiment_journal, render_experiment_journal,
)
from core.mechanism_model import MechanismHypothesis, SituationSnapshot
from core.outcome_model import ExperimentOutcome
from db import (
    capture_experiment_outcome, create_behavioral_experiment, create_mechanism_hypothesis,
    create_situation_snapshot, get_experiment_journal_records, get_skill_mastery_history,
    init_db, record_skill_mastery_transition, transition_behavioral_experiment,
)


class ExperimentJournalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.file = tempfile.NamedTemporaryFile(suffix=".db")
        await init_db(self.file.name)

    async def asyncTearDown(self):
        self.file.close()

    async def _experiment(self, *, parent=None, progression="first", task="начать отчёт"):
        situation_id = await create_situation_snapshot(self.file.name, SituationSnapshot(
            None, 51, None, task, "открыть документ", "work", "start", 40, 60, "today",
        ))
        hypothesis_id = await create_mechanism_hypothesis(self.file.name, MechanismHypothesis(
            None, situation_id, "executive_start_deficit", "medium", ("документ закрыт",),
            ("поможет ли короткий вход",), (), "rules", False,
        ))
        experiment_id = await create_behavioral_experiment(self.file.name, BehavioralExperiment(
            None, 51, situation_id, "open_only", "executive_start_deficit", "work", 1,
            "Открой документ", "начать отчёт", "Документ открыт", None, None, "proposed",
            parent, progression, "MECHANISM_MATCH", "beck", 0,
        ), mechanism_hypothesis_id=hypothesis_id)
        revision = await transition_behavioral_experiment(
            self.file.name, experiment_id, status="accepted", expected_revision=0,
        )
        revision = await transition_behavioral_experiment(
            self.file.name, experiment_id, status="started", expected_revision=revision,
        )
        await capture_experiment_outcome(self.file.name, ExperimentOutcome(
            experiment_id, "yes", "yes", "better", 40, 20, True, True, None, None,
        ), expected_revision=revision)
        return experiment_id

    async def test_journal_is_reconstructed_from_normalized_experiment_tables(self):
        experiment_id = await self._experiment()
        records = await get_experiment_journal_records(self.file.name, user_id=51)
        journal = build_experiment_journal(records)
        self.assertEqual(journal[0].experiment_id, experiment_id)
        text = render_experiment_journal(journal)
        self.assertIn("Ситуация: начать отчёт", text)
        self.assertIn("Что проверяли:", text)
        self.assertIn("Действие: Открой документ", text)
        self.assertIn("Критерий выполнен".lower(), text.lower())
        self.assertNotIn("prompt", text.lower())
        self.assertNotIn("llm", text.lower())

    async def test_progression_chain_can_be_opened_from_root(self):
        root = await self._experiment()
        child = await self._experiment(parent=root, progression="simplify", task="вернуться к отчёту")
        records = await get_experiment_journal_records(
            self.file.name, user_id=51, root_experiment_id=root,
        )
        self.assertEqual({row["experiment_id"] for row in records}, {root, child})
        self.assertEqual(records[1]["parent_experiment_id"], root)

    async def test_mastery_history_is_separate_and_requires_matching_experiment(self):
        experiment_id = await self._experiment()
        await record_skill_mastery_transition(
            self.file.name, user_id=51, skill_id="open_only", experiment_id=experiment_id,
            from_status="PRACTICING", to_status="MASTERED", reason_code="INDEPENDENT_SUCCESS_THRESHOLD",
        )
        history = await get_skill_mastery_history(self.file.name, user_id=51, skill_id="open_only")
        self.assertEqual(history[0]["experiment_id"], experiment_id)
        self.assertEqual(history[0]["to_status"], "MASTERED")
        with self.assertRaisesRegex(ValueError, "matching experiment"):
            await record_skill_mastery_transition(
                self.file.name, user_id=51, skill_id="other", experiment_id=experiment_id,
                from_status="NEW", to_status="LEARNING", reason_code="STARTED",
            )

    async def test_export_uses_anonymous_ids_and_no_personal_text(self):
        await self._experiment(task="секретная личная задача")
        journal = build_experiment_journal(await get_experiment_journal_records(self.file.name, user_id=51))
        exported = anonymized_journal_export(journal, user_id=51, secret_salt="private-test-salt")
        self.assertNotEqual(exported[0]["anonymous_user_id"], "51")
        self.assertNotIn("experiment_id", exported[0])
        self.assertNotIn("секретная", str(exported))


if __name__ == "__main__":
    unittest.main()
