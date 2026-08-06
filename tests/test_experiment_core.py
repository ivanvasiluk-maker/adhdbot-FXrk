import sqlite3
import tempfile
import unittest

from core.experiment_core import BehavioralExperiment, build_experiment_card
from core.mechanism_model import MechanismHypothesis, SituationSnapshot
from db import (
    create_behavioral_experiment, create_mechanism_hypothesis,
    create_situation_snapshot, init_db, record_behavioral_outcome_and_decision,
    transition_behavioral_experiment,
)


class ExperimentCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.file = tempfile.NamedTemporaryFile(suffix=".db")
        await init_db(self.file.name)
        self.situation_id = await create_situation_snapshot(self.file.name, SituationSnapshot(
            None, 7, None, "отчёт ещё не открыт", "открыть файл", "work", "start", 60, 40, "today",
        ))
        self.mechanism_id = await create_mechanism_hypothesis(self.file.name, MechanismHypothesis(
            None, self.situation_id, "executive_start_deficit", "medium",
            ("пользователь сообщил, что файл не открыт",), ("достаточно ли открытия для старта",), (), "rules", True,
        ))

    async def asyncTearDown(self):
        self.file.close()

    def experiment(self, **changes):
        values = dict(
            id=None, user_id=7, situation_id=self.situation_id, skill_id="open_only",
            mechanism_code="executive_start_deficit", context_domain="work",
            difficulty_level=1, instruction_variant="Открой файл, ничего больше.",
            target_action="Открыть файл", success_criterion="Файл открыт",
            started_at=None, completed_at=None, status="proposed", parent_experiment_id=None,
            progression_type="first", decision_reason_code="MECHANISM_MATCH",
            trainer_style="marsha", state_revision=0,
        )
        values.update(changes)
        return BehavioralExperiment(**values)

    async def test_full_chain_and_objective_card(self):
        experiment = self.experiment()
        card = build_experiment_card(experiment, duration="60 секунд")
        self.assertEqual(card["actions"], ("need_simpler", "report_result"))
        self.assertEqual(card["completion_criterion"], "Файл открыт")
        experiment_id = await create_behavioral_experiment(
            self.file.name, experiment, mechanism_hypothesis_id=self.mechanism_id,
        )
        revision = await transition_behavioral_experiment(self.file.name, experiment_id, status="accepted", expected_revision=0)
        revision = await transition_behavioral_experiment(self.file.name, experiment_id, status="started", expected_revision=revision)
        await transition_behavioral_experiment(self.file.name, experiment_id, status="completed", expected_revision=revision)
        outcome_id, decision_id = await record_behavioral_outcome_and_decision(
            self.file.name, experiment_id, criterion_met=True, observed_result="файл открыт",
            decision="repeat", reason_code="CRITERION_MET",
        )
        self.assertGreater(outcome_id, 0)
        self.assertGreater(decision_id, 0)

    async def test_only_one_productive_experiment(self):
        await create_behavioral_experiment(self.file.name, self.experiment(), mechanism_hypothesis_id=self.mechanism_id)
        with self.assertRaisesRegex(ValueError, "already has"):
            await create_behavioral_experiment(self.file.name, self.experiment(), mechanism_hypothesis_id=self.mechanism_id)

    def test_progression_never_reuses_original_record(self):
        with self.assertRaisesRegex(ValueError, "parent"):
            self.experiment(progression_type="repeat")

    async def test_chain_rejects_mismatched_mechanism(self):
        with self.assertRaisesRegex(ValueError, "preserve"):
            await create_behavioral_experiment(
                self.file.name, self.experiment(mechanism_code="overwhelm"),
                mechanism_hypothesis_id=self.mechanism_id,
            )


if __name__ == "__main__":
    unittest.main()
