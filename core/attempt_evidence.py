"""One durable record per action; presentation is not proof of execution."""
from __future__ import annotations

import json

PREDICT_BUTTON = "🔮 Оценить трудность шага"
ACTUAL_BUTTON = "📊 Насколько трудно было"


def rating(value):
    return value if type(value) is int and 0 <= value <= 10 else None


def comparison(expected, actual):
    expected, actual = rating(expected), rating(actual)
    if expected is None or actual is None:
        return ""
    return f"Вы ожидали трудность {expected}/10, получилось {actual}/10."


def find_attempt(user, action_id):
    return next((item for item in reversed(user.get("skill_attempts") or [])
                 if str(item.get("action_id") or "") == str(action_id)), None)


async def ensure_schema(db):
    await db.execute("""CREATE TABLE IF NOT EXISTS attempt_evidence (
        user_id INTEGER NOT NULL, action_id TEXT NOT NULL, skill_id TEXT NOT NULL,
        day_id TEXT, calendar_date TEXT, source TEXT, shown_at TEXT,
        result TEXT NOT NULL DEFAULT 'started', completed INTEGER,
        partial INTEGER, helpfulness TEXT, continued INTEGER,
        expected_difficulty INTEGER CHECK(expected_difficulty BETWEEN 0 AND 10),
        actual_difficulty INTEGER CHECK(actual_difficulty BETWEEN 0 AND 10),
        reported_at TEXT, PRIMARY KEY(user_id, action_id))""")
    columns = {row[1] for row in await (await db.execute("PRAGMA table_info(attempt_evidence)")).fetchall()}
    for name, kind in (("independent", "INTEGER"), ("context_domain", "TEXT"),
                       ("mechanism", "TEXT"), ("target_function", "TEXT")):
        if name not in columns:
            await db.execute(f"ALTER TABLE attempt_evidence ADD COLUMN {name} {kind}")


async def persist(db, user):
    """Run in the same optimistic transaction as the user snapshot.

    Legacy records without an action id are not guessed or double-counted.
    No raw task, message or case text belongs in the analytics record.
    """
    await ensure_schema(db)
    entries = user.get("skill_attempts") or []
    if isinstance(entries, str):
        entries = json.loads(entries)
    for entry in entries:
        action_id = str(entry.get("action_id") or "")
        if not action_id:
            continue
        completed = entry.get("completed")
        partial = entry.get("partial")
        result = entry.get("result") or "started"
        # Old histories still distinguish completed, partial and not completed.
        if type(completed) is not bool and result in {"completed", "partial", "not_completed"}:
            completed = result == "completed"
        if type(partial) is not bool and result in {"completed", "partial", "not_completed"}:
            partial = result == "partial"
        continued = entry.get("continued_target_task")
        effect = entry.get("effect")
        await db.execute("""INSERT INTO attempt_evidence
            (user_id,action_id,skill_id,day_id,calendar_date,source,shown_at,result,completed,
             partial,helpfulness,continued,expected_difficulty,actual_difficulty,reported_at,
             independent,context_domain,mechanism,target_function)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id,action_id) DO UPDATE SET
            result=excluded.result, completed=excluded.completed, partial=excluded.partial,
            helpfulness=excluded.helpfulness, continued=excluded.continued,
            expected_difficulty=excluded.expected_difficulty,
            actual_difficulty=excluded.actual_difficulty, reported_at=excluded.reported_at,
            independent=excluded.independent,context_domain=excluded.context_domain,
            mechanism=excluded.mechanism,target_function=excluded.target_function""",
            (user["user_id"], action_id, str(entry.get("skill_id") or ""),
             entry.get("day_id"), entry.get("calendar_date"), entry.get("source"),
             entry.get("started_at"), result,
             int(completed) if type(completed) is bool else None,
             int(partial) if type(partial) is bool else None,
             effect if effect in {"helped", "some", "not_helped", "worse"} else None,
             int(continued) if type(continued) is bool else None,
             rating(entry.get("expected_difficulty")), rating(entry.get("actual_difficulty")),
             entry.get("completed_at"),
             int(entry["independent"]) if type(entry.get("independent")) is bool else None,
             entry.get("context_domain"), entry.get("mechanism"), entry.get("target_function")))


async def metrics(db, user_id, *, day_id=""):
    await ensure_schema(db)
    where = "user_id=?" + (" AND day_id=?" if day_id else "")
    params = (user_id, day_id) if day_id else (user_id,)
    row = await (await db.execute(f"""SELECT COUNT(*),
        COALESCE(SUM(completed=1),0), COALESCE(SUM(partial=1),0),
        COALESCE(SUM(helpfulness IN ('helped','some')),0), COALESCE(SUM(continued=1),0),
        COALESCE(SUM(completed IS NULL),0) FROM attempt_evidence WHERE {where}""", params)).fetchone()
    return dict(zip(("shown", "completed", "partial", "felt_better", "continued", "execution_unknown"), row))
