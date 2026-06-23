import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import aiosqlite

from db import USER_STATE_SCHEMA_VERSION, get_user, init_db, migrate_db, save_user


async def main():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(db_path)
    try:
        await init_db(db_path)
        await migrate_db(db_path)

        user = await get_user(777001, db_path)
        assert user["telegram_id"] == 777001
        assert user["day_number"] == 1
        assert user["current_step"] == "start"
        assert user["access_status"] == user["payment_status"]
        assert user["trainer"] == user["trainer_key"]
        assert user["mode"] == user["input_mode"]
        assert user["schema_version"] == USER_STATE_SCHEMA_VERSION

        user.update({
            "day": 5,
            "stage": "training",
            "payment_status": "free_mode",
            "trainer_key": "beck",
            "input_mode": "voice",
        })
        await save_user(user, db_path)

        restored = await get_user(777001, db_path)
        assert restored["day"] == 5
        assert restored["day_number"] == 5
        assert restored["stage"] == "training"
        assert restored["current_step"] == "training"
        assert restored["payment_status"] == "free_mode"
        assert restored["access_status"] == "free_mode"
        assert restored["trainer"] == "beck"
        assert restored["mode"] == "voice"
        assert restored["updated_at"]

        async with aiosqlite.connect(db_path) as db:
            cols = [row[1] for row in await (await db.execute("PRAGMA table_info(users)")).fetchall()]
            for col in [
                "telegram_id",
                "day_number",
                "current_step",
                "access_status",
                "trainer",
                "mode",
                "created_at",
                "updated_at",
                "schema_version",
            ]:
                assert col in cols, col
            migration = await (
                await db.execute(
                    "SELECT version FROM schema_migrations WHERE version=?",
                    (USER_STATE_SCHEMA_VERSION,),
                )
            ).fetchone()
            assert migration is not None

        print("[SMOKE] persistent user state OK")
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


if __name__ == "__main__":
    asyncio.run(main())
