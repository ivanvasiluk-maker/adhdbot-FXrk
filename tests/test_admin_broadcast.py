import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import broadcast_admin


def create_users_table(path: str) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                telegram_id INTEGER,
                chat_id INTEGER,
                notifications_enabled INTEGER,
                notification_consent INTEGER
            )
            """
        )
        db.commit()


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))


class AdminBroadcastTests(unittest.IsolatedAsyncioTestCase):
    def test_manual_broadcast_parser_deduplicates_ids(self):
        recipients, message = broadcast_admin.parse_manual_broadcast(
            "/broadcast_ids 1234567890, 987654321,1234567890 | Бот снова работает"
        )
        self.assertEqual(
            recipients,
            [
                {"user_id": 1234567890, "chat_id": 1234567890},
                {"user_id": 987654321, "chat_id": 987654321},
            ],
        )
        self.assertEqual(message, "Бот снова работает")

    def test_manual_broadcast_parser_rejects_invalid_ids(self):
        with self.assertRaisesRegex(ValueError, "положительными числами"):
            broadcast_admin.parse_manual_broadcast("/broadcast_ids 123,abc | Привет")

    def test_database_inspection_reports_only_count_and_file_metadata(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            create_users_table(file.name)
            with sqlite3.connect(file.name) as db:
                db.executemany(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                    [(1, 1, 1, 1, 1), (2, 2, 2, 1, 1)],
                )
                db.commit()
            result = broadcast_admin.inspect_database(Path(file.name))
            self.assertTrue(result["exists"])
            self.assertEqual(result["users"], 2)
            self.assertNotIn("telegram_id", result)
            self.assertNotIn("chat_id", result)

    async def test_audience_excludes_opt_outs_and_deduplicates_chats(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            create_users_table(file.name)
            with sqlite3.connect(file.name) as db:
                db.executemany(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                    [
                        (1, 101, 1001, 1, 1),
                        (2, 102, 1002, 0, 1),
                        (3, 103, 1003, 1, 0),
                        (4, 104, 1001, 1, 1),
                        (5, 105, None, 1, 1),
                    ],
                )
                db.commit()

            recipients, counts = await broadcast_admin.broadcast_audience(file.name)

            self.assertEqual(recipients, [
                {"user_id": 1, "chat_id": 1001},
                {"user_id": 5, "chat_id": 105},
            ])
            self.assertEqual(counts["eligible"], 2)
            self.assertEqual(counts["opted_out"], 2)
            self.assertEqual(counts["duplicate_chat"], 1)

    async def test_confirmed_broadcast_persists_delivery_journal(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            create_users_table(file.name)
            with sqlite3.connect(file.name) as db:
                db.executemany(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                    [(1, 1, 1001, 1, 1), (2, 2, 1002, 1, 1)],
                )
                db.commit()
            fake_bot = FakeBot()

            with patch.object(broadcast_admin.app, "DB_PATH", file.name), patch.object(
                broadcast_admin, "BROADCAST_SEND_INTERVAL_SECONDS", 0,
            ):
                await broadcast_admin.run_broadcast(fake_bot, 999, "run-1", "Бот снова работает")

            with sqlite3.connect(file.name) as db:
                run = db.execute(
                    "SELECT status,total,sent,blocked,failed FROM broadcast_runs WHERE run_id='run-1'"
                ).fetchone()
                deliveries = db.execute(
                    "SELECT user_id,status FROM broadcast_deliveries ORDER BY user_id"
                ).fetchall()
            self.assertEqual(run, ("completed", 2, 2, 0, 0))
            self.assertEqual(deliveries, [(1, "sent"), (2, "sent")])
            self.assertEqual([message[0] for message in fake_bot.messages], [1001, 1002, 999])
            self.assertNotIn(999, broadcast_admin.BROADCAST_ACTIVE_ADMINS)

    async def test_override_audience_does_not_require_users_rows(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            create_users_table(file.name)
            fake_bot = FakeBot()
            recipients = [{"user_id": 123456789, "chat_id": 123456789}]

            with patch.object(broadcast_admin.app, "DB_PATH", file.name), patch.object(
                broadcast_admin, "BROADCAST_SEND_INTERVAL_SECONDS", 0,
            ):
                await broadcast_admin.run_broadcast(
                    fake_bot, 999, "run-recovery", "Снова работает",
                    recipients_override=recipients,
                )

            self.assertEqual([message[0] for message in fake_bot.messages], [123456789, 999])
            with sqlite3.connect(file.name) as db:
                run = db.execute(
                    "SELECT total,sent,blocked,failed FROM broadcast_runs WHERE run_id='run-recovery'"
                ).fetchone()
            self.assertEqual(run, (1, 1, 0, 0))

    def test_restart_button_uses_dedicated_callback(self):
        markup = broadcast_admin.restart_keyboard()
        self.assertEqual(markup.inline_keyboard[0][0].callback_data, "broadcast:restart")


if __name__ == "__main__":
    unittest.main()
