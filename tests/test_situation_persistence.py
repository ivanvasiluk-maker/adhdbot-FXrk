import sqlite3
import tempfile
import unittest

from core.mechanism_model import MechanismHypothesis, SituationSnapshot
from db import (
    create_experiment, create_mechanism_hypothesis, create_situation_snapshot,
    init_db,
)


class SituationPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_experiment_links_situation_and_mechanism_with_clean_outcome(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            situation_id = await create_situation_snapshot(file.name, SituationSnapshot(
                None, 42, None, "нужно начать отчёт", "открыть документ",
                "work", "start", 55, 40, "today",
            ))
            mechanism_id = await create_mechanism_hypothesis(file.name, MechanismHypothesis(
                None, situation_id, "executive_start_deficit", "medium",
                ("пользователь ещё не открыл документ",), ("поможет ли видимый первый шаг",), (), "rules", False,
            ))
            first = await create_experiment(
                file.name, user_id=42, situation_id=situation_id,
                mechanism_hypothesis_id=mechanism_id, skill_id="open_only",
            )
            with sqlite3.connect(file.name) as db:
                db.execute("UPDATE experiments SET outcome='done' WHERE id=?", (first,))
                db.commit()
            second = await create_experiment(
                file.name, user_id=42, situation_id=situation_id,
                mechanism_hypothesis_id=mechanism_id, skill_id="one_visible_step",
            )
            with sqlite3.connect(file.name) as db:
                row = db.execute(
                    "SELECT situation_id,mechanism_hypothesis_id,outcome FROM experiments WHERE id=?",
                    (second,),
                ).fetchone()
            self.assertEqual(row, (situation_id, mechanism_id, None))


if __name__ == "__main__":
    unittest.main()
