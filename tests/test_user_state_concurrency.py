import tempfile
import unittest

from db import StaleUserWriteError, get_user, init_db, migrate_db, save_user


class UserStateConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_snapshot_cannot_overwrite_newer_stage(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            await migrate_db(file.name)
            first = await get_user(5001, file.name)
            stale = await get_user(5001, file.name)
            first["stage"] = "confirm_analysis"
            await save_user(first, file.name)
            stale["stage"] = "training"
            with self.assertRaises(StaleUserWriteError):
                await save_user(stale, file.name)
            current = await get_user(5001, file.name)
            self.assertEqual(current["stage"], "confirm_analysis")
            self.assertGreater(current["row_revision"], stale["_loaded_row_revision"])

    async def test_same_loaded_object_can_save_repeatedly(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            await migrate_db(file.name)
            user = await get_user(5002, file.name)
            initial = user["row_revision"]
            user["stage"] = "confirm_analysis"
            await save_user(user, file.name)
            user["stage"] = "working_map"
            await save_user(user, file.name)
            self.assertEqual(user["row_revision"], initial + 2)


if __name__ == "__main__":
    unittest.main()
