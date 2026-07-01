# ============================================================
# DB.PY — Все функции работы с БД
# ============================================================

import json
import time
import logging
import os
import re
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import aiosqlite

from sheets_sync import sanitize_event_data

# Logging
log = logging.getLogger("bot")

# Global test switch to unlock features without paywalls
TEST_MODE = os.getenv("TEST_MODE", "").lower() in {"1", "true", "yes", "on", "debug"}

# Persistent user-state schema version. Migrations must be additive/non-destructive:
# deploys must never drop the SQLite file or reset existing users.
USER_STATE_SCHEMA_VERSION = 2


# ============================================================
# USER PROFILE: dynamic digital model
# ============================================================

USER_PROFILE_SCHEMA_VERSION = 1

USER_PROFILE_LIST_FIELDS = {
    "user_model_events",
    "strengths",
    "barriers",
    "resources",
    "failure_patterns",
    "working_strategies",
    "successful_skills",
    "failed_skills",
    "confirmed_signals",
    "secondary_hypotheses",
    "system_day_opened",
    "system_day_useful",
    "system_day_already",
}

USER_PROFILE_DICT_FIELDS = {
    "attention_profile",
    "motivation_profile",
    "emotional_profile",
    "development_stats",
    "development_avatar",
    "development_map",
    "development_history",
}

USER_PROFILE_CORE_FIELDS = USER_PROFILE_LIST_FIELDS | USER_PROFILE_DICT_FIELDS | {
    "preferred_trainer",
    "avatar_version",
    "profile_prompt",
}

DEVELOPMENT_AVATAR_VERSION = 1
DEVELOPMENT_HISTORY_VERSION = 1
DEVELOPMENT_AVATAR_BASE_VALUE = 20
DEVELOPMENT_AVATAR_DIMENSIONS = {
    "task_initiation": {"emoji": "🧠", "label": "Запуск задач"},
    "attention_holding": {"emoji": "🎯", "label": "Удержание внимания"},
    "slip_recovery": {"emoji": "🔄", "label": "Возврат после срыва"},
    "self_regulation": {"emoji": "⚖", "label": "Саморегуляция"},
    "resilience": {"emoji": "🔥", "label": "Устойчивость"},
    "consistency": {"emoji": "📈", "label": "Последовательность действий"},
    "social_activity": {"emoji": "🤝", "label": "Социальная активность"},
    "professional_activity": {"emoji": "💼", "label": "Профессиональная активность"},
}

SAFE_TONE_ALLOWED_MARKERS = (
    "похоже",
    "сейчас видно",
    "пока предполагаем",
    "мы проверим",
    "данных пока мало",
    "эта модель будет уточняться",
)

UNSAFE_CERTAINTY_MARKERS = (
    "у тебя " + "точно",
    "ты такой " + "человек",
    "нав" + "сегда",
    "100" + "%",
    "точный " + "показатель",
)

SLIP_AS_INFORMATION_PRINCIPLE = "Срыв = информация. Не наказание."

DEVELOPMENT_FOCUS_LABELS = {
    "task_initiation": "запуск задач",
    "attention_holding": "удержание внимания",
    "self_regulation": "саморегуляция",
    "self_criticism": "самокритика",
    "slip_recovery": "возврат после срыва",
    "social_activity": "социальная активность",
    "professional_activity": "профессиональная активность",
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _as_list(value: Any) -> List[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _merge_unique_list(current: Any, incoming: Any, *, limit: int = 50) -> List[Any]:
    merged: List[Any] = []
    seen = set()
    for item in [*_as_list(current), *_as_list(incoming)]:
        if item in (None, ""):
            continue
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[-limit:]


def _avatar_metric(value: int = DEVELOPMENT_AVATAR_BASE_VALUE, *, updated_at: str = "") -> Dict[str, Any]:
    return {
        "value": max(0, min(100, int(value))),
        "trend": "data_needed",
        "samples": 0,
        "last_delta": 0,
        "updated_at": updated_at,
    }


def default_development_avatar(now: Optional[str] = None) -> Dict[str, Any]:
    """Create the adult development avatar model (not a game character)."""
    now = now or _utc_iso()
    return {
        "version": DEVELOPMENT_AVATAR_VERSION,
        "principle": "self_development_reflection",
        "precision_note": "ranges_and_trends_not_diagnosis",
        "metrics": {
            key: _avatar_metric(updated_at=now)
            for key in DEVELOPMENT_AVATAR_DIMENSIONS
        },
        "events_count": 0,
        "slips_recorded": 0,
        "last_event": "",
        "updated_at": now,
    }


def normalize_development_avatar(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else {}
        except Exception:
            raw = {}
    avatar = raw.copy() if isinstance(raw, dict) else {}
    normalized = default_development_avatar()
    normalized.update({k: v for k, v in avatar.items() if k != "metrics"})
    raw_metrics = avatar.get("metrics") if isinstance(avatar.get("metrics"), dict) else {}
    metrics = {}
    for key in DEVELOPMENT_AVATAR_DIMENSIONS:
        current = raw_metrics.get(key) if isinstance(raw_metrics.get(key), dict) else {}
        metric = _avatar_metric(current.get("value", DEVELOPMENT_AVATAR_BASE_VALUE))
        metric.update({k: current.get(k, metric[k]) for k in metric})
        metric["value"] = max(0, min(100, int(metric.get("value") or DEVELOPMENT_AVATAR_BASE_VALUE)))
        metric["samples"] = max(0, int(metric.get("samples") or 0))
        metrics[key] = metric
    normalized["metrics"] = metrics
    normalized["version"] = DEVELOPMENT_AVATAR_VERSION
    normalized["precision_note"] = "ranges_and_trends_not_diagnosis"
    return normalized


def _avatar_trend(samples: int, delta: int) -> str:
    if samples < 2:
        return "data_needed"
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "stable"


def _avatar_apply_delta(avatar: Dict[str, Any], metric_key: str, delta: int, now: str) -> None:
    metric = avatar["metrics"].get(metric_key)
    if not metric:
        return
    old_value = int(metric.get("value") or DEVELOPMENT_AVATAR_BASE_VALUE)
    new_value = max(0, min(100, old_value + int(delta)))
    samples = int(metric.get("samples") or 0) + 1
    metric.update({
        "value": new_value,
        "trend": _avatar_trend(samples, new_value - old_value),
        "samples": samples,
        "last_delta": new_value - old_value,
        "updated_at": now,
    })


def development_avatar_event_patch(profile: Dict[str, Any], event_type: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a mergeable patch that updates the development avatar from user actions.

    Slips/failures are recorded as information, never as penalties.
    """
    context = context or {}
    now = _utc_iso()
    avatar = normalize_development_avatar((profile or {}).get("development_avatar"))
    deltas = {
        "skill_done": {"task_initiation": 4, "consistency": 2},
        "return_after_slip": {"slip_recovery": 5, "resilience": 3, "consistency": 1},
        "downscale": {"self_regulation": 4, "resilience": 1},
        "five_day_streak": {"consistency": 6},
        "body_doubling": {"social_activity": 4, "task_initiation": 1},
        "professional_action": {"professional_activity": 4, "task_initiation": 1},
        "attention_action": {"attention_holding": 3, "self_regulation": 1},
    }
    event_deltas = dict(deltas.get(event_type, {}))
    skill_id = str(context.get("skill_id") or "")
    if event_type == "skill_done":
        if skill_id == "body_doubling_plan":
            event_deltas.update(deltas["body_doubling"])
        if skill_id in {"one_tab_focus", "phone_far_3min", "visible_next_step", "urge_surf_60"}:
            event_deltas.update(deltas["attention_action"])
        target_text = str(context.get("target") or "").lower()
        if context.get("professional") or any(x in target_text for x in ("работ", "проект", "код", "созвон", "учеб", "документ")):
            event_deltas.update(deltas["professional_action"])
        if int(context.get("streak") or 0) == 5:
            event_deltas["consistency"] = event_deltas.get("consistency", 0) + deltas["five_day_streak"]["consistency"]

    for metric_key, delta in event_deltas.items():
        _avatar_apply_delta(avatar, metric_key, delta, now)

    avatar["events_count"] = int(avatar.get("events_count") or 0) + 1
    avatar["last_event"] = event_type
    avatar["updated_at"] = now
    if event_type in {"slip_recorded", "action_failed"}:
        avatar["slips_recorded"] = int(avatar.get("slips_recorded") or 0) + 1
        avatar["last_slip_at"] = now

    stats = dict((profile or {}).get("development_stats") or {})
    stats["avatar_events_count"] = avatar["events_count"]
    stats["avatar_last_event"] = event_type
    if event_type in {"slip_recorded", "action_failed"}:
        stats["slips_recorded_as_information"] = int(stats.get("slips_recorded_as_information") or 0) + 1

    return {
        "development_avatar": avatar,
        "development_stats": stats,
        "avatar_version": DEVELOPMENT_AVATAR_VERSION,
    }


async def record_development_avatar_event(
    user_id: int,
    event_type: str,
    db_path: str = "bot.db",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profile = await get_user_profile(user_id, db_path)
    patch = development_avatar_event_patch(profile, event_type, context)
    return await update_user_profile(user_id, patch, db_path, source=f"avatar_{event_type}")


def _avatar_range_label(value: int) -> str:
    if value < 25:
        return "низкий диапазон"
    if value < 50:
        return "формируется"
    if value < 75:
        return "укрепляется"
    return "сильная зона"


def _avatar_trend_label(trend: str) -> str:
    return {
        "up": "↗ растёт",
        "stable": "→ стабильно",
        "down": "↘ снизилось",
        "data_needed": "данных пока мало",
    }.get(trend or "", "данных пока мало")


def render_development_avatar(profile: Dict[str, Any], *, limit: int = 8) -> str:
    avatar = normalize_development_avatar((profile or {}).get("development_avatar"))
    lines = [
        "🧩 Аватар развития",
        "Это не оценка личности и не медицинское заключение — только диапазоны и тренды по действиям.",
    ]
    for key, meta in list(DEVELOPMENT_AVATAR_DIMENSIONS.items())[:limit]:
        metric = avatar["metrics"].get(key, {})
        value = int(metric.get("value") or DEVELOPMENT_AVATAR_BASE_VALUE)
        lines.append(
            f"{meta['emoji']} {meta['label']}: {_avatar_range_label(value)} ({_avatar_trend_label(str(metric.get('trend') or 'data_needed'))})"
        )
    if int(avatar.get("slips_recorded") or 0):
        lines.append("Срывы здесь учитываются как данные, не как штраф.")
    return "\n".join(lines)


DEVELOPMENT_MAP_VERSION = 1


def default_development_map(now: Optional[str] = None) -> Dict[str, Any]:
    now = now or _utc_iso()
    return {
        "version": DEVELOPMENT_MAP_VERSION,
        "status": "learning",
        "hypotheses": [],
        "checks": [
            {"label": "помогает ли уменьшение шага", "status": "testing", "evidence_count": 0, "contradiction_count": 0},
            {"label": "помогает ли плохой черновик", "status": "testing", "evidence_count": 0, "contradiction_count": 0},
            {"label": "помогает ли присутствие других людей", "status": "testing", "evidence_count": 0, "contradiction_count": 0},
        ],
        "helps": [],
        "blocks": [],
        "slip_points": [],
        "return_points": [],
        "successful_strategies": [],
        "ineffective_strategies": [],
        "behavior_events_count": 0,
        "last_update_source": "",
        "updated_at": now,
    }


def _normalize_map_items(items: Any) -> List[Dict[str, Any]]:
    normalized = []
    for item in _as_list(items):
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("name") or "").strip()
            if not label:
                continue
            normalized.append({
                "label": label,
                "status": str(item.get("status") or "testing"),
                "evidence_count": max(0, int(item.get("evidence_count") or 0)),
                "contradiction_count": max(0, int(item.get("contradiction_count") or 0)),
                "last_source": item.get("last_source") or "",
                "updated_at": item.get("updated_at") or "",
            })
        elif item not in (None, ""):
            normalized.append({
                "label": str(item),
                "status": "testing",
                "evidence_count": 0,
                "contradiction_count": 0,
                "last_source": "",
                "updated_at": "",
            })
    return normalized


def normalize_development_map(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else {}
        except Exception:
            raw = {}
    current = raw.copy() if isinstance(raw, dict) else {}
    normalized = default_development_map()
    normalized.update({k: v for k, v in current.items() if k not in {"hypotheses", "checks"}})
    normalized["hypotheses"] = _normalize_map_items(current.get("hypotheses"))
    raw_checks = _normalize_map_items(current.get("checks"))
    if raw_checks:
        normalized["checks"] = raw_checks
    for key in ("helps", "blocks", "slip_points", "return_points", "successful_strategies", "ineffective_strategies"):
        normalized[key] = _merge_unique_list([], normalized.get(key), limit=20)
    normalized["version"] = DEVELOPMENT_MAP_VERSION
    normalized["behavior_events_count"] = max(0, int(normalized.get("behavior_events_count") or 0))
    return normalized


def _map_touch_item(items: Any, label: str, *, source: str, evidence_delta: int = 0, contradiction_delta: int = 0, status: str = "testing") -> List[Dict[str, Any]]:
    label = str(label or "").strip()
    if not label:
        return _normalize_map_items(items)
    now = _utc_iso()
    normalized = _normalize_map_items(items)
    for item in normalized:
        if item["label"] == label:
            item["evidence_count"] = max(0, int(item.get("evidence_count") or 0) + evidence_delta)
            item["contradiction_count"] = max(0, int(item.get("contradiction_count") or 0) + contradiction_delta)
            if item["evidence_count"] >= 2 and item["evidence_count"] > item["contradiction_count"]:
                item["status"] = "confirmed"
            elif item["contradiction_count"] >= 2 and item["contradiction_count"] > item["evidence_count"]:
                item["status"] = "weakened"
            else:
                item["status"] = status or item.get("status") or "testing"
            item["last_source"] = source
            item["updated_at"] = now
            return normalized
    normalized.append({
        "label": label,
        "status": status,
        "evidence_count": max(0, evidence_delta),
        "contradiction_count": max(0, contradiction_delta),
        "last_source": source,
        "updated_at": now,
    })
    return normalized[-12:]


def _skill_label_for_map(skill_id: Any) -> str:
    if not skill_id:
        return ""
    raw = str(skill_id)
    return globals().get("SKILL_LABELS", {}).get(raw, raw)


def development_map_event_patch(profile: Dict[str, Any], signal_patch: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Update the development map from behavior signals without exposing internals."""
    profile = profile or {}
    signal_patch = signal_patch or {}
    source = source or "profile_signal"
    now = _utc_iso()
    dev_map = normalize_development_map(profile.get("development_map"))

    main = signal_patch.get("main_hypothesis") or signal_patch.get("main_pattern") or signal_patch.get("avoidance_pattern")
    if main:
        dev_map["hypotheses"] = _map_touch_item(dev_map.get("hypotheses"), str(main), source=source, evidence_delta=1, status="testing")
    for item in _as_list(signal_patch.get("secondary_hypotheses")):
        dev_map["hypotheses"] = _map_touch_item(dev_map.get("hypotheses"), str(item), source=source, status="testing")

    skill = _skill_label_for_map(signal_patch.get("best_skill") or signal_patch.get("last_successful_skill"))
    failed_skill = _skill_label_for_map(signal_patch.get("failed_skill") or signal_patch.get("worst_skill"))
    trigger = signal_patch.get("avoidance_trigger") or signal_patch.get("avoidance_reason")

    if source in {"action_done", "downscale_done", "after_action_note_saved"} or signal_patch.get("last_effect_note"):
        if skill:
            dev_map["helps"] = _merge_unique_list(dev_map.get("helps"), [skill], limit=12)
            dev_map["successful_strategies"] = _merge_unique_list(dev_map.get("successful_strategies"), [skill], limit=12)
            dev_map["hypotheses"] = _map_touch_item(dev_map.get("hypotheses"), f"помогает: {skill}", source=source, evidence_delta=1, status="testing")
        if signal_patch.get("preferred_activation") == "body_doubling":
            dev_map["checks"] = _map_touch_item(dev_map.get("checks"), "помогает ли присутствие других людей", source=source, evidence_delta=1, status="testing")
        if signal_patch.get("downscale_pattern") or int(signal_patch.get("downscale_count") or 0):
            dev_map["checks"] = _map_touch_item(dev_map.get("checks"), "помогает ли уменьшение шага", source=source, evidence_delta=1, status="testing")

    if source.startswith("downscale") or signal_patch.get("needs_downscale") or signal_patch.get("downscale_pattern"):
        dev_map["checks"] = _map_touch_item(dev_map.get("checks"), "помогает ли уменьшение шага", source=source, evidence_delta=1, status="testing")
        dev_map["helps"] = _merge_unique_list(dev_map.get("helps"), ["уменьшение шага"], limit=12)
        dev_map["successful_strategies"] = _merge_unique_list(dev_map.get("successful_strategies"), ["уменьшение шага"], limit=12)

    if source in {"action_failed", "downscale_even_too_hard"} or signal_patch.get("action_failed_count"):
        if trigger:
            dev_map["blocks"] = _merge_unique_list(dev_map.get("blocks"), [str(trigger)], limit=12)
            dev_map["slip_points"] = _merge_unique_list(dev_map.get("slip_points"), [str(trigger)], limit=12)
        if failed_skill:
            dev_map["ineffective_strategies"] = _merge_unique_list(dev_map.get("ineffective_strategies"), [failed_skill], limit=12)
            dev_map["hypotheses"] = _map_touch_item(dev_map.get("hypotheses"), f"не подходит: {failed_skill}", source=source, evidence_delta=1, status="testing")
        if main:
            dev_map["hypotheses"] = _map_touch_item(dev_map.get("hypotheses"), str(main), source=source, contradiction_delta=1, status="testing")

    if source == "return_after_slip" or signal_patch.get("return_pattern"):
        return_label = str(signal_patch.get("return_pattern") or "возврат после срыва")
        dev_map["return_points"] = _merge_unique_list(dev_map.get("return_points"), [return_label], limit=12)
        dev_map["helps"] = _merge_unique_list(dev_map.get("helps"), ["возврат через маленький шаг"], limit=12)
        dev_map["hypotheses"] = _map_touch_item(dev_map.get("hypotheses"), "возврат после срыва тренируется", source=source, evidence_delta=1, status="testing")

    for tag in _as_list(signal_patch.get("effect_tags")):
        dev_map["helps"] = _merge_unique_list(dev_map.get("helps"), [str(tag)], limit=12)

    dev_map["behavior_events_count"] = int(dev_map.get("behavior_events_count") or 0) + 1
    dev_map["last_update_source"] = source
    dev_map["updated_at"] = now
    return {"development_map": dev_map}


def default_development_history(now: Optional[str] = None) -> Dict[str, Any]:
    now = now or _utc_iso()
    return {
        "version": DEVELOPMENT_HISTORY_VERSION,
        "principle": "development_mirror_not_tracker",
        "snapshots": [],
        "periodic_reports": {
            "weekly": [],
            "monthly": [],
            "day90": [],
            "day180": [],
        },
        "updated_at": now,
    }


def _normalize_history_snapshots(items: Any) -> List[Dict[str, Any]]:
    snapshots: List[Dict[str, Any]] = []
    for item in _as_list(items):
        if not isinstance(item, dict):
            continue
        created_at = str(item.get("created_at") or item.get("date") or "")
        if not created_at:
            continue
        snapshots.append({
            "created_at": created_at,
            "source": str(item.get("source") or "profile_update"),
            "changed_fields": [str(x) for x in _as_list(item.get("changed_fields")) if x not in (None, "")],
            "status": str(item.get("status") or "learning"),
            "avatar_metrics": item.get("avatar_metrics") if isinstance(item.get("avatar_metrics"), dict) else {},
            "successful_skills": _merge_unique_list([], item.get("successful_skills"), limit=12),
            "ineffective_strategies": _merge_unique_list([], item.get("ineffective_strategies"), limit=12),
            "barriers": _merge_unique_list([], item.get("barriers"), limit=12),
            "working_strategies": _merge_unique_list([], item.get("working_strategies"), limit=12),
            "series_actions": int(item.get("series_actions") or 0),
            "return_count": int(item.get("return_count") or 0),
            "done_count": int(item.get("done_count") or 0),
            "slips_recorded": int(item.get("slips_recorded") or 0),
            "growth_directions": _merge_unique_list([], item.get("growth_directions"), limit=12),
        })
    snapshots.sort(key=lambda x: _parse_iso_datetime(x.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
    return snapshots[-240:]


def normalize_development_history(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else {}
        except Exception:
            raw = {}
    current = raw.copy() if isinstance(raw, dict) else {}
    normalized = default_development_history()
    normalized.update({k: v for k, v in current.items() if k not in {"snapshots", "periodic_reports"}})
    normalized["snapshots"] = _normalize_history_snapshots(current.get("snapshots"))
    reports = current.get("periodic_reports") if isinstance(current.get("periodic_reports"), dict) else {}
    normalized["periodic_reports"] = {
        "weekly": _merge_unique_list([], reports.get("weekly"), limit=24),
        "monthly": _merge_unique_list([], reports.get("monthly"), limit=18),
        "day90": _merge_unique_list([], reports.get("day90"), limit=8),
        "day180": _merge_unique_list([], reports.get("day180"), limit=4),
    }
    normalized["version"] = DEVELOPMENT_HISTORY_VERSION
    normalized["principle"] = "development_mirror_not_tracker"
    normalized["updated_at"] = normalized.get("updated_at") or _utc_iso()
    return normalized


def _snapshot_from_profile(profile: Dict[str, Any], source: str, changed_fields: Optional[List[str]] = None) -> Dict[str, Any]:
    avatar = normalize_development_avatar(profile.get("development_avatar"))
    dev_map = normalize_development_map(profile.get("development_map"))
    stats = profile.get("development_stats") if isinstance(profile.get("development_stats"), dict) else {}
    avatar_metrics = {
        key: int((avatar.get("metrics") or {}).get(key, {}).get("value") or DEVELOPMENT_AVATAR_BASE_VALUE)
        for key in DEVELOPMENT_AVATAR_DIMENSIONS
    }
    successful = _merge_unique_list(
        dev_map.get("successful_strategies"),
        [* _as_list(profile.get("successful_skills")), profile.get("best_skill"), profile.get("last_successful_skill")],
        limit=12,
    )
    ineffective = _merge_unique_list(dev_map.get("ineffective_strategies"), profile.get("failed_skills"), limit=12)
    barriers = _merge_unique_list(profile.get("barriers"), dev_map.get("blocks"), limit=12)
    growth_directions = _merge_unique_list(
        barriers,
        [DEVELOPMENT_AVATAR_DIMENSIONS[k]["label"] for k, v in sorted(avatar_metrics.items(), key=lambda item: item[1])[:3]],
        limit=12,
    )
    return {
        "created_at": _utc_iso(),
        "source": source or "profile_update",
        "changed_fields": changed_fields or [],
        "status": str(profile.get("status") or "learning"),
        "avatar_metrics": avatar_metrics,
        "successful_skills": successful,
        "ineffective_strategies": ineffective,
        "barriers": barriers,
        "working_strategies": _merge_unique_list(profile.get("working_strategies"), dev_map.get("helps"), limit=12),
        "series_actions": int(profile.get("streak") or stats.get("streak") or 0),
        "return_count": int(profile.get("return_count") or stats.get("return_count") or 0),
        "done_count": int(profile.get("done_count") or profile.get("action_done_count") or stats.get("done_count") or 0),
        "slips_recorded": int(avatar.get("slips_recorded") or stats.get("slips_recorded_as_information") or 0),
        "growth_directions": growth_directions,
    }


def append_development_history_snapshot(profile: Dict[str, Any], source: str, changed_fields: Optional[List[str]] = None) -> Dict[str, Any]:
    history = normalize_development_history(profile.get("development_history"))
    snapshot = _snapshot_from_profile(profile, source, changed_fields)
    snapshots = history.get("snapshots") or []
    last = snapshots[-1] if snapshots else {}
    last_time = _parse_iso_datetime(last.get("created_at"))
    now_time = _parse_iso_datetime(snapshot.get("created_at")) or datetime.now(timezone.utc)
    same_source_recently = (
        last.get("source") == snapshot.get("source")
        and last_time is not None
        and (now_time - last_time).total_seconds() < 60
    )
    if same_source_recently:
        snapshots[-1] = snapshot
    else:
        snapshots.append(snapshot)
    history["snapshots"] = _normalize_history_snapshots(snapshots)
    history["updated_at"] = snapshot["created_at"]
    profile["development_history"] = history
    return profile


def _history_list_text(items: Any, fallback: str, *, limit: int = 4) -> str:
    values = [str(x) for x in _as_list(items) if x not in (None, "")]
    if not values:
        return f"— {fallback}"
    return "\n".join(f"— {x}" for x in values[:limit])


def _snapshot_for_period(snapshots: List[Dict[str, Any]], period_days: int, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    if not snapshots:
        return None
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - period_days * 24 * 3600
    older = [s for s in snapshots if (_parse_iso_datetime(s.get("created_at")) or now).timestamp() <= cutoff]
    if older:
        return older[-1]
    return snapshots[0]


def _metric_delta_lines(current: Dict[str, int], baseline: Dict[str, int], *, limit: int = 4) -> List[str]:
    deltas = []
    for key, meta in DEVELOPMENT_AVATAR_DIMENSIONS.items():
        cur = int(current.get(key) or DEVELOPMENT_AVATAR_BASE_VALUE)
        old = int(baseline.get(key) or DEVELOPMENT_AVATAR_BASE_VALUE)
        delta = cur - old
        if delta > 0:
            deltas.append((delta, f"— {meta['label']} стало легче удерживать: +{delta} пунктов диапазона"))
        elif delta < 0:
            deltas.append((delta, f"— {meta['label']} пока требует внимания: {delta} пунктов диапазона"))
    if not deltas:
        return ["— изменения пока накапливаются; нужно больше действий для уверенного сравнения"]
    deltas.sort(key=lambda item: abs(item[0]), reverse=True)
    return [line for _, line in deltas[:limit]]


def _period_title(period_days: int) -> str:
    return {
        7: "недельный отчёт",
        30: "месячный отчёт",
        90: "отчёт за 90 дней",
        180: "отчёт за 180 дней",
    }.get(period_days, f"отчёт за {period_days} дней")


def render_development_mirror_report(profile: Dict[str, Any], period_days: int = 30) -> str:
    """Render the long-term development mirror: identity/behavior change, not dry stats."""
    profile = normalize_user_profile(profile)
    history = normalize_development_history(profile.get("development_history"))
    snapshots = history.get("snapshots") or []
    current = _snapshot_from_profile(profile, "mirror_now", [])
    baseline = _snapshot_for_period(snapshots, period_days) or current
    baseline_metrics = baseline.get("avatar_metrics") if isinstance(baseline.get("avatar_metrics"), dict) else {}
    current_metrics = current.get("avatar_metrics") if isinstance(current.get("avatar_metrics"), dict) else {}
    changed_lines = "\n".join(_metric_delta_lines(current_metrics, baseline_metrics))
    title = _period_title(period_days)
    baseline_date = str(baseline.get("created_at") or "первые данные")[:10]
    has_real_baseline = bool(snapshots) and baseline is not current
    comparison_note = (
        f"Сравнение с состоянием около {baseline_date}."
        if has_real_baseline
        else "Пока это первая линия сравнения; зеркало станет точнее через новые недели действий."
    )
    return (
        f"🪞 Зеркало развития — {title}\n"
        f"{comparison_note}\n\n"
        "Кем ты был(а) раньше:\n"
        f"{_history_list_text(baseline.get('barriers'), 'пока нет старой точки сравнения')}\n\n"
        "Что изменилось:\n"
        f"{changed_lines}\n"
        f"— выполненных действий в модели: {current.get('done_count', 0)}\n"
        f"— серия действий сейчас: {current.get('series_actions', 0)}\n"
        f"— возвратов после срыва в модели: {current.get('return_count', 0)}\n\n"
        "Какие стратегии сработали:\n"
        f"{_history_list_text(current.get('successful_skills') or current.get('working_strategies'), 'пока проверяем первые навыки')}\n\n"
        "Где стало легче:\n"
        f"{_history_list_text(current.get('working_strategies'), 'пока собираем подтверждения')}\n\n"
        "Где всё ещё трудно:\n"
        f"{_history_list_text(current.get('barriers') or current.get('ineffective_strategies'), 'пока нет устойчивого паттерна')}\n\n"
        "Главные направления роста:\n"
        f"{_history_list_text(current.get('growth_directions'), 'продолжаем уточнять')}\n\n"
        "Главная идея: «Я вижу, как меняюсь.»"
    )


def render_development_mirror_reports(profile: Dict[str, Any]) -> str:
    return "\n\n".join(
        render_development_mirror_report(profile, period_days=days)
        for days in (7, 30, 90, 180)
    )




def determine_development_focus(profile: Dict[str, Any]) -> Dict[str, str]:
    """Choose a current route focus from profile signals using cautious language."""
    profile = profile or {}
    dev_map = normalize_development_map(profile.get("development_map"))
    failed = _as_list(profile.get("failed_skills"))
    successful = _as_list(profile.get("successful_skills"))
    barriers = _as_list(profile.get("barriers"))
    blocks = _as_list(dev_map.get("blocks"))
    helps = _as_list(dev_map.get("helps"))
    target = str(profile.get("today_target") or profile.get("last_target") or "").lower()

    if int(profile.get("return_count") or 0) > 0 or dev_map.get("return_points"):
        code = "slip_recovery"
        reason = "сейчас видно несколько сигналов возврата после паузы, поэтому мы проверим мягкий маршрут обратно"
    elif profile.get("preferred_activation") == "body_doubling" or "body_doubling_plan" in successful:
        code = "social_activity"
        reason = "похоже, присутствие другого человека может снижать порог старта"
    elif profile.get("attention_pattern") == "scroll_autopilot" or int(profile.get("attention_escape_count") or 0) > 0:
        code = "attention_holding"
        reason = "сейчас видно, что внимание иногда уходит в автопилот, поэтому мы проверим короткий контейнер фокуса"
    elif profile.get("shame_signal") or profile.get("main_pattern") == "shame_self_attack" or any("самокрит" in str(x).lower() for x in barriers):
        code = "self_criticism"
        reason = "пока предполагаем, что самокритика после откладывания мешает старту"
    elif int(profile.get("downscale_count") or 0) > 0 or "уменьшение шага" in helps:
        code = "self_regulation"
        reason = "пока есть 1–2 сигнала, что уменьшение шага может помогать; нужно ещё несколько попыток"
    elif any(x in target for x in ("работ", "проект", "код", "созвон", "учеб", "документ")):
        code = "professional_activity"
        reason = "мы проверим профессиональную активность через маленький рабочий шаг"
    elif failed or blocks:
        code = "task_initiation"
        reason = "похоже, первый вход в задачу пока остаётся главным узким местом"
    else:
        code = "task_initiation"
        reason = "данных пока мало, поэтому начнём с самого безопасного фокуса — запуск задач"
    return {
        "code": code,
        "label": DEVELOPMENT_FOCUS_LABELS.get(code, code),
        "reason": reason,
    }


def daily_profile_explanation(profile: Dict[str, Any], skill_id: str = "", day: int | None = None) -> str:
    """Explain today's recommendation from accumulated profile without sounding diagnostic."""
    profile = profile or {}
    focus = determine_development_focus(profile)
    dev_map = normalize_development_map(profile.get("development_map"))

    yesterday_insights = []
    if int(profile.get("downscale_count") or 0) > 0 or "уменьшение шага" in _as_list(dev_map.get("helps")):
        yesterday_insights.append("вход в задачу часто становится слишком большим")
        yesterday_insights.append("после уменьшения шага становится легче")
    if profile.get("attention_pattern") == "scroll_autopilot" or int(profile.get("attention_escape_count") or 0):
        yesterday_insights.append("телефон появляется как способ уйти от напряжения")
    if profile.get("failed_skill") or profile.get("failed_skills"):
        yesterday_insights.append("если навык не подошёл, нужен более простой вход")
    if profile.get("best_skill") or profile.get("last_successful_skill"):
        skill = label(SKILL_LABELS, profile.get("best_skill") or profile.get("last_successful_skill"), str(profile.get("best_skill") or profile.get("last_successful_skill")))
        yesterday_insights.append(f"раньше помогал формат «{skill}»")
    if not yesterday_insights:
        yesterday_insights.append("данных пока мало, поэтому начинаем с самого безопасного входа")
    current_pattern = label(PATTERN_LABELS, profile.get("main_pattern") or profile.get("avoidance_pattern"), "важная задача → много вариантов → напряжение → откладывание → труднее начать")

    if skill_id:
        skill_label = label(SKILL_LABELS, skill_id, skill_id)
        today_hypothesis = f"помогает ли тебе навык «{skill_label}» как следующий маленький вход"
    elif int(profile.get("downscale_count") or 0) > 0:
        today_hypothesis = "помогает ли тебе вход без требования работать долго"
    else:
        today_hypothesis = "помогает ли тебе начать с одного маленького физического шага"

    day_num = max(1, int(day or 1))
    if day_num == 1:
        map_lines = [
            "🧭 Предварительная карта",
            "",
            "Пока это гипотеза.",
            "Сегодня мы смотрим, где ломается вход в задачу.",
        ]
    elif day_num == 2:
        first_signal_lines = [
            f"что помогает: {label(SKILL_LABELS, skill_id, 'маленький вход в задачу') if skill_id else 'маленький вход в задачу'}",
            "где шаг оказался большим",
            "как ты реагируешь на залипание",
        ]
        map_lines = [
            "🧭 Первые сигналы",
            "",
            "Уже видно:",
            "\n".join(f"— {item}" for item in first_signal_lines),
        ]
    elif day_num == 3:
        map_lines = [
            "🧭 Первый паттерн",
            "",
            "Теперь видно не отдельные реакции, а цикл:",
            current_pattern,
            "",
            "Можно продолжить коротко или включить полный режим.",
        ]
    else:
        new_insight = yesterday_insights[0] if yesterday_insights else "карта уточняется по сегодняшним действиям"
        map_lines = [
            "🧭 Уточнение карты",
            "",
            "Сегодня карта стала точнее:",
            new_insight,
            "",
            "Следующая проверка:",
            today_hypothesis,
        ]

    lines = [
        *map_lines,
        "",
        "Это не диагноз и не окончательный вывод.",
        "Карта уточняется по твоим действиям.",
        "",
        f"🧭 Сегодняшний фокус: {focus['label']}.",
        f"Почему так: {focus['reason']}.",
    ]
    if int(profile.get("downscale_count") or 0) > 0 or "уменьшение шага" in _as_list(dev_map.get("helps")):
        lines.append("В прошлый раз маленький шаг помог тебе начать, поэтому сегодня начнём так же.")
    if skill_id:
        skill_label = label(SKILL_LABELS, skill_id, skill_id)
        lines.append(f"Мы проверим навык «{skill_label}» как следующий маленький шаг маршрута.")
    lines.append("Эта модель будет уточняться после твоих действий; данных может быть пока мало.")
    return "\n".join(lines)

def render_development_map(profile: Dict[str, Any]) -> str:
    dev_map = normalize_development_map((profile or {}).get("development_map"))

    def lines_from(items: Any, fallback: str, limit: int = 4) -> str:
        values = [str(x) for x in _as_list(items) if x not in (None, "")]
        if not values:
            return f"— {fallback}"
        return "\n".join(f"— {x}" for x in values[:limit])

    hypotheses = _normalize_map_items(dev_map.get("hypotheses"))
    if hypotheses:
        hyp_lines = []
        for item in hypotheses[:4]:
            status = {"confirmed": "подтверждается", "weakened": "ослабла", "testing": "проверяем"}.get(item.get("status"), "проверяем")
            hyp_lines.append(f"— {item['label']} ({status})")
        hypotheses_text = "\n".join(hyp_lines)
    else:
        hypotheses_text = "— пока проверяем первые гипотезы"

    return (
        "🗺 Карта развития обновляется\n\n"
        "Система уже уточняет карту по твоим действиям, а не только по диагностике.\n\n"
        "Что начинает помогать:\n"
        f"{lines_from(dev_map.get('helps'), 'данных пока мало')}\n\n"
        "Что мешает / где застревание:\n"
        f"{lines_from(dev_map.get('blocks'), 'пока собираем')}\n\n"
        "Где происходит возврат:\n"
        f"{lines_from(dev_map.get('return_points'), 'пока наблюдаем')}\n\n"
        "Гипотезы карты:\n"
        f"{hypotheses_text}\n\n"
        "Чем больше реальных попыток, тем точнее становится карта."
    )


def _profile_items_text(items: Any, fallback: str = "пока собираем данные", *, limit: int = 5) -> str:
    normalized = [str(x) for x in _as_list(items) if x not in (None, "")]
    if not normalized:
        return f"- {fallback}"
    return "\n".join(f"- {x}" for x in normalized[:limit])


def _profile_skill_labels(items: Any, fallback: str = "пока проверяем") -> str:
    labels = []
    mapping = globals().get("SKILL_LABELS", {})
    for item in _as_list(items):
        if item in (None, ""):
            continue
        raw = str(item)
        labels.append(str(mapping.get(raw, raw)))
    return _profile_items_text(labels, fallback)


def build_profile_prompt(profile: Dict[str, Any]) -> str:
    """Build the hidden working profile used for personalization.

    This is intentionally stored as internal context, not as a user-facing text.
    """
    profile = profile or {}
    barriers = _merge_unique_list(profile.get("barriers"), profile.get("failure_patterns"), limit=8)
    working = _merge_unique_list(profile.get("working_strategies"), profile.get("resources"), limit=8)
    bad = _merge_unique_list(profile.get("failed_skills"), profile.get("worst_skill"), limit=8)
    good = _merge_unique_list(profile.get("successful_skills"), [profile.get("best_skill"), profile.get("last_successful_skill")], limit=8)

    if int(profile.get("downscale_count") or 0) > 0 or profile.get("needs_downscale"):
        working = _merge_unique_list(working, ["уменьшение шага", "минимальный вход"], limit=8)
    if profile.get("preferred_activation") == "body_doubling":
        good = _merge_unique_list(good, ["body_doubling_plan"], limit=8)
        working = _merge_unique_list(working, ["внешний контроль / присутствие другого человека"], limit=8)
    if int(profile.get("action_failed_count") or 0) > 0:
        bad = _merge_unique_list(bad, [profile.get("failed_skill") or "слишком крупный первый шаг"], limit=8)

    preferred_trainer = profile.get("preferred_trainer") or profile.get("trainer_current_mode") or ""
    attention = profile.get("attention_profile") if isinstance(profile.get("attention_profile"), dict) else {}
    motivation = profile.get("motivation_profile") if isinstance(profile.get("motivation_profile"), dict) else {}
    emotional = profile.get("emotional_profile") if isinstance(profile.get("emotional_profile"), dict) else {}
    notes = []
    if attention:
        notes.append(f"attention_profile={json.dumps(attention, ensure_ascii=False, sort_keys=True)}")
    if motivation:
        notes.append(f"motivation_profile={json.dumps(motivation, ensure_ascii=False, sort_keys=True)}")
    if emotional:
        notes.append(f"emotional_profile={json.dumps(emotional, ensure_ascii=False, sort_keys=True)}")

    return (
        "USER PROFILE\n\n"
        "Главные барьеры:\n"
        f"{_profile_items_text(barriers)}\n\n"
        "Рабочие стратегии:\n"
        f"{_profile_items_text(working)}\n\n"
        "Плохо работают:\n"
        f"{_profile_skill_labels(bad, 'пока нет подтверждённых неработающих навыков')}\n\n"
        "Хорошо работают:\n"
        f"{_profile_skill_labels(good, 'пока проверяем первые навыки')}\n\n"
        "Предпочтительный стиль/тренер:\n"
        f"- {preferred_trainer or 'пока не определён'}\n\n"
        "Дополнительные сигналы:\n"
        f"{_profile_items_text(notes, 'пока собираем данные')}"
    )


def default_user_profile(*, trainer_key: str = "") -> Dict[str, Any]:
    """Create the V1 dynamic user profile skeleton.

    The bot stores the profile in users.profile_json for backwards compatibility,
    but the object itself follows the `user_profile` patch: strengths, barriers,
    resources, failure patterns, working strategies and nested attention /
    motivation / emotional / development stats.
    """
    now = _utc_iso()
    return {
        "schema_version": USER_PROFILE_SCHEMA_VERSION,
        "status": "preliminary",
        "strengths": [],
        "barriers": [],
        "resources": [],
        "failure_patterns": [],
        "working_strategies": [],
        "user_model_events": [],
        "attention_profile": {},
        "motivation_profile": {},
        "emotional_profile": {},
        "preferred_trainer": trainer_key or "",
        "successful_skills": [],
        "failed_skills": [],
        "development_stats": {},
        "development_avatar": default_development_avatar(now),
        "development_map": default_development_map(now),
        "development_history": default_development_history(now),
        "current_development_focus": "task_initiation",
        "development_focus_reason": "данных пока мало, начинаем с запуска задач",
        "safety_tone_policy": "working_model_not_precise_assessment",
        "slip_principle": SLIP_AS_INFORMATION_PRINCIPLE,
        "avatar_version": DEVELOPMENT_AVATAR_VERSION,
        "profile_prompt": "",
        "created_at": now,
        "updated_at": now,
        "update_log": [],
    }


def normalize_user_profile(raw: Any, *, trainer_key: str = "") -> Dict[str, Any]:
    """Return a full V1 user profile while preserving legacy flat signals."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw else {}
        except Exception:
            raw = {}
    profile = raw.copy() if isinstance(raw, dict) else {}
    defaults = default_user_profile(trainer_key=trainer_key)
    normalized = {**defaults, **profile}

    for key in USER_PROFILE_LIST_FIELDS:
        normalized[key] = _merge_unique_list([], normalized.get(key))
    for key in USER_PROFILE_DICT_FIELDS:
        normalized[key] = normalized.get(key) if isinstance(normalized.get(key), dict) else {}

    normalized["development_avatar"] = normalize_development_avatar(normalized.get("development_avatar"))
    normalized["development_map"] = normalize_development_map(normalized.get("development_map"))
    normalized["development_history"] = normalize_development_history(normalized.get("development_history"))
    normalized["schema_version"] = USER_PROFILE_SCHEMA_VERSION
    normalized["avatar_version"] = int(normalized.get("avatar_version") or DEVELOPMENT_AVATAR_VERSION)
    if not normalized.get("preferred_trainer") and trainer_key:
        normalized["preferred_trainer"] = trainer_key
    normalized["created_at"] = normalized.get("created_at") or defaults["created_at"]
    normalized["updated_at"] = normalized.get("updated_at") or defaults["updated_at"]
    normalized["update_log"] = _merge_unique_list([], normalized.get("update_log"), limit=30)
    if not normalized.get("profile_prompt"):
        normalized["profile_prompt"] = build_profile_prompt(normalized)
    return normalized


def merge_user_profile_patch(profile: Dict[str, Any], patch: dict, *, source: str = "profile_patch") -> Dict[str, Any]:
    """Merge a profile patch without resetting accumulated model data."""
    merged = normalize_user_profile(profile)
    changed_fields: List[str] = []
    for key, value in (patch or {}).items():
        if value is None:
            continue
        if key in USER_PROFILE_LIST_FIELDS:
            before = list(merged.get(key) or [])
            merged[key] = _merge_unique_list(merged.get(key), value)
            if merged[key] != before:
                changed_fields.append(key)
        elif key in USER_PROFILE_DICT_FIELDS:
            incoming = value if isinstance(value, dict) else {}
            before = dict(merged.get(key) or {})
            merged[key] = {**before, **incoming}
            if merged[key] != before:
                changed_fields.append(key)
        else:
            if value == "" and merged.get(key):
                continue
            if merged.get(key) != value:
                merged[key] = value
                changed_fields.append(key)

    focus = determine_development_focus(merged)
    if merged.get("current_development_focus") != focus["code"]:
        merged["current_development_focus"] = focus["code"]
        changed_fields.append("current_development_focus")
    if merged.get("development_focus_reason") != focus["reason"]:
        merged["development_focus_reason"] = focus["reason"]
        changed_fields.append("development_focus_reason")

    merged["schema_version"] = USER_PROFILE_SCHEMA_VERSION
    merged["updated_at"] = _utc_iso()
    if changed_fields:
        log_item = {
            "source": source or "profile_patch",
            "fields": changed_fields,
            "created_at": merged["updated_at"],
        }
        merged["update_log"] = _merge_unique_list(merged.get("update_log"), [log_item], limit=30)
        merged = append_development_history_snapshot(merged, source or "profile_patch", changed_fields)
    merged["profile_prompt"] = build_profile_prompt(merged)
    return merged



def diagnosis_user_profile_patch(comp: Dict[str, Any]) -> Dict[str, Any]:
    """Build the first preliminary profile patch from diagnosis output."""
    bucket = str(comp.get("bucket") or "mixed").strip()
    mapping = {
        "anxiety": {
            "main_pattern": "anxiety_avoidance",
            "avoidance_reason": "fear_of_bad_result",
            "emotional_trigger": "shame_or_anxiety",
            "barriers": ["страх ошибки или оценки", "тревога перед входом в задачу"],
            "resources": ["бережный маленький шаг"],
            "failure_patterns": ["избегание из-за страха плохого результата"],
            "working_strategies": ["плохой черновик", "уменьшение шага"],
            "emotional_profile": {"dominant_load": "shame_or_anxiety"},
        },
        "low_energy": {
            "main_pattern": "start_avoidance",
            "avoidance_reason": "low_energy",
            "emotional_trigger": "fatigue_or_overload",
            "barriers": ["низкий ресурс на старте", "перегруз"],
            "resources": ["минимально жизнеспособный день"],
            "failure_patterns": ["задача не запускается, когда требует много энергии"],
            "working_strategies": ["сначала тело, потом задача", "минимальный вход"],
            "motivation_profile": {"energy_gate": "low_start_energy"},
            "emotional_profile": {"dominant_load": "fatigue_or_overload"},
        },
        "distractibility": {
            "main_pattern": "start_avoidance",
            "avoidance_reason": "unclear_first_step",
            "emotional_trigger": "distraction_or_restlessness",
            "barriers": ["неясный первый шаг", "отвлечения уводят от действия"],
            "resources": ["видимый следующий шаг"],
            "failure_patterns": ["уход в отвлечение до ясного старта"],
            "working_strategies": ["одно окно", "сделать следующий шаг видимым"],
            "attention_profile": {"risk": "scroll_autopilot_or_context_switching"},
        },
        "mixed": {
            "main_pattern": "start_avoidance",
            "avoidance_reason": "task_too_big",
            "emotional_trigger": "shame_or_anxiety",
            "barriers": ["первый шаг кажется слишком большим", "самокритика после откладывания"],
            "resources": ["способность вернуться через маленький шаг"],
            "failure_patterns": ["откладывание усиливается, когда задача выглядит большой"],
            "working_strategies": ["открыть задачу без требования работать", "уменьшение шага"],
            "attention_profile": {"start_gate": "unclear_or_large_first_step"},
            "emotional_profile": {"dominant_load": "shame_or_anxiety"},
        },
    }
    patch = dict(mapping.get(bucket, mapping["mixed"]))
    skills_focus = comp.get("skills_focus") if isinstance(comp.get("skills_focus"), list) else []
    selected_skill = comp.get("selected_skill")
    analysis_result = comp.get("analysis_result") if isinstance(comp.get("analysis_result"), dict) else {}
    if not selected_skill:
        selected_skill = analysis_result.get("recommended_variant")
    if comp.get("useful_signal"):
        patch["strengths"] = [str(comp.get("useful_signal"))]
    patch["working_strategies"] = [
        *patch.get("working_strategies", []),
        *[str(x) for x in skills_focus[:3] if x],
    ]
    if selected_skill:
        patch["working_strategies"].append(str(selected_skill))
        patch["recommended_variant"] = str(selected_skill)
    patch["status"] = "preliminary"
    patch["recommended_track"] = "procrastination"
    patch["development_stats"] = {
        "diagnosis_completed": True,
        "diagnosis_bucket": bucket,
        "profile_confidence": "preliminary",
    }
    return patch


# ============================================================
# 4) DB: schema + CRUD
# ============================================================

USER_FIELDS = [
    "user_id",
    "telegram_id",
    "chat_id",
    "name",
    "trainer_key",
    "trainer",
    "input_mode",
    "mode",
    "stage",
    "current_step",
    "bucket",
    "analysis_json",
    "plan_json",
    "pending_skill_id",
    "pending_skill_day",
    "today_target",
    "day",
    "day_number",
    "created_at",
    "updated_at",
    "schema_version",
    "first_start_date",
    "points",
    "level",
    "streak",
    "last_active",
    "plan_overrides_json",
    "trial_days",
    "trial_phase",
    "payment_status",
    "access_status",
    "free_mode",
    "paid_until",
    "last_payment_click",
    "is_test_user",
    "fast_forward_enabled",
    "last_morning_checkin_date",
    "last_evening_checkin_date",
    "notifications_enabled",
    "timezone",
    "reactivation_count",
    "pending_plan_change",
    "crisis_count",
    "test_answers",
    "done_count",
    "return_count",
    "analysis_retry_count",
    "has_started_training",
    "last_offer_shown_at",
    "profile_json",
    "last_micro_habit_id",
    "last_micro_habit_date",
    "micro_habit_json",
    "day_core_skill_id",
    "day_core_skill_date",
    "day_core_round_count",
    "current_core_skill_id",
    "current_skill_variant_id",
    "current_core_skill_date",
    "profile_map_shown_date",
    "profile_map_shown_count",
    "last_explanation_context",
    "safety_mode",
    "safety_last_risk",
    "safety_contact_status",
    "safety_resume_context",
    "return_mode",
    "current_state",
    "state_version",
    "current_action_id",
    "last_simplification_modality",
    "success_repeat_count",
    "day_closed",
    "today_closed",
    "last_day_closed_at",
    "day_status",
    "current_day_id",
    "current_session_id",
    "daily_skill_id",
    "daily_skill_name",
    "daily_skill_status",
    "current_task_id",
    "current_task_title",
    "current_task_description",
    "current_task_context",
    "current_next_physical_step",
    "current_task_status",
    "full_mode",
    "full_mode_started_at",
    "full_mode_until",
    "full_mode_plan_json",
    "pending_feedback_json",
    "last_action_request_context_json",
    "current_screen_id",
    "closed_day_extra_step_date",
    "closed_day_extra_step_count",
]

EVENT_NAME_ALIASES = {
    "crisis_open": "crisis_clicked",
    "crisis_message": "crisis_clicked",
    "done": "action_done",
    "return": "action_done",
    "not_done": "action_failed",
    "downscale_done": "action_done",
    "downscale_triggered": "action_downscaled",
    "day1_started": "diagnosis_started",
    "analysis_action_started": "diagnosis_started",
    "trainer_chosen": "trainer_selected",
}


EVENT_EXTRA_COLS = {
    "event_name": "TEXT",
    "event_data": "TEXT",
    "created_at": "TEXT",
    "synced": "INTEGER DEFAULT 0",
    "sync_attempts": "INTEGER DEFAULT 0",
    "last_sync_error": "TEXT",
    # Legacy columns kept for compatibility with existing analytics code.
    "ts": "REAL",
    "event": "TEXT",
    "meta": "TEXT",
    "stage": "TEXT",
}


async def ensure_events_schema(db: aiosqlite.Connection):
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            event_name TEXT,
            event_data TEXT,
            stage TEXT,
            created_at TEXT,
            synced INTEGER DEFAULT 0,
            sync_attempts INTEGER DEFAULT 0,
            last_sync_error TEXT,
            ts REAL,
            event TEXT,
            meta TEXT
        )
        """
    )
    cur = await db.execute("PRAGMA table_info(events)")
    cols = [r[1] for r in await cur.fetchall()]
    for col, ctype in EVENT_EXTRA_COLS.items():
        if col not in cols:
            await db.execute(f"ALTER TABLE events ADD COLUMN {col} {ctype}")

    await db.execute("UPDATE events SET event_name = event WHERE event_name IS NULL AND event IS NOT NULL")
    await db.execute("UPDATE events SET event_data = meta WHERE event_data IS NULL AND meta IS NOT NULL")
    await db.execute("UPDATE events SET created_at = datetime(ts, 'unixepoch') WHERE created_at IS NULL AND ts IS NOT NULL")
    await db.execute("UPDATE events SET synced = 0 WHERE synced IS NULL")
    await db.execute("UPDATE events SET sync_attempts = 0 WHERE sync_attempts IS NULL")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_events_synced ON events(synced, id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_events_user_id ON events(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_events_name ON events(event_name)")


def default_user(uid: int) -> Dict[str, Any]:
    """Создать нового пользователя с дефолтными значениями"""
    return {
        "user_id": uid,
        "telegram_id": uid,
        "chat_id": uid,
        "name": None,
        "trainer_key": "marsha",
        "trainer": "marsha",
        "input_mode": "text",   # text | voice | test
        "mode": "text",
        "stage": "start",
        "current_step": "start",
        "bucket": "mixed",
        "analysis_json": None,
        "plan_json": None,
        "pending_skill_id": None,
        "pending_skill_day": None,
        "today_target": None,
        "day": 1,
        "day_number": 1,
        "points": 0,
        "level": 1,
        "streak": 0,
        "last_active": 0.0,
        "updated_at": _utc_iso(),
        "schema_version": USER_STATE_SCHEMA_VERSION,
        "plan_overrides_json": None,
        "trial_days": 3,
        "trial_phase": "paid" if TEST_MODE else "trial3",
        "payment_status": "paid" if TEST_MODE else "trial",
        "access_status": "paid" if TEST_MODE else "trial",
        "free_mode": 0,
        "paid_until": None,
        "last_payment_click": None,
        "is_test_user": 0,
        "fast_forward_enabled": 0,
        "last_morning_checkin_date": None,
        "last_evening_checkin_date": None,
        "notifications_enabled": 1,
        "timezone": "Europe/Vilnius",
        "reactivation_count": 0,
        "pending_plan_change": None,
        "crisis_count": 0,
        "created_at": time.time(),
        "first_start_date": None,
        "test_answers": [],  # Временное хранилище для ответов теста
        "done_count": 0,
        "return_count": 0,
        "analysis_retry_count": 0,
        "has_started_training": 0,  # Флаг: 1 если юзер начал день 1
        "last_offer_shown_at": None,
        "last_explanation_context": None,
        "profile_json": default_user_profile(trainer_key="marsha"),
        "last_micro_habit_id": None,
        "last_micro_habit_date": None,
        "micro_habit_json": None,
        "day_core_skill_id": None,
        "day_core_skill_date": None,
        "day_core_round_count": 0,
        "current_core_skill_id": None,
        "current_skill_variant_id": None,
        "current_core_skill_date": None,
        "profile_map_shown_date": None,
        "profile_map_shown_count": 0,
        "safety_mode": "none",
        "safety_last_risk": "unknown",
        "safety_contact_status": "not_asked",
        "safety_resume_context": None,
        "return_mode": None,
        "current_state": "ONBOARDING",
        "state_version": 0,
        "current_action_id": None,
        "last_simplification_modality": None,
        "success_repeat_count": 0,
        "day_closed": 0,
        "today_closed": 0,
        "last_day_closed_at": None,
        "day_status": "open",
        "current_day_id": None,
        "current_session_id": None,
        "daily_skill_id": None,
        "daily_skill_name": None,
        "daily_skill_status": None,
        "current_task_id": None,
        "current_task_title": None,
        "current_task_description": None,
        "current_task_context": None,
        "current_next_physical_step": None,
        "current_task_status": None,
        "full_mode": 0,
        "full_mode_started_at": None,
        "full_mode_until": None,
        "full_mode_plan_json": None,
        "pending_feedback_json": None,
        "last_action_request_context_json": None,
        "current_screen_id": None,
        "closed_day_extra_step_date": None,
        "closed_day_extra_step_count": 0,
    }

async def init_db(db_path: str):
    """Инициализация БД"""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                telegram_id INTEGER UNIQUE,
                chat_id INTEGER,
                name TEXT,
                trainer_key TEXT,
                trainer TEXT,
                input_mode TEXT,
                mode TEXT,
                stage TEXT,
                current_step TEXT,
                bucket TEXT,
                analysis_json TEXT,
                plan_json TEXT,
                pending_skill_id TEXT,
                pending_skill_day INTEGER,
                today_target TEXT,
                day INTEGER,
                day_number INTEGER,
                created_at REAL,
                updated_at TEXT,
                schema_version INTEGER DEFAULT 2,
                first_start_date TEXT,
                points INTEGER,
                level INTEGER,
                streak INTEGER,
                last_active REAL,
                plan_overrides_json TEXT,
                trial_days INTEGER,
                trial_phase TEXT,
                payment_status TEXT,
                access_status TEXT,
                free_mode INTEGER,
                paid_until TEXT,
                last_payment_click TEXT,
                is_test_user INTEGER DEFAULT 0,
                fast_forward_enabled INTEGER DEFAULT 0,
                last_morning_checkin_date TEXT,
                last_evening_checkin_date TEXT,
                notifications_enabled INTEGER DEFAULT 1,
                timezone TEXT DEFAULT 'Europe/Vilnius',
                reactivation_count INTEGER DEFAULT 0,
                pending_plan_change TEXT,
                crisis_count INTEGER,
                test_answers TEXT,
                done_count INTEGER,
                return_count INTEGER,
                analysis_retry_count INTEGER,
                has_started_training INTEGER,
                last_offer_shown_at TEXT,
                profile_json TEXT DEFAULT '{}',
                last_micro_habit_id TEXT,
                last_micro_habit_date TEXT,
                micro_habit_json TEXT,
                day_core_skill_id TEXT,
                day_core_skill_date TEXT,
                day_core_round_count INTEGER DEFAULT 0,
                current_core_skill_id TEXT,
                current_skill_variant_id TEXT,
                current_core_skill_date TEXT,
                profile_map_shown_date TEXT,
                profile_map_shown_count INTEGER DEFAULT 0,
                safety_mode TEXT DEFAULT 'none',
                safety_last_risk TEXT DEFAULT 'unknown',
                safety_contact_status TEXT DEFAULT 'not_asked',
                safety_resume_context TEXT,
                current_state TEXT DEFAULT 'ONBOARDING',
                state_version INTEGER DEFAULT 0,
                current_action_id TEXT,
                last_simplification_modality TEXT,
                success_repeat_count INTEGER DEFAULT 0,
                day_closed INTEGER DEFAULT 0,
                today_closed INTEGER DEFAULT 0,
                last_day_closed_at TEXT,
                day_status TEXT DEFAULT 'open',
                current_day_id TEXT,
                current_session_id TEXT,
                daily_skill_id TEXT,
                daily_skill_name TEXT,
                daily_skill_status TEXT,
                current_task_id TEXT,
                current_task_title TEXT,
                current_task_description TEXT,
                current_task_context TEXT,
                current_next_physical_step TEXT,
                current_task_status TEXT,
                full_mode INTEGER DEFAULT 0,
                full_mode_started_at TEXT,
                full_mode_until TEXT,
                full_mode_plan_json TEXT,
                pending_feedback_json TEXT,
                current_screen_id TEXT,
                closed_day_extra_step_date TEXT,
                closed_day_extra_step_count INTEGER DEFAULT 0
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_days (
                day_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                day_number INTEGER NOT NULL,
                calendar_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                daily_skill_id TEXT,
                daily_skill_name TEXT,
                daily_skill_status TEXT,
                opened_at TEXT NOT NULL,
                closed_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                day_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                skill_id TEXT,
                task_id TEXT,
                result TEXT,
                barrier TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS action_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                day_id TEXT,
                attempt_id INTEGER,
                event_type TEXT NOT NULL,
                skill_id TEXT,
                task_id TEXT,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                feedback_type TEXT NOT NULL,
                value TEXT,
                comment TEXT,
                day_id TEXT,
                day_number INTEGER,
                skill_id TEXT,
                trainer_key TEXT,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_tasks (
                task_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                context TEXT,
                next_physical_step TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                opened_at TEXT NOT NULL,
                last_activity_at TEXT NOT NULL,
                current_screen TEXT
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_days_user ON user_days(user_id, day_number)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_attempts_day ON skill_attempts(day_id, attempt_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_action_events_user_day ON action_events(user_id, day_id, event_type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_feedback_user_type ON user_feedback(user_id, feedback_type, day_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_tasks_user ON user_tasks(user_id, status, updated_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id, last_activity_at)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_updated_at ON users(updated_at)")
        await db.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (USER_STATE_SCHEMA_VERSION, _utc_iso()),
        )
        await ensure_events_schema(db)
        await db.commit()

def sync_user_state_aliases(u: Dict[str, Any]) -> Dict[str, Any]:
    """Keep durable user-state columns in sync with legacy bot fields.

    The bot historically used user_id/day/stage/payment_status/trainer_key/input_mode.
    The persistent schema also stores telegram_id/day_number/current_step/access_status/
    trainer/mode so deploys and future code can resume users from explicit state columns.
    """
    uid = u.get("user_id") or u.get("telegram_id")
    if uid is not None:
        u["user_id"] = int(uid)
        u["telegram_id"] = int(uid)
        u.setdefault("chat_id", int(uid))
    day_value = int(u.get("day") or u.get("day_number") or 1)
    u["day"] = day_value
    u["day_number"] = day_value
    step_value = u.get("stage") or u.get("current_step") or "start"
    u["stage"] = step_value
    u["current_step"] = step_value
    access_value = u.get("payment_status") or u.get("access_status") or ("paid" if TEST_MODE else "trial")
    u["payment_status"] = access_value
    u["access_status"] = access_value
    trainer_value = u.get("trainer_key") or u.get("trainer") or "marsha"
    u["trainer_key"] = trainer_value
    u["trainer"] = trainer_value
    mode_value = u.get("input_mode") or u.get("mode") or "text"
    u["input_mode"] = mode_value
    u["mode"] = mode_value
    u["schema_version"] = max(int(u.get("schema_version") or 0), USER_STATE_SCHEMA_VERSION)
    u["safety_mode"] = u.get("safety_mode") or "none"
    u["safety_last_risk"] = u.get("safety_last_risk") or "unknown"
    u["safety_contact_status"] = u.get("safety_contact_status") or "not_asked"
    if not u.get("updated_at"):
        u["updated_at"] = _utc_iso()
    return u


async def get_user(uid: int, db_path: str) -> Dict[str, Any]:
    """Получить пользователя из БД"""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row:
            u = sync_user_state_aliases(default_user(uid))
            await save_user(u, db_path)
            return u

        cols = [description[0] for description in cur.description] if cur.description else []
        if cols:
            u = dict(zip(cols, row))
        else:
            u = dict(row) if hasattr(row, 'keys') else {}
        
        # Deserialize test_answers if stored as JSON string
        if 'test_answers' in u and u.get('test_answers'):
            try:
                u['test_answers'] = json.loads(u['test_answers']) if isinstance(u['test_answers'], str) else u['test_answers']
            except Exception:
                u['test_answers'] = []
        else:
            u['test_answers'] = []
        return sync_user_state_aliases(u)

async def save_user(u: Dict[str, Any], db_path: str):
    """Сохранить пользователя в БД без сброса состояния между деплоями."""
    u = sync_user_state_aliases(dict(u))
    u["updated_at"] = _utc_iso()
    u["schema_version"] = USER_STATE_SCHEMA_VERSION
    cols = USER_FIELDS
    vals = []
    for c in cols:
        v = u.get(c)
        # Serialize lists/dicts to JSON for storage
        if isinstance(v, (list, dict)):
            try:
                v = json.dumps(v, ensure_ascii=False)
            except Exception:
                v = None
        vals.append(v)
    placeholders = ",".join(["?"] * len(cols))
    cols_sql = ",".join(cols)

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            f"INSERT OR REPLACE INTO users ({cols_sql}) VALUES ({placeholders})",
            tuple(vals),
        )
        await db.commit()

# ============================================================
# DB MIGRATION + EVENTS (аналитика) + GAMIFY FIELDS
# ============================================================

EXTRA_USER_COLS = {
    "telegram_id": "INTEGER",
    "day_number": "INTEGER",
    "current_step": "TEXT",
    "access_status": "TEXT",
    "trainer": "TEXT",
    "mode": "TEXT",
    "updated_at": "TEXT",
    "schema_version": "INTEGER DEFAULT 2",
    "points": "INTEGER",
    "level": "INTEGER",
    "streak": "INTEGER",
    "last_active": "REAL",
    "first_start_date": "TEXT",
    "plan_overrides_json": "TEXT",   # правки плана после кризиса
    "trial_days": "INTEGER",         # 3 или 7
    "trial_phase": "TEXT",           # "trial3" / "trial7" / "paid" / ...
    "payment_status": "TEXT",        # "trial" / "paid" / "manual_pending" / ...
    "free_mode": "INTEGER",          # 1 если пользователь мягко отказался от оплаты
    "paid_until": "TEXT",
    "last_payment_click": "TEXT",
    "is_test_user": "INTEGER DEFAULT 0",
    "fast_forward_enabled": "INTEGER DEFAULT 0",
    "last_morning_checkin_date": "TEXT",
    "last_evening_checkin_date": "TEXT",
    "notifications_enabled": "INTEGER DEFAULT 1",
    "timezone": "TEXT DEFAULT 'Europe/Vilnius'",
    "reactivation_count": "INTEGER DEFAULT 0",
    "pending_plan_change": "TEXT",   # отложенная правка плана после кризиса
    "crisis_count": "INTEGER",       # лимит в trial
    "test_answers": "TEXT",
    "done_count": "INTEGER",
    "return_count": "INTEGER",
    "analysis_retry_count": "INTEGER",  # сколько раз пользователь сказал "ты меня не понял"
    "has_started_training": "INTEGER",  # 1 если юзер начал день 1
    "pending_skill_id": "TEXT",
    "pending_skill_day": "INTEGER",
    "today_target": "TEXT",
    "last_offer_shown_at": "TEXT",
    "last_explanation_context": "TEXT",
    "profile_json": "TEXT DEFAULT '{}'",
    "last_micro_habit_id": "TEXT",
    "last_micro_habit_date": "TEXT",
    "micro_habit_json": "TEXT",
    "day_core_skill_id": "TEXT",
    "day_core_skill_date": "TEXT",
    "day_core_round_count": "INTEGER DEFAULT 0",
    "current_core_skill_id": "TEXT",
    "current_skill_variant_id": "TEXT",
    "current_core_skill_date": "TEXT",
    "profile_map_shown_date": "TEXT",
    "profile_map_shown_count": "INTEGER DEFAULT 0",
    "safety_mode": "TEXT DEFAULT 'none'",
    "safety_last_risk": "TEXT DEFAULT 'unknown'",
    "safety_contact_status": "TEXT DEFAULT 'not_asked'",
    "safety_resume_context": "TEXT",
    "return_mode": "TEXT",
    "current_state": "TEXT DEFAULT 'ONBOARDING'",
    "state_version": "INTEGER DEFAULT 0",
    "current_action_id": "TEXT",
    "last_simplification_modality": "TEXT",
    "success_repeat_count": "INTEGER DEFAULT 0",
    "day_closed": "INTEGER DEFAULT 0",
    "today_closed": "INTEGER DEFAULT 0",
    "last_day_closed_at": "TEXT",
    "day_status": "TEXT DEFAULT 'open'",
    "current_day_id": "TEXT",
    "current_session_id": "TEXT",
    "daily_skill_id": "TEXT",
    "daily_skill_name": "TEXT",
    "daily_skill_status": "TEXT",
    "current_task_id": "TEXT",
    "current_task_title": "TEXT",
    "current_task_description": "TEXT",
    "current_task_context": "TEXT",
    "current_next_physical_step": "TEXT",
    "current_task_status": "TEXT",
    "full_mode": "INTEGER DEFAULT 0",
    "full_mode_started_at": "TEXT",
    "full_mode_until": "TEXT",
    "full_mode_plan_json": "TEXT",
    "pending_feedback_json": "TEXT",
    "last_action_request_context_json": "TEXT",
    "current_screen_id": "TEXT",
    "closed_day_extra_step_date": "TEXT",
    "closed_day_extra_step_count": "INTEGER DEFAULT 0"
}

async def migrate_db(db_path: str):
    """Мигрировать БД структуру"""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("PRAGMA table_info(users)")
        cols = [r[1] for r in await cur.fetchall()]

        for col, ctype in EXTRA_USER_COLS.items():
            if col not in cols:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {ctype}")

        # Non-destructive backfill: preserve legacy values and expose explicit
        # persistent state columns used to resume flows after deploys.
        await db.execute("UPDATE users SET telegram_id = user_id WHERE telegram_id IS NULL")
        await db.execute("UPDATE users SET day_number = COALESCE(day_number, day, 1)")
        await db.execute("UPDATE users SET current_step = COALESCE(current_step, stage, 'start')")
        await db.execute("UPDATE users SET access_status = COALESCE(access_status, payment_status, 'trial')")
        await db.execute("UPDATE users SET trainer = COALESCE(trainer, trainer_key, 'marsha')")
        await db.execute("UPDATE users SET mode = COALESCE(mode, input_mode, 'text')")
        await db.execute("UPDATE users SET updated_at = COALESCE(updated_at, datetime('now'))")
        await db.execute("UPDATE users SET schema_version = ? WHERE schema_version IS NULL OR schema_version < ?", (USER_STATE_SCHEMA_VERSION, USER_STATE_SCHEMA_VERSION))
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_users_updated_at ON users(updated_at)")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_days (
                day_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                day_number INTEGER NOT NULL,
                calendar_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                daily_skill_id TEXT,
                daily_skill_name TEXT,
                daily_skill_status TEXT,
                opened_at TEXT NOT NULL,
                closed_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                day_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                skill_id TEXT,
                task_id TEXT,
                result TEXT,
                barrier TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS action_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                day_id TEXT,
                attempt_id INTEGER,
                event_type TEXT NOT NULL,
                skill_id TEXT,
                task_id TEXT,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                feedback_type TEXT NOT NULL,
                value TEXT,
                comment TEXT,
                day_id TEXT,
                day_number INTEGER,
                skill_id TEXT,
                trainer_key TEXT,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_tasks (
                task_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                context TEXT,
                next_physical_step TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                session_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                opened_at TEXT NOT NULL,
                last_activity_at TEXT NOT NULL,
                current_screen TEXT
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_days_user ON user_days(user_id, day_number)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_attempts_day ON skill_attempts(day_id, attempt_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_action_events_user_day ON action_events(user_id, day_id, event_type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_feedback_user_type ON user_feedback(user_id, feedback_type, day_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_user_tasks_user ON user_tasks(user_id, status, updated_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id, last_activity_at)")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (USER_STATE_SCHEMA_VERSION, _utc_iso()),
        )

        await ensure_events_schema(db)

        await db.commit()

async def log_event(
    user_id: int,
    event_name: str,
    event_data: dict = None,
    stage: str = None,
    db_path: str = "bot.db",
    sheets_webhook_url: str = "",
):
    """Log event to SQLite only; Sheets sync happens asynchronously in the background.

    Supports the legacy call shape log_event(user_id, stage, event, meta, db_path, webhook)
    while accepting the new shape log_event(user_id, event_name, event_data=None, stage=None).
    Never raises into user-facing bot flows.
    """
    try:
        # Backward compatibility for existing calls: (user_id, stage, event, meta, db_path, webhook).
        if isinstance(event_data, str) and (stage is None or isinstance(stage, dict)):
            legacy_stage = event_name
            legacy_event = event_data
            legacy_meta = stage if isinstance(stage, dict) else {}
            stage = legacy_stage
            event_name = legacy_event
            event_data = legacy_meta

        clean_data = sanitize_event_data(event_data or {})
        event_name = EVENT_NAME_ALIASES.get(event_name, event_name)
        if stage and "stage" not in clean_data:
            clean_data["stage"] = stage

        event_data_s = json.dumps(clean_data, ensure_ascii=False)
        ts = time.time()
        created_at = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(db_path) as db:
            await ensure_events_schema(db)
            await db.execute(
                """
                INSERT INTO events(
                    user_id, event_name, event_data, stage, created_at,
                    synced, sync_attempts, last_sync_error, ts, event, meta
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    user_id,
                    event_name,
                    event_data_s,
                    stage,
                    created_at,
                    0,
                    0,
                    None,
                    ts,
                    event_name,
                    event_data_s,
                ),
            )
            await db.commit()
    except Exception as e:
        log.warning("log_event failed: %s", e)



async def ensure_user_session(user_id: int, db_path: str, current_screen: str = "") -> str:
    """Open or touch the current logical bot session."""
    now = _utc_iso()
    async with aiosqlite.connect(db_path) as db:
        session_id = f"{user_id}:{int(time.time())}"
        await db.execute(
            "INSERT INTO user_sessions (session_id, user_id, opened_at, last_activity_at, current_screen) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, now, now, current_screen),
        )
        await db.commit()
        return session_id


async def ensure_user_day(u: Dict[str, Any], db_path: str, *, calendar_date: str, skill_id: str = "", skill_name: str = "") -> str:
    """Ensure the user has one active product day; do not create a new day for more attempts."""
    day_number = int(u.get("day") or u.get("day_number") or 1)
    existing = u.get("current_day_id")
    async with aiosqlite.connect(db_path) as db:
        if existing:
            cur = await db.execute("SELECT status FROM user_days WHERE day_id=? AND user_id=?", (existing, u["user_id"]))
            row = await cur.fetchone()
            if row and row[0] == "active":
                return str(existing)
        day_id = f"{u['user_id']}:{day_number}"
        now = _utc_iso()
        await db.execute(
            """
            INSERT OR IGNORE INTO user_days
            (day_id, user_id, day_number, calendar_date, status, daily_skill_id, daily_skill_name, daily_skill_status, opened_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?, 'active', ?)
            """,
            (day_id, u["user_id"], day_number, calendar_date, skill_id, skill_name, now),
        )
        await db.execute(
            """
            UPDATE user_days
            SET status='active', daily_skill_id=COALESCE(NULLIF(?, ''), daily_skill_id),
                daily_skill_name=COALESCE(NULLIF(?, ''), daily_skill_name), daily_skill_status='active'
            WHERE day_id=? AND user_id=?
            """,
            (skill_id, skill_name, day_id, u["user_id"]),
        )
        await db.commit()
    u["current_day_id"] = day_id
    u["daily_skill_id"] = skill_id or u.get("daily_skill_id")
    u["daily_skill_name"] = skill_name or u.get("daily_skill_name")
    u["daily_skill_status"] = "active"
    return day_id


async def close_user_day(u: Dict[str, Any], db_path: str) -> None:
    day_id = u.get("current_day_id")
    if not day_id:
        return
    now = _utc_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE user_days SET status='closed', closed_at=?, daily_skill_status=COALESCE(daily_skill_status, 'active') WHERE day_id=? AND user_id=?",
            (now, day_id, u["user_id"]),
        )
        await db.commit()
    u["daily_skill_status"] = "closed"


async def create_skill_attempt(u: Dict[str, Any], db_path: str, *, skill_id: str, task_id: str = "", result: str = "started", barrier: str = "") -> int:
    day_id = u.get("current_day_id")
    if not day_id:
        day_id = await ensure_user_day(u, db_path, calendar_date=str(u.get("day_core_skill_date") or ""), skill_id=skill_id)
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO skill_attempts (day_id, user_id, skill_id, task_id, result, barrier, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (day_id, u["user_id"], skill_id, task_id, result, barrier, _utc_iso()),
        )
        attempt_id = int(cur.lastrowid)
        await db.execute(
            "INSERT INTO action_events (user_id, day_id, attempt_id, event_type, skill_id, task_id, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (u["user_id"], day_id, attempt_id, "attempt_started", skill_id, task_id, json.dumps({"result": result, "barrier": barrier}, ensure_ascii=False), _utc_iso()),
        )
        await db.commit()
        return attempt_id


async def attempt_count_for_day(day_id: str, db_path: str) -> int:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM skill_attempts WHERE day_id=?", (day_id,))
        row = await cur.fetchone()
        return int(row[0] if row else 0)


async def get_user_day_status(day_id: str, db_path: str) -> str:
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("SELECT status FROM user_days WHERE day_id=?", (day_id,))
        row = await cur.fetchone()
        return str(row[0]) if row else ""


def _new_task_id(user_id: int) -> str:
    return f"{user_id}:task:{int(time.time() * 1000)}"


async def save_current_task(u: Dict[str, Any], db_path: str, *, title: str, description: str = "", context: str = "", next_step: str = "") -> str:
    """Create a new active task and pause the previous one instead of overwriting it."""
    title = str(title or "").strip()
    if not title:
        title = "сегодняшняя задача"
    now = _utc_iso()
    old_task_id = u.get("current_task_id")
    async with aiosqlite.connect(db_path) as db:
        if old_task_id:
            await db.execute(
                "UPDATE user_tasks SET status='paused', updated_at=? WHERE task_id=? AND user_id=? AND status='active'",
                (now, old_task_id, u["user_id"]),
            )
        task_id = _new_task_id(int(u["user_id"]))
        await db.execute(
            """
            INSERT INTO user_tasks (task_id, user_id, title, description, context, next_physical_step, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (task_id, u["user_id"], title, description, context, next_step, now, now),
        )
        await db.commit()
    u["current_task_id"] = task_id
    u["current_task_title"] = title
    u["current_task_description"] = description or None
    u["current_task_context"] = context or None
    u["current_next_physical_step"] = next_step or None
    u["current_task_status"] = "active"
    u["today_target"] = title
    return task_id


async def update_current_task_step(u: Dict[str, Any], db_path: str, next_step: str) -> None:
    next_step = str(next_step or "").strip()
    if not u.get("current_task_id") or not next_step:
        return
    now = _utc_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE user_tasks SET next_physical_step=?, updated_at=? WHERE task_id=? AND user_id=?",
            (next_step, now, u["current_task_id"], u["user_id"]),
        )
        await db.commit()
    u["current_next_physical_step"] = next_step


async def get_user_tasks(user_id: int, db_path: str) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM user_tasks WHERE user_id=? ORDER BY created_at", (user_id,))
        return [dict(row) for row in await cur.fetchall()]


ACTION_EVENT_TYPES = {
    "attempt_started",
    "attempt_completed_self_reported",
    "slip_reported",
    "too_hard_reported",
    "no_energy_reported",
    "skill_changed",
    "skill_skipped",
    "step_reduced",
    "returned_after_slip",
    "day_closed",
    "stuck_reason_selected",
    "skill_result_reported",
    "crisis_started",
    "crisis_resolved_or_paused",
}


async def record_action_event(
    user_id: int,
    db_path: str,
    event_type: str,
    *,
    day_id: str = "",
    attempt_id: int | None = None,
    skill_id: str = "",
    task_id: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if event_type not in ACTION_EVENT_TYPES:
        raise ValueError(f"unknown action event type: {event_type}")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO action_events (user_id, day_id, attempt_id, event_type, skill_id, task_id, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, day_id or None, attempt_id, event_type, skill_id or None, task_id or None, json.dumps(metadata or {}, ensure_ascii=False), _utc_iso()),
        )
        await db.commit()


async def get_action_metrics(user_id: int, db_path: str, *, day_id: str = "") -> Dict[str, Dict[str, int]]:
    def empty() -> Dict[str, int]:
        return {
            "micro_approaches": 0,
            "slips": 0,
            "returns_after_slip": 0,
            "step_reductions": 0,
            "too_hard": 0,
            "no_energy": 0,
            "attempts_started": 0,
            "skill_skipped": 0,
        }

    mapping = {
        "attempt_completed_self_reported": "micro_approaches",
        "slip_reported": "slips",
        "returned_after_slip": "returns_after_slip",
        "step_reduced": "step_reductions",
        "too_hard_reported": "too_hard",
        "no_energy_reported": "no_energy",
        "attempt_started": "attempts_started",
        "skill_skipped": "skill_skipped",
    }
    metrics = {"today": empty(), "period": empty()}
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT day_id, event_type, COUNT(*) FROM action_events WHERE user_id=? GROUP BY day_id, event_type",
            (user_id,),
        )
        rows = await cur.fetchall()
    for row_day_id, event_type, count in rows:
        key = mapping.get(event_type)
        if not key:
            continue
        metrics["period"][key] += int(count)
        if day_id and row_day_id == day_id:
            metrics["today"][key] += int(count)
    return metrics


async def record_user_feedback(
    user_id: int,
    db_path: str,
    feedback_type: str,
    value: str,
    *,
    comment: str = "",
    day_id: str = "",
    day_number: int | None = None,
    skill_id: str = "",
    trainer_key: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Persist tester feedback outside the psychological user map."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                feedback_type TEXT NOT NULL,
                value TEXT,
                comment TEXT,
                day_id TEXT,
                day_number INTEGER,
                skill_id TEXT,
                trainer_key TEXT,
                metadata TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "INSERT INTO user_feedback (user_id, feedback_type, value, comment, day_id, day_number, skill_id, trainer_key, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id,
                feedback_type,
                value,
                comment,
                day_id or None,
                day_number,
                skill_id or None,
                trainer_key or None,
                json.dumps(metadata or {}, ensure_ascii=False),
                _utc_iso(),
            ),
        )
        await db.commit()


async def user_feedback_count(user_id: int, db_path: str, feedback_type: str, *, day_id: str = "") -> int:
    async with aiosqlite.connect(db_path) as db:
        if day_id:
            cur = await db.execute(
                "SELECT COUNT(*) FROM user_feedback WHERE user_id=? AND feedback_type=? AND day_id=?",
                (user_id, feedback_type, day_id),
            )
        else:
            cur = await db.execute(
                "SELECT COUNT(*) FROM user_feedback WHERE user_id=? AND feedback_type=?",
                (user_id, feedback_type),
            )
        row = await cur.fetchone()
    return int(row[0] if row else 0)


async def recent_user_feedback(user_id: int, db_path: str, *, limit: int = 20) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT feedback_type, value, comment, day_id, day_number, skill_id, trainer_key, metadata, created_at
            FROM user_feedback
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = await cur.fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata") or "{}")
        except Exception:
            item["metadata"] = {}
        result.append(item)
    return result


PATTERN_LABELS = {
    "fearofevaluation": "Страх оценки",
    "fear_of_evaluation": "Страх оценки",
    "shameselfattack": "Жёсткая самокритика",
    "shame_self_attack": "Жёсткая самокритика",
    "attention_autopilot": "Уход в быстрые стимулы",
    "task_avoidance": "Избегание трудной задачи",
    "perfectionism_start_block": "идеальный образ результата делает вход слишком дорогим",
    "entry_too_large": "первый шаг ощущается слишком большим",
    "micro_entry_block": "даже подготовка к старту воспринимается как задача",
    "start_avoidance": "сложно войти в действие",
    "anxiety_avoidance": "тревога делает вход в задачу небезопасным",
    "boredom_avoidance": "нет быстрого подкрепления, и мозг теряет интерес",
}

REASON_LABELS = {
    "fear_of_bad_result": "страх сделать плохо или неидеально",
    "task_too_big": "задача воспринимается слишком большой",
    "unclear_first_step": "неясен первый физический шаг",
    "low_energy": "мало ресурса для входа",
    "no_visible_result": "не видно быстрого результата",
}

SKILL_LABELS = {
    "openwithouttimer": "Открыть без таймера",
    "open_without_timer": "Открыть без таймера",
    "open_only": "открыть задачу без требования работать",
    "ninety_sec_start": "90 секунд входа",
    "bad_first_step": "плохой первый шаг",
    "task_naming": "назвать задачу одним словом",
    "one_tab_focus": "одно окно для удержания внимания",
    "visible_next_step": "сделать следующий шаг видимым",
    "phone_far_3min": "убрать телефон на 3 минуты",
    "restart_after_slip": "возврат после выпадения",
    "restart_after_break": "возврат после срыва",
    "self_criticism_to_instruction": "перевести самокритику в инструкцию",
    "check_the_facts_light": "проверить факт против приговора",
    "urge_surf_60": "пережить импульс отвлечься 60 секунд",
    "body_before_task": "сначала тело, потом задача",
    "minimum_viable_day": "минимально жизнеспособный день",
    "body_doubling_plan": "запуск рядом с человеком",
    "if_then_plan": "если–то план для маленького входа",
    "small_step": "маленький шаг",
    "draft_mode": "Плохой черновик на 2 минуты",
    "bad_draft": "Плохой черновик на 2 минуты",
}


def label(mapping: dict, value: Optional[str], fallback: str) -> str:
    if not value:
        return fallback
    raw = str(value)
    technical_labels = {
        "openwithouttimer": "Открыть без таймера",
        "open_without_timer": "Открыть без таймера",
        "fearofevaluation": "Страх оценки",
        "fear_of_evaluation": "Страх оценки",
        "shameselfattack": "Жёсткая самокритика",
        "shame_self_attack": "Жёсткая самокритика",
        "small_step": "маленький шаг",
        "draft_mode": "Плохой черновик на 2 минуты",
        "bad_draft": "Плохой черновик на 2 минуты",
        "attention_autopilot": "Уход в быстрые стимулы",
        "task_avoidance": "Избегание трудной задачи",
    }
    return mapping.get(raw, technical_labels.get(raw, raw))


async def get_user_profile(user_id: int, db_path: str = "bot.db") -> dict:
    user = await get_user(user_id, db_path)
    return normalize_user_profile(user.get("profile_json") or "{}", trainer_key=str(user.get("trainer_key") or ""))


async def update_user_profile(user_id: int, patch: dict, db_path: str = "bot.db", source: str = "profile_patch") -> dict:
    profile = await get_user_profile(user_id, db_path)
    profile = merge_user_profile_patch(profile, patch or {}, source=source)

    profile_json = json.dumps(profile, ensure_ascii=False)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE users SET profile_json = ? WHERE user_id = ?",
            (profile_json, user_id),
        )
        await db.commit()
    return profile


USER_MODEL_EVENT_TYPES = {
    "reported",
    "hypothesis",
    "intervention_offered",
    "intervention_attempted",
    "intervention_confirmed_helpful",
    "intervention_not_helpful",
    "barrier_reported",
    "barrier_confirmed",
    "contradiction",
}


def _human_skill_label(value: Any) -> str:
    if value in (None, ""):
        return ""
    return label(SKILL_LABELS, str(value), str(value))


def user_model_event(
    user_id: int,
    event_type: str,
    text: str,
    *,
    source_message_id: str = "",
    source_skill_id: str = "",
    confidence: float = 0.5,
    is_active: bool = True,
    event_id: str = "",
    created_at: str = "",
) -> Dict[str, Any]:
    """Create a normalized user-map event for fact/hypothesis/intervention separation."""
    safe_type = event_type if event_type in USER_MODEL_EVENT_TYPES else "hypothesis"
    return {
        "id": event_id or f"ume:{user_id}:{int(time.time() * 1000)}",
        "user_id": user_id,
        "event_type": safe_type,
        "text": str(text or "").strip(),
        "source_message_id": str(source_message_id or ""),
        "source_skill_id": str(source_skill_id or ""),
        "confidence": max(0.0, min(1.0, float(confidence or 0))),
        "created_at": created_at or _utc_iso(),
        "is_active": bool(is_active),
    }




def user_model_events_from_signal(user_id: int, signal_patch: Dict[str, Any], source: str) -> List[Dict[str, Any]]:
    """Translate legacy profile patches into explicit user-model provenance events."""
    patch = signal_patch or {}
    events: List[Dict[str, Any]] = []
    for item in _as_list(patch.get("confirmed_signals")):
        if item:
            events.append(user_model_event(user_id, "reported", str(item), confidence=0.9))
    if patch.get("main_hypothesis"):
        events.append(user_model_event(user_id, "hypothesis", f"Пока есть гипотеза, что {patch.get('main_hypothesis')}", confidence=0.5))
    for item in _as_list(patch.get("secondary_hypotheses")):
        if item:
            events.append(user_model_event(user_id, "hypothesis", f"Мы проверяем, может ли {item}", confidence=0.4))

    offered_skill = patch.get("recommended_core_skill") or patch.get("recommended_variant")
    if offered_skill:
        events.append(user_model_event(user_id, "intervention_offered", "", source_skill_id=str(offered_skill), confidence=0.6))

    skill = patch.get("best_skill") or patch.get("last_successful_skill") or patch.get("best_variant")
    if source in {"action_done", "downscale_done"} and skill:
        events.append(user_model_event(user_id, "intervention_attempted", "", source_skill_id=str(skill), confidence=0.6))
    if source == "after_action_note_saved" and skill:
        tags = set(str(x) for x in _as_list(patch.get("effect_tags")))
        positive = bool(tags & {"relief", "anxiety_down", "confidence_up", "clarity_up"}) or bool(patch.get("effect_relief") or patch.get("effect_anxiety_down"))
        event_type = "intervention_confirmed_helpful" if positive else "intervention_attempted"
        events.append(user_model_event(user_id, event_type, "", source_skill_id=str(skill), confidence=0.7 if positive else 0.5))

    failed_skill = patch.get("failed_skill") or patch.get("worst_skill")
    if source in {"action_failed", "downscale_even_too_hard"} and failed_skill:
        events.append(user_model_event(user_id, "intervention_not_helpful", "", source_skill_id=str(failed_skill), confidence=0.7))
    trigger = patch.get("avoidance_trigger") or patch.get("avoidance_reason")
    if trigger:
        events.append(user_model_event(user_id, "barrier_reported", str(trigger), confidence=0.6))
    return events


def _normalize_user_model_events(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for item in _as_list((profile or {}).get("user_model_events")):
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type") or "").strip()
        text = str(item.get("text") or "").strip()
        skill_id = str(item.get("source_skill_id") or "").strip()
        if not text and skill_id:
            text = _human_skill_label(skill_id)
        if not text or event_type not in USER_MODEL_EVENT_TYPES or item.get("is_active") is False:
            continue
        normalized = dict(item)
        normalized["event_type"] = event_type
        normalized["text"] = _human_skill_label(text) if text in SKILL_LABELS else text
        normalized["source_skill_id"] = skill_id
        events.append(normalized)
    return events[-80:]


def _event_skill_text(event: Dict[str, Any]) -> str:
    skill_id = str(event.get("source_skill_id") or "").strip()
    text = str(event.get("text") or "").strip()
    if skill_id:
        return _human_skill_label(skill_id)
    return _human_skill_label(text) if text in SKILL_LABELS else text



def profile_contradiction_prompt(profile: Dict[str, Any]) -> str:
    """Ask a clarifying question when reported signals conflict."""
    events = _normalize_user_model_events(profile or {})
    texts = "\n".join(str(e.get("text") or "") for e in events + [{"text": x} for x in _as_list((profile or {}).get("confirmed_signals"))]).lower()
    has_evaluation = any(x in texts for x in ("страх оцен", "люди увид", "недодел", "сделаю плохо", "оценят", "стыд"))
    has_meaning = any(x in texts for x in ("не вижу смысла", "нет смысла", "зачем", "бессмыс"))
    if not (has_evaluation and has_meaning):
        return ""
    return (
        "Ты описал страх оценки, но выбрал(а) “не вижу смысла”.\n"
        "Что чаще возникает ПЕРЕД тем, как ты уходишь в Telegram?\n\n"
        "😬 “Сделаю плохо, меня оценят”\n"
        "😶 “Не понимаю, зачем это вообще делать”\n"
        "🌀 Оба варианта\n"
        "✍️ Объясню иначе"
    )




def _skill_status_wording(status: str) -> str:
    return {
        "proposed": "данных пока мало",
        "tested_once": "данных пока мало",
        "started_task": "помог начать задачу",
        "promising": "помог",
        "confirmed": "помог",
        "not_helpful": "не помог",
    }.get(str(status or "proposed"), "данных пока мало")


_TECHNICAL_MAP_TOKENS = {
    "shametoaction",
    "entrysmallstep",
    "onevisiblestep",
}


def _clean_map_text(value: Any) -> str:
    """Return a short human-facing map phrase without internal IDs or autogenerated glue."""
    text = str(value or "").strip()
    if not text:
        return ""
    if text in SKILL_LABELS:
        text = _human_skill_label(text)
    low = text.lower().replace("_", "").replace("-", "")
    if low in _TECHNICAL_MAP_TOKENS:
        return ""
    # Drop obvious internal identifiers while keeping normal human phrases.
    if re.fullmatch(r"[a-z0-9_:-]{5,}", text) and not re.search(r"[а-яА-ЯёЁ\s]", text):
        return ""
    text = re.sub(r"\bume:\d+:\d+\b", "", text)
    text = re.sub(r"\s+", " ", text).strip(" —•;,.\n\t")
    return text


def _append_unique_clean(target: List[str], values: Any, *, limit: int = 5, prefix: str = "") -> List[str]:
    seen = {x.lower() for x in target}
    for raw in _as_list(values):
        text = _clean_map_text(raw)
        if not text:
            continue
        if prefix and not text.lower().startswith(prefix.lower()):
            text = f"{prefix}{text}"
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        target.append(text)
        if len(target) >= limit:
            break
    return target


def _map_bullets(items: Any, fallback: str, *, limit: int = 5) -> str:
    values: List[str] = []
    _append_unique_clean(values, items, limit=limit)
    if not values:
        values = [fallback]
    return "\n".join(f"— {item}" for item in values[:limit])


def _checked_skill_lines(profile: Dict[str, Any], events: List[Dict[str, Any]], skill_map: Any, limit: int = 5) -> List[str]:
    results: List[str] = []
    seen = set()

    def add(skill: Any, result: str) -> None:
        label_text = _clean_map_text(_human_skill_label(skill))
        if not label_text:
            return
        line = f"{label_text}: {result}"
        key = line.lower()
        if key not in seen and len(results) < limit:
            seen.add(key)
            results.append(line)

    for e in events:
        et = str(e.get("event_type") or "")
        skill = e.get("source_skill_id") or e.get("text")
        if et == "intervention_confirmed_helpful":
            add(skill, "помог")
        elif et == "intervention_not_helpful":
            add(skill, "не помог")
        elif et == "intervention_attempted":
            add(skill, "данных пока мало")
        elif et == "intervention_offered":
            add(skill, "данных пока мало")

    for sid in _as_list(profile.get("successful_skills")) + _as_list(profile.get("best_skill")) + _as_list(profile.get("last_successful_skill")):
        add(sid, "помог")
    for sid in _as_list(profile.get("failed_skills")) + _as_list(profile.get("failed_skill")) + _as_list(profile.get("worst_skill")):
        add(sid, "не помог")
    for sid in _as_list(profile.get("completed_skills_effect_unknown")):
        add(sid, "данных пока мало")

    skills = skill_map.get("skills") if isinstance(skill_map, dict) else []
    for item in skills or []:
        if not isinstance(item, dict):
            continue
        add(item.get("skill_id"), _skill_status_wording(str(item.get("status") or "proposed")))

    return results or ["данных пока мало"]


def render_short_user_map(profile: dict, name: Optional[str] = None) -> str:
    profile = normalize_user_profile(profile or {})
    events = _normalize_user_model_events(profile)

    reported = [_event_skill_text(e) for e in events if e["event_type"] in {"reported", "barrier_reported", "barrier_confirmed"}]
    _append_unique_clean(reported, profile.get("confirmed_signals"), limit=5)
    if profile.get("attention_pattern") == "scroll_autopilot" or int(profile.get("attention_escape_count") or 0):
        _append_unique_clean(reported, ["уход в быстрые стимулы / Telegram / новости"], limit=5)

    hypotheses: List[str] = []
    for e in events:
        if e["event_type"] == "hypothesis":
            text = _clean_map_text(_event_skill_text(e))
            if text:
                if "пока" not in text.lower():
                    text = f"пока {text}"
                _append_unique_clean(hypotheses, [text], limit=5)
    if profile.get("main_hypothesis"):
        _append_unique_clean(hypotheses, [f"пока {profile.get('main_hypothesis')}"], limit=5)
    for item in _as_list(profile.get("secondary_hypotheses")):
        _append_unique_clean(hypotheses, [f"пока проверяем: {item}"], limit=5)

    skill_map = profile.get("_skill_map") if isinstance(profile, dict) else {}
    checked = _checked_skill_lines(profile, events, skill_map, limit=5)

    offered = [_event_skill_text(e) for e in events if e["event_type"] in {"intervention_offered", "intervention_attempted"}]
    legacy_next = [profile.get("recommended_core_skill"), profile.get("recommended_variant"), profile.get("best_variant")]
    next_candidates: List[str] = []
    _append_unique_clean(next_candidates, offered + legacy_next, limit=1)
    contradiction = profile_contradiction_prompt(profile)
    if contradiction:
        next_test = "ответить на уточнение: что возникает перед уходом в Telegram"
    elif next_candidates:
        next_test = f"проверить «{next_candidates[0]}» и отметить: помог / не помог / не подошёл / данных пока мало"
    else:
        next_test = "выбрать один маленький вход в задачу и отметить честный результат"

    text = (
        "🧭 Твоя короткая карта\n\n"
        "Что ты описал\n"
        f"{_map_bullets(reported, 'данных пока мало — ждём твоих слов и фактов', limit=5)}\n\n"
        "Что мы пока предполагаем\n"
        f"{_map_bullets(hypotheses, 'пока вход в задачу может быть слишком большим', limit=5)}\n\n"
        "Что уже проверили\n"
        f"{_map_bullets(checked, 'данных пока мало', limit=5)}\n\n"
        "Что проверим следующим\n"
        f"— {next_test}"
    )
    if contradiction:
        text = f"{text}\n\n{contradiction}"
    return text

def gamify_apply(u: dict, delta_points: int, reason: str):
    """Launch-safe no-op: false points/levels/streaks are worse than no gamification."""
    u["last_active"] = time.time()
    u["gamify_reason"] = reason

def is_paid(u: dict) -> bool:
    """Проверить, есть ли полный доступ: paid, testmode или активная дата paid_until."""
    if TEST_MODE:
        return True
    if int(u.get("is_test_user") or 0) == 1 or str(u.get("payment_status") or "").lower() in {"paid", "test", "full"}:
        return True
    if u.get("trial_phase") == "paid":
        return True
    paid_until = u.get("paid_until")
    if paid_until:
        try:
            text = str(paid_until).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt > datetime.now(timezone.utc)
        except Exception:
            return False
    return False

def should_ping(u: dict, hours: int) -> bool:
    """Проверить, нужно ли пинговать пользователя"""
    try:
        last = float(u.get("last_active") or 0)
    except (TypeError, ValueError):
        last = 0.0
    return time.time() - last > hours * 3600
