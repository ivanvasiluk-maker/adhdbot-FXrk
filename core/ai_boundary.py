"""Bounded AI transport: no prompt text or secrets in durable usage records."""
import asyncio
import contextvars
import datetime as dt
import weakref
import io
from contextlib import contextmanager

import aiosqlite

_scope = contextvars.ContextVar("ai_scope", default=None)
_slots = weakref.WeakKeyDictionary()
MAX_PROMPT_BYTES = 24000
MAX_OUTPUT_TOKENS = 800
MAX_VOICE_SECONDS = 120
MAX_VOICE_BYTES = 5 * 1024 * 1024
USER_CALLS = 30
GLOBAL_CALLS = 300
GLOBAL_INPUT_UNITS = 3_000_000
CALLER_TIMEOUT_SECONDS = 15


class AIBoundaryError(RuntimeError):
    pass


class VoiceBuffer(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > MAX_VOICE_BYTES:
            raise AIBoundaryError("ai_voice_too_large")
        return super().write(data)


@contextmanager
def ai_scope(user_id, db_path):
    token = _scope.set((int(user_id), str(db_path)))
    try:
        yield
    finally:
        _scope.reset(token)


class AIUsageMiddleware:
    def __init__(self, path):
        self.path = path

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)
        with ai_scope(user.id, self.path()):
            return await handler(event, data)


async def reserve_usage(*, input_units=0, voice_seconds=0):
    scope = _scope.get()
    if scope is None:
        raise AIBoundaryError("ai_identity_missing")
    user_id, path = scope
    if user_id <= 0:
        raise AIBoundaryError("ai_identity_missing")
    today = dt.datetime.now(dt.timezone.utc).date()
    day = today.isoformat()
    async with aiosqlite.connect(path, timeout=3) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS ai_usage (day TEXT NOT NULL, user_id INTEGER NOT NULL, calls INTEGER NOT NULL, input_units INTEGER NOT NULL, voice_seconds INTEGER NOT NULL, PRIMARY KEY(day,user_id))")
        await db.execute("BEGIN IMMEDIATE")
        await db.execute("DELETE FROM ai_usage WHERE day < ?", ((today-dt.timedelta(days=7)).isoformat(),))
        own = await (await db.execute("SELECT calls,voice_seconds FROM ai_usage WHERE day=? AND user_id=?", (day,user_id))).fetchone() or (0,0)
        total = await (await db.execute("SELECT calls,input_units,voice_seconds FROM ai_usage WHERE day=? AND user_id=0", (day,))).fetchone() or (0,0,0)
        if own[0] >= USER_CALLS or total[0] >= GLOBAL_CALLS or total[1]+input_units > GLOBAL_INPUT_UNITS or own[1]+voice_seconds > 600 or total[2]+voice_seconds > 3600:
            raise AIBoundaryError("ai_daily_limit")
        await db.execute("INSERT INTO ai_usage VALUES (?,?,1,?,?) ON CONFLICT(day,user_id) DO UPDATE SET calls=calls+1,input_units=input_units+excluded.input_units,voice_seconds=voice_seconds+excluded.voice_seconds", (day,user_id,input_units,voice_seconds))
        # A non-personal aggregate survives profile deletion: /reset_me must not
        # reset the global spending guard.
        await db.execute("INSERT INTO ai_usage VALUES (?,0,1,?,?) ON CONFLICT(day,user_id) DO UPDATE SET calls=calls+1,input_units=input_units+excluded.input_units,voice_seconds=voice_seconds+excluded.voice_seconds", (day,input_units,voice_seconds))
        await db.commit()


async def _run(call):
    loop = asyncio.get_running_loop()
    slots = _slots.setdefault(loop, asyncio.Semaphore(2))
    # Refuse excess work, rather than create an unbounded queue of threads.
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.01)
    except asyncio.TimeoutError as error:
        raise AIBoundaryError("ai_busy") from error

    async def worker():
        try:
            return await asyncio.to_thread(call)
        finally:
            slots.release()

    task = asyncio.create_task(worker())
    # Timed-out callers must not release a slot while its SDK thread is alive.
    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    return await asyncio.wait_for(asyncio.shield(task), timeout=CALLER_TIMEOUT_SECONDS)


async def chat_completion(client, **kwargs):
    messages = kwargs.get("messages") or []
    size = sum(len(str(m.get("content", "")).encode("utf-8")) for m in messages)
    if size > MAX_PROMPT_BYTES:
        raise AIBoundaryError("ai_input_too_large")
    kwargs["max_tokens"] = min(int(kwargs.get("max_tokens") or MAX_OUTPUT_TOKENS), MAX_OUTPUT_TOKENS)
    kwargs["timeout"] = 12
    await reserve_usage(input_units=size)
    return await _run(lambda: client.chat.completions.create(**kwargs))


async def transcribe(client, *, duration, **kwargs):
    if not isinstance(duration, (int,float)) or not 0 < duration <= MAX_VOICE_SECONDS:
        raise AIBoundaryError("ai_voice_duration")
    await reserve_usage(voice_seconds=int(duration)+1)
    kwargs["timeout"] = 12
    return await _run(lambda: client.audio.transcriptions.create(**kwargs))
