import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import product_config
from db import (
    USER_STATE_SCHEMA_VERSION, init_db, migrate_db, migrate_legacy_data, startup_schema_check,
)
from scripts.backup_sqlite import backup_database


class LegacyMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_database_opens_and_additive_migration_is_recorded(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            with sqlite3.connect(file.name) as db:
                db.execute("CREATE TABLE users(user_id INTEGER PRIMARY KEY, stage TEXT, profile_json TEXT)")
                db.execute("INSERT INTO users VALUES(1,'training','{}')")
                db.commit()
            await init_db(file.name)
            await migrate_db(file.name)
            check = await startup_schema_check(file.name)
            self.assertTrue(check["ok"])
            with sqlite3.connect(file.name) as db:
                self.assertIsNotNone(db.execute(
                    "SELECT applied_at FROM schema_migrations WHERE version=?", (USER_STATE_SCHEMA_VERSION,),
                ).fetchone())
                self.assertEqual(db.execute("SELECT stage FROM users WHERE user_id=1").fetchone()[0], "training")

    async def test_adapters_are_idempotent_and_do_not_invent_mastery(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            await migrate_db(file.name)
            profile = json.dumps({"working_strategies": ["open_only"]})
            with sqlite3.connect(file.name) as db:
                db.execute(
                    "INSERT INTO users(user_id,telegram_id,stage,current_step,profile_json,trainer_key) VALUES(1,1,'training',NULL,?,'beck')",
                    (profile,),
                )
                db.execute(
                    """INSERT INTO skill_attempts(day_id,user_id,skill_id,task_id,result,barrier,created_at)
                       VALUES('legacy-day',1,'open_only','legacy-task','done','','2026-08-01T10:00:00Z')"""
                )
                db.commit()
            first = await migrate_legacy_data(file.name)
            second = await migrate_legacy_data(file.name)
            self.assertEqual(first, {"flow_states": 1, "attempts": 1, "strategies": 1})
            self.assertEqual(second, {"flow_states": 0, "attempts": 0, "strategies": 0})
            with sqlite3.connect(file.name) as db:
                self.assertEqual(db.execute("SELECT current_step FROM flow_states WHERE user_id=1").fetchone()[0], "training")
                effectiveness = db.execute(
                    "SELECT effectiveness_band,migration_confidence FROM user_skill_effectiveness WHERE user_id=1 AND skill_id='open_only'"
                ).fetchone()
                mastery = db.execute(
                    "SELECT status,migration_confidence FROM skill_mastery WHERE user_id=1 AND skill_id='open_only'"
                ).fetchone()
                self.assertEqual(effectiveness, ("unknown", "low"))
                self.assertEqual(mastery, ("NEW", "low"))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM skill_attempts").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM behavioral_experiments").fetchone()[0], 1)

    async def test_new_tables_do_not_modify_legacy_flow_when_adapter_is_not_run(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            await migrate_db(file.name)
            with sqlite3.connect(file.name) as db:
                db.execute("INSERT INTO users(user_id,telegram_id,stage,profile_json) VALUES(2,2,'legacy_stage','{}')")
                db.commit()
                self.assertEqual(db.execute("SELECT stage FROM users WHERE user_id=2").fetchone()[0], "legacy_stage")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM behavioral_experiments WHERE user_id=2").fetchone()[0], 0)


class RolloutAndBackupTests(unittest.TestCase):
    def test_test_user_can_switch_engine_while_default_stays_legacy(self):
        with patch.object(product_config, "NEW_ARCHITECTURE_ENABLED", False), patch.object(
            product_config, "NEW_ARCHITECTURE_TEST_COHORT_ENABLED", True,
        ), patch.object(product_config, "ADMIN_IDS", frozenset()), patch.object(
            product_config, "NEW_ARCHITECTURE_COHORT_IDS", frozenset({99}),
        ):
            self.assertFalse(product_config.use_new_architecture(1))
            self.assertTrue(product_config.use_new_architecture(1, is_test_user=True))
            self.assertTrue(product_config.use_new_architecture(99))

    def test_backup_command_creates_integrity_checked_copy_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "production.db"
            destination = Path(directory) / "production.backup"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, stage TEXT)")
                db.execute("INSERT INTO users VALUES(1,'training')")
                db.commit()
            backup_database(source, destination)
            with sqlite3.connect(destination) as db:
                self.assertEqual(db.execute("SELECT stage FROM users WHERE id=1").fetchone()[0], "training")
            with self.assertRaises(FileExistsError):
                backup_database(source, destination)


if __name__ == "__main__":
    unittest.main()
