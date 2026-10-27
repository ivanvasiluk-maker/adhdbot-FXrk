import asyncio
import io
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite
import bot
from core import ai_boundary as ai


class AIBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = self.temp.name + "/usage.db"

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_atomic_daily_limit_under_concurrency(self):
        async def reserve():
            with ai.ai_scope(31, self.path):
                try:
                    await ai.reserve_usage(input_units=10)
                    return True
                except ai.AIBoundaryError:
                    return False
        results = await asyncio.gather(*(reserve() for _ in range(35)))
        self.assertEqual(sum(results), 30)
        async with aiosqlite.connect(self.path) as db:
            self.assertEqual((await (await db.execute("SELECT calls FROM ai_usage WHERE user_id=0")).fetchone())[0], 30)

    async def test_global_guard_survives_profile_erasure(self):
        with ai.ai_scope(31, self.path):
            await ai.reserve_usage()
        async with aiosqlite.connect(self.path) as db:
            await db.execute("DELETE FROM ai_usage WHERE user_id=31")
            await db.commit()
        with ai.ai_scope(32, self.path), patch.object(ai, "GLOBAL_CALLS", 1):
            with self.assertRaises(ai.AIBoundaryError):
                await ai.reserve_usage()

    async def test_chat_is_nonblocking_and_has_bounded_output(self):
        release = threading.Event()
        started = threading.Event()
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            started.set()
            release.wait(2)
            return "response"
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with ai.ai_scope(31, self.path):
            task = asyncio.create_task(ai.chat_completion(client, model="test", messages=[{"content":"text"}], max_tokens=9999))
            try:
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(.01)
                self.assertTrue(started.is_set())
                self.assertFalse(task.done())
            finally:
                release.set()
            self.assertEqual(await task, "response")
        self.assertEqual(calls[0]["max_tokens"], 800)
        self.assertEqual(calls[0]["timeout"], 12)

    async def test_timeout_keeps_slot_until_sdk_thread_finishes(self):
        release = threading.Event()
        with patch.object(ai, "CALLER_TIMEOUT_SECONDS", .02):
            tasks = [asyncio.create_task(ai._run(lambda: release.wait(2))) for _ in range(2)]
            try:
                await asyncio.sleep(.04)
                with self.assertRaises(ai.AIBoundaryError):
                    await ai._run(lambda: None)
                results = await asyncio.gather(*tasks, return_exceptions=True)
                self.assertTrue(all(isinstance(r, asyncio.TimeoutError) for r in results))
            finally:
                release.set()
                await asyncio.sleep(.05)
        self.assertEqual(await ai._run(lambda: "free"), "free")

    async def test_missing_identity_and_oversize_never_call_provider(self):
        create = AsyncMock()
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with self.assertRaises(ai.AIBoundaryError):
            await ai.chat_completion(client, messages=[{"content":"x"}])
        with ai.ai_scope(31, self.path):
            with self.assertRaises(ai.AIBoundaryError):
                await ai.chat_completion(client, messages=[{"content":"x"*24001}])
        create.assert_not_called()

    async def test_large_voice_rejected_before_telegram_download(self):
        download = AsyncMock()
        m = SimpleNamespace(voice=SimpleNamespace(duration=121,file_size=10),bot=SimpleNamespace(get_file=download),answer=AsyncMock())
        with patch.object(bot, "client", object()):
            self.assertIsNone(await bot.whisper_transcribe(m))
        download.assert_not_awaited()
        m.answer.assert_awaited_once()

    async def test_voice_quota_and_metadata_are_validated(self):
        with ai.ai_scope(31, self.path):
            with self.assertRaises(ai.AIBoundaryError):
                await ai.transcribe(object(), duration=121, file=io.BytesIO())
            with patch.object(ai, "GLOBAL_CALLS", 0):
                with self.assertRaises(ai.AIBoundaryError):
                    await ai.transcribe(object(), duration=10, file=io.BytesIO())

    async def test_reset_deletes_personal_usage_but_keeps_global_and_other_user(self):
        from db import init_db, migrate_db, save_user, default_user
        await init_db(self.path)
        await migrate_db(self.path)
        for uid in (31,32):
            await save_user(default_user(uid), self.path)
            with ai.ai_scope(uid, self.path):
                await ai.reserve_usage()
        with patch.object(bot, "DB_PATH", self.path):
            await bot.reset_current_user(31,31)
        async with aiosqlite.connect(self.path) as db:
            rows = dict(await (await db.execute("SELECT user_id,calls FROM ai_usage")).fetchall())
        self.assertEqual(rows, {0:2,32:1})

    def test_unknown_file_length_is_still_bounded_while_downloading(self):
        with patch.object(ai, "MAX_VOICE_BYTES", 5):
            buffer = ai.VoiceBuffer()
            buffer.write(b"1234")
            with self.assertRaises(ai.AIBoundaryError):
                buffer.write(b"56")
            self.assertEqual(buffer.getvalue(), b"1234")

    async def test_middleware_scopes_each_update_and_clears_identity(self):
        async def handler(event, data):
            await ai.reserve_usage()
            return "ok"
        middleware = ai.AIUsageMiddleware(lambda:self.path)
        self.assertEqual(await middleware(handler, SimpleNamespace(from_user=SimpleNamespace(id=31)), {}), "ok")
        with self.assertRaises(ai.AIBoundaryError):
            await ai.reserve_usage()
