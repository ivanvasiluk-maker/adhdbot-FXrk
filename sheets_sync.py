# sheets_sync.py
# Privacy policy for Google Sheets analytics:
# DO NOT send full problem text, voice transcripts, crisis messages, personal stories, or medical details.
# Sheets is for behavior analytics only, not for storing confessions.
# Telegram identity and free text never leave SQLite through this module.
import os
import json
import re
import aiohttp
import asyncio
import logging
import hashlib
import hmac
from datetime import datetime
from typing import Any, Dict, List, Tuple

import aiosqlite
from dotenv import load_dotenv

load_dotenv(override=False)

SHEETS_WEBHOOK_URL = os.getenv("SHEETS_WEBHOOK_URL", "")
SHEETS_SYNC_ENABLED = os.getenv("SHEETS_SYNC_ENABLED", "false").lower() == "true"
SHEETS_SYNC_INTERVAL_SECONDS = int(os.getenv("SHEETS_SYNC_INTERVAL_SECONDS", "60"))
SHEETS_SYNC_BATCH_SIZE = int(os.getenv("SHEETS_SYNC_BATCH_SIZE", "50"))
ANALYTICS_ID_SALT = os.getenv("ANALYTICS_ID_SALT", "")

SENSITIVE_KEYS = {
    "problem_text",
    "voice_transcript",
    "crisis_text",
    "raw_text",
    "message_text",
    "user_text",
    "transcript",
    "text",
    "personal_story",
    "medical_details",
    "history",
    "full_text",
    "telegram_user_id",
    "telegram_username",
    "telegram_name",
    "name",
    "username",
}
ALLOWED_KEYS = {
    "problem_category",
    "pattern",
    "skill_id",
    "button_text",
    "result",
    "stage",
    "day",
    "trainer",
    "trainer_key",
    "from_trainer",
    "to_trainer",
    "switch_count",
    "trainer_switch_count",
    "trainer_current_mode",
    "trainer_previous_mode",
    "trainer_fit_signal",
    "trainer_modes_viewed",
    "trainer_modes_view_count",
    "payment_status",
    "bucket",
    "source",
    "keyboard",
    "button_count",
    "count",
    "return_count",
    "price_month",
    "amount",
    "reason",
    "choice",
    "day_number",
    "sid",
    "skill",
    "len",
    "is_test_user",
    "fast_forward_enabled",
    "db_ok",
    "payment_click",
    "error_type",
    "error_source",
    "main_pattern",
    "avoidance_reason",
    "avoidance_trigger",
    "avoidance_pattern",
    "attention_pattern",
    "emotional_trigger",
    "preferred_activation",
    "return_pattern",
    "downscale_pattern",
    "energy_pattern",
    "best_skill",
    "failed_skill",
    "worst_skill",
    "slip_pattern",
    "side_skill_interest",
    "done_count",
    "downscale_count",
    "recommended_track",
    "next_theme",
    "last_successful_skill",
    "needs_downscale",
    "needs_minimum_action",
    "next_skill_hint",
    "action_done_count",
    "action_failed_count",
    "downscale_count",
    "habit_id",
    "micro_habit_id",
    "last_effect_note",
    "effect_tags",
    "failed_reason_count",
    "failed_reason_count_today",
    "attention_escape_count",
    "shame_signal",
    "body_doubling_signal",
    "energy_signal",
    "best_variant",
    "daily_progress_shown_count",
    "daily_progress_shown_date",
    "effect_relief",
    "effect_confidence",
    "effect_anxiety_down",
    "effect_clarity",
    "crisis_tool_date",
    "crisis_tool_count_today",
    "last_crisis_tool_reason",
    "crisis_pattern",
    "crisis_skill",
    "crisis_effect",
    "last_crisis_pattern",
    "last_crisis_skill",
    "last_crisis_effect",
    "most_common_crisis_pattern",
    "most_effective_crisis_skill",
    "crisis_count",
    "crisis_success_rate",
    "crisis_effect_count",
    "crisis_effect_success_count",
    "crisis_pattern_counts",
    "crisis_skill_success_counts",
    "system_day_id",
    "last_system_day_id",
    "system_day_opened",
    "system_day_useful",
    "system_day_already",
    "system_day_signals",
}

log = logging.getLogger("sheets_sync")

SAFE_TAXONOMY_VALUE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def anonymous_analytics_id(user_id: Any, *, secret_salt: str = ANALYTICS_ID_SALT) -> str:
    if not secret_salt or user_id in {None, ""}:
        return ""
    return hmac.new(
        secret_salt.encode(), str(user_id).encode(), hashlib.sha256,
    ).hexdigest()[:20]


def sanitize_event_data(data: Any) -> Dict[str, Any]:
    if not data:
        return {}
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return {"raw": "[masked_string]"}
    if not isinstance(data, dict):
        return {"value": "[masked]"}

    clean: Dict[str, Any] = {}
    for k, v in data.items():
        key = str(k)
        if key in SENSITIVE_KEYS:
            clean[key] = "[masked]"
        elif key not in ALLOWED_KEYS:
            if isinstance(v, (int, float, bool)) or v is None:
                clean[key] = v
            else:
                clean[key] = "[masked]"
        elif isinstance(v, str):
            clean[key] = v if SAFE_TAXONOMY_VALUE.fullmatch(v) else "[masked]"
        elif isinstance(v, (dict, list)):
            clean[key] = "[structured]"
        else:
            clean[key] = v
    return clean


def event_to_sheet_row(event: Dict[str, Any], user: Dict[str, Any] | None = None) -> List[Any]:
    data = sanitize_event_data(event.get("event_data") or event.get("meta") or {})
    user = user or {}
    return [
        event.get("created_at") or event.get("ts") or datetime.utcnow().isoformat(),
        event.get("event_name") or event.get("event"),
        anonymous_analytics_id(event.get("user_id")),
        "",
        "",
        data.get("stage") or event.get("stage") or "",
        data.get("day") or user.get("day") or "",
        data.get("trainer_key") or user.get("trainer_key") or "",
        data.get("skill_id") or user.get("pending_skill_id") or "",
        data.get("pattern") or "",
        data.get("bucket") or user.get("bucket") or "",
        data.get("button_text") or "",
        data.get("result") or "",
        json.dumps(data, ensure_ascii=False),
    ]

ERROR_EVENTS = {
    "openai_error",
    "whisper_error",
    "sheets_sync_error",
    "payment_error",
    "telegram_send_error",
    "db_error",
}


def error_to_sheet_row(event: Dict[str, Any], user: Dict[str, Any] | None = None) -> List[Any]:
    data = sanitize_event_data(event.get("event_data") or event.get("meta") or {})
    user = user or {}
    return [
        event.get("created_at") or event.get("ts") or datetime.utcnow().isoformat(),
        event.get("event_name") or event.get("event"),
        anonymous_analytics_id(event.get("user_id")),
        "",
        "",
        data.get("stage") or event.get("stage") or "",
        data.get("error_type") or "",
        data.get("error_source") or data.get("source") or "",
        json.dumps(data, ensure_ascii=False),
    ]


PAYMENT_EVENTS = {
    "payment_click_20",
    "payment_click_month_1498",
    "payment_declined_soft",
    "payment_completed",
    "free_mode_started",
    "paid_mode_started",
    "payment_error",
    "payment_stub_shown",
}


def _event_name(event: Dict[str, Any]) -> str:
    return event.get("event_name") or event.get("event") or ""


def _parse_json(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _iso_from_timestamp(value: Any) -> str:
    try:
        ts = float(value or 0)
    except (TypeError, ValueError):
        ts = 0
    if ts <= 0:
        return ""
    return datetime.utcfromtimestamp(ts).isoformat()


def user_to_sheet_row(user: Dict[str, Any]) -> List[Any]:
    """Deprecated minimal snapshot: no identity, stories, conclusions, or profile strings."""
    return [
        _iso_from_timestamp(user.get("created_at")),
        _iso_from_timestamp(user.get("last_active")),
        anonymous_analytics_id(user.get("user_id")),
        user.get("day") or 0,
        user.get("is_test_user") or 0,
    ]


def payment_to_sheet_row(event: Dict[str, Any], user: Dict[str, Any] | None = None) -> List[Any]:
    """Format a payment-related event for the optional `payments` sheet."""
    data = sanitize_event_data(event.get("event_data") or event.get("meta") or {})
    user = user or {}
    name = _event_name(event)
    if name == "payment_click_20":
        offer_type, amount = "7_days", 20
    elif name == "payment_click_month_1498":
        offer_type, amount = "month", 14.98
    else:
        offer_type = data.get("payment_click") or data.get("source") or ""
        amount = data.get("amount") or ""
    return [
        event.get("created_at") or event.get("ts") or datetime.utcnow().isoformat(),
        anonymous_analytics_id(event.get("user_id")),
        "",
        name,
        offer_type,
        amount,
        user.get("payment_status") or data.get("payment_status") or "",
        json.dumps(data, ensure_ascii=False),
    ]


def daily_summary_to_sheet_row(date: str, users: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> List[Any]:
    """Build one aggregate row for the optional `daily_summary` sheet."""
    def event_created_on_day(event: Dict[str, Any]) -> bool:
        created_at = str(event.get("created_at") or "")
        return created_at.startswith(date)

    todays_events = [event for event in events if event_created_on_day(event)]
    names = [_event_name(event) for event in todays_events]

    def count(*event_names: str) -> int:
        allowed = set(event_names)
        return sum(1 for name in names if name in allowed)

    def user_created_today(user: Dict[str, Any]) -> bool:
        first_seen = _iso_from_timestamp(user.get("created_at"))
        return first_seen.startswith(date)

    return [
        date,
        len(users),
        sum(1 for user in users if user_created_today(user)),
        count("diagnosis_completed"),
        count("action_sent", "skill_card_shown"),
        count("action_done", "done", "return", "downscale_done"),
        count("action_failed", "not_done"),
        count("action_downscaled", "downscale_triggered"),
        sum(1 for user in users if int(user.get("day") or 0) >= 2),
        sum(1 for user in users if int(user.get("day") or 0) >= 3),
        count("offer_shown"),
        count("payment_click_20"),
        count("payment_click_month_1498"),
        count("payment_completed"),
        count("crisis_clicked", "crisis_open", "crisis_message"),
    ]


async def post_rows(rows: List[List[Any]], sheet: str = "events") -> Tuple[bool, str]:
    if not SHEETS_WEBHOOK_URL or not rows:
        return False, "No webhook url or no rows"
    payload = {"sheet": sheet, "rows": rows}
    async with aiohttp.ClientSession() as session:
        async with session.post(SHEETS_WEBHOOK_URL, json=payload, timeout=10) as resp:
            text = await resp.text()
            if resp.status >= 400:
                return False, f"HTTP {resp.status}: {text}"
            try:
                result = json.loads(text)
                if isinstance(result, dict) and not result.get("ok", True):
                    return False, text
            except Exception:
                pass
            return True, text


def behavioral_analytics_to_sheet_row(event: Dict[str, Any], *, secret_salt: str) -> List[Any]:
    """Serialize only pseudonymous ids, bounded taxonomy, counts, timestamps, and versions."""
    if not secret_salt:
        raise ValueError("ANALYTICS_ID_SALT is required for behavioral analytics export")
    anonymous_user = anonymous_analytics_id(event.get("user_id"), secret_salt=secret_salt)
    return [
        event.get("created_at") or "", event.get("event_name") or "", anonymous_user,
        event.get("situation_id") or "", event.get("experiment_id") or "",
        event.get("skill_id") or "", event.get("mechanism_code") or "",
        event.get("context_domain") or "", event.get("outcome_label") or "",
        int(event.get("count_value") or 0), event.get("policy_version") or "",
        event.get("ranking_version") or "", int(event.get("skill_version") or 0),
    ]


async def sync_behavioral_analytics_events(db_path: str, limit: int) -> Dict[str, Any]:
    """Export only the normalized PATCH-16 table; legacy events stay local."""
    if not ANALYTICS_ID_SALT:
        return {"synced": 0, "failed": 0, "error": "", "warning": "ANALYTICS_ID_SALT is empty"}
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT * FROM behavioral_analytics_events WHERE synced=0 ORDER BY id LIMIT ?",
            (limit,),
        )).fetchall()
        if not rows:
            return {"synced": 0, "failed": 0, "error": ""}
        events = [dict(row) for row in rows]
        ok, message = await post_rows(
            [behavioral_analytics_to_sheet_row(event, secret_salt=ANALYTICS_ID_SALT) for event in events],
            sheet="behavioral_kpi",
        )
        event_ids = [int(event["id"]) for event in events]
        placeholders = ",".join("?" for _ in event_ids)
        if ok:
            await db.execute(
                f"UPDATE behavioral_analytics_events SET synced=1,last_sync_error=NULL WHERE id IN ({placeholders})",
                event_ids,
            )
            await db.commit()
            return {"synced": len(event_ids), "failed": 0, "error": ""}
        await db.execute(
            f"UPDATE behavioral_analytics_events SET sync_attempts=sync_attempts+1,last_sync_error=? WHERE id IN ({placeholders})",
            [message[:500], *event_ids],
        )
        await db.commit()
        return {"synced": 0, "failed": len(event_ids), "error": message[:500]}


async def _fetch_unsynced_events(db: aiosqlite.Connection, limit: int) -> List[Dict[str, Any]]:
    db.row_factory = aiosqlite.Row
    cur = await db.execute(
        """
        SELECT * FROM events
        WHERE COALESCE(synced, 0) = 0
        ORDER BY id
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(row) for row in await cur.fetchall()]


async def _fetch_users(db: aiosqlite.Connection, user_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    if not user_ids:
        return {}
    placeholders = ",".join("?" for _ in user_ids)
    cur = await db.execute(f"SELECT * FROM users WHERE user_id IN ({placeholders})", user_ids)
    rows = await cur.fetchall()
    return {int(row["user_id"]): dict(row) for row in rows}


async def _fetch_all_users(db: aiosqlite.Connection) -> List[Dict[str, Any]]:
    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM users")
    return [dict(row) for row in await cur.fetchall()]


async def _fetch_all_events(db: aiosqlite.Connection) -> List[Dict[str, Any]]:
    db.row_factory = aiosqlite.Row
    cur = await db.execute("SELECT * FROM events")
    return [dict(row) for row in await cur.fetchall()]


async def _mark_events_synced(db: aiosqlite.Connection, event_ids: List[int]):
    if not event_ids:
        return
    placeholders = ",".join("?" for _ in event_ids)
    await db.execute(
        f"UPDATE events SET synced = 1, last_sync_error = NULL WHERE id IN ({placeholders})",
        event_ids,
    )


async def _mark_events_failed(db: aiosqlite.Connection, event_ids: List[int], error: str):
    if not event_ids:
        return
    placeholders = ",".join("?" for _ in event_ids)
    await db.execute(
        f"""
        UPDATE events
        SET synced = 0,
            sync_attempts = COALESCE(sync_attempts, 0) + 1,
            last_sync_error = ?
        WHERE id IN ({placeholders})
        """,
        [error[:500], *event_ids],
    )


async def sync_unsynced_events(db_path: str, limit: int = SHEETS_SYNC_BATCH_SIZE) -> Dict[str, Any]:
    # The old multi-tab exporter mixed identity/profile snapshots with events.
    # Keep it unreachable: production sync is normalized behavioral analytics only.
    return await sync_behavioral_analytics_events(db_path, limit)


async def _sync_legacy_events_disabled(db_path: str, limit: int = SHEETS_SYNC_BATCH_SIZE) -> Dict[str, Any]:
    """Retained only as migration reference; never called by the scheduler."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        events = await _fetch_unsynced_events(db, limit)
        if not events:
            return {"synced": 0, "failed": 0, "error": ""}

        user_ids = sorted({int(e["user_id"]) for e in events if e.get("user_id") is not None})
        users = await _fetch_users(db, user_ids)
        all_users = await _fetch_all_users(db)
        all_events = await _fetch_all_events(db)
        event_ids = [int(e["id"]) for e in events]
        normal_events = [e for e in events if _event_name(e) not in ERROR_EVENTS]
        error_events = [e for e in events if _event_name(e) in ERROR_EVENTS]
        payment_events = [e for e in events if _event_name(e) in PAYMENT_EVENTS]
        synced_ids: List[int] = []
        failures: List[str] = []
        supplemental_warnings: List[str] = []

        if normal_events:
            rows = [event_to_sheet_row(e, users.get(int(e["user_id"] or 0), {})) for e in normal_events]
            ok, msg = await post_rows(rows, sheet="events")
            if ok:
                synced_ids.extend(int(e["id"]) for e in normal_events)
            else:
                failures.append(msg)
                await _mark_events_failed(db, [int(e["id"]) for e in normal_events], msg)

        if error_events:
            rows = [error_to_sheet_row(e, users.get(int(e["user_id"] or 0), {})) for e in error_events]
            ok, msg = await post_rows(rows, sheet="errors")
            if ok:
                synced_ids.extend(int(e["id"]) for e in error_events)
            else:
                failures.append(msg)
                await _mark_events_failed(db, [int(e["id"]) for e in error_events], msg)

        # Optional analytics tabs. They are best-effort and do not block the core event sync.
        if payment_events:
            rows = [payment_to_sheet_row(e, users.get(int(e["user_id"] or 0), {})) for e in payment_events]
            ok, msg = await post_rows(rows, sheet="payments")
            if not ok:
                supplemental_warnings.append(f"payments: {msg}")

        if users:
            rows = [user_to_sheet_row(user) for user in users.values()]
            ok, msg = await post_rows(rows, sheet="users")
            if not ok:
                supplemental_warnings.append(f"users: {msg}")

        today = datetime.utcnow().date().isoformat()
        ok, msg = await post_rows([daily_summary_to_sheet_row(today, all_users, all_events)], sheet="daily_summary")
        if not ok:
            supplemental_warnings.append(f"daily_summary: {msg}")

        # PATCH-16 analytics is an optional, privacy-minimal sheet. It never
        # shares Telegram identity or free text and requires a private hash salt.
        analytics_rows = await (await db.execute(
            """SELECT * FROM behavioral_analytics_events WHERE synced=0 ORDER BY id LIMIT ?""",
            (limit,),
        )).fetchall()
        if analytics_rows and ANALYTICS_ID_SALT:
            analytics_dicts = [dict(row) for row in analytics_rows]
            ok, msg = await post_rows(
                [behavioral_analytics_to_sheet_row(row, secret_salt=ANALYTICS_ID_SALT) for row in analytics_dicts],
                sheet="behavioral_kpi",
            )
            analytics_ids = [int(row["id"]) for row in analytics_dicts]
            placeholders = ",".join("?" for _ in analytics_ids)
            if ok:
                await db.execute(
                    f"UPDATE behavioral_analytics_events SET synced=1,last_sync_error=NULL WHERE id IN ({placeholders})",
                    analytics_ids,
                )
            else:
                await db.execute(
                    f"""UPDATE behavioral_analytics_events SET sync_attempts=sync_attempts+1,last_sync_error=?
                        WHERE id IN ({placeholders})""", [msg[:500], *analytics_ids],
                )
                supplemental_warnings.append(f"behavioral_kpi: {msg}")
        elif analytics_rows:
            supplemental_warnings.append("behavioral_kpi: ANALYTICS_ID_SALT is empty")

        if synced_ids:
            await _mark_events_synced(db, synced_ids)
        await db.commit()

        failed = len(event_ids) - len(synced_ids)
        if failures:
            msg = "; ".join(failures)[:500]
            log.error("Sheets sync failed: %s", msg)
            return {"synced": len(synced_ids), "failed": failed, "error": msg}
        if supplemental_warnings:
            warning = "; ".join(supplemental_warnings)[:500]
            log.warning("Sheets optional tab sync skipped: %s", warning)
            return {"synced": len(synced_ids), "failed": 0, "error": "", "warning": warning}
        return {"synced": len(synced_ids), "failed": 0, "error": ""}


async def _record_sheets_sync_error(db_path: str, error: Exception):
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """
                INSERT INTO events(user_id, event_name, event_data, stage, created_at, synced, sync_attempts, last_sync_error, ts, event, meta)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    0,
                    "sheets_sync_error",
                    json.dumps({"error_type": type(error).__name__, "error_source": "sheets_sync_loop"}, ensure_ascii=False),
                    "background",
                    datetime.utcnow().isoformat(),
                    0,
                    0,
                    None,
                    datetime.utcnow().timestamp(),
                    "sheets_sync_error",
                    json.dumps({"error_type": type(error).__name__, "error_source": "sheets_sync_loop"}, ensure_ascii=False),
                ),
            )
            await db.commit()
    except Exception:
        log.exception("Could not record sheets_sync_error")


async def sheets_sync_loop(db_path: str):
    if not SHEETS_SYNC_ENABLED:
        logging.info("Sheets sync disabled")
        return
    if not SHEETS_WEBHOOK_URL:
        logging.info("Sheets sync disabled: SHEETS_WEBHOOK_URL is empty")
        return
    while True:
        try:
            await sync_unsynced_events(db_path, SHEETS_SYNC_BATCH_SIZE)
        except Exception as e:
            logging.exception("Sheets sync failed: %s", e)
            await _record_sheets_sync_error(db_path, e)
        await asyncio.sleep(SHEETS_SYNC_INTERVAL_SECONDS)
