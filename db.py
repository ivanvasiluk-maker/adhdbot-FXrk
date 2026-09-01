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
USER_STATE_SCHEMA_VERSION = 20


class StaleUserWriteError(RuntimeError):
    """A second handler tried to overwrite a newer user-state snapshot."""


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

POST_DIAGNOSTIC_STAGES = {
    "confirm_analysis",
    "analysis_details",
    "working_map",
    "analysis_rebuilt",
    "analysis_contract",
    "analysis_next_step",
    "waiting_next_day",
    "training",
    "offer",
    "closed_day_continue_confirm",
    "day_core_stop",
    "day_pause_confirm",
    "curator_path",
    "morning_checkin",
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


def _safe_json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


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
    "current_day",
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
    "notification_consent",
    "notification_time",
    "timezone",
    "reactivation_count",
    "last_user_activity_at",
    "last_bot_reactivation_at",
    "reactivation_count_today",
    "reactivation_date",
    "last_reactivation_variant",
    "last_bot_message_at",
    "pending_plan_change",
    "crisis_count",
    "test_answers",
    "done_count",
    "return_count",
    "pending_return_after_disruption",
    "pending_return_reason",
    "pending_return_date",
    "analysis_retry_count",
    "analysis_action_transition_shown",
    "has_started_training",
    "last_offer_shown_at",
    "offer_seen",
    "previous_stage",
    "offer_mode",
    "last_offer_action",
    "pending_offer_request_format",
    "profile_json",
    "profile_completed",
    "diagnostic_completed",
    "coach_style",
    "user_type_hypothesis",
    "user_map_json",
    "skill_effects_json",
    "day_history_json",
    "attempts_count",
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
    "row_revision",
    "current_action_id",
    "current_action_context",
    "last_simplification_modality",
    "success_repeat_count",
    "day_closed",
    "today_closed",
    "daily_training_completed",
    "interaction_allowed",
    "today_started",
    "last_day_closed_at",
    "day_date",
    "day_status",
    "current_day_id",
    "current_session_id",
    "daily_skill_id",
    "daily_skill_name",
    "daily_skill_status",
    "day_skill_progress",
    "daily_check_in_status",
    "daily_reminder_status",
    "skill_attempts_today",
    "skill_attempts",
    "streak_counted_today",
    "current_skill_completed_count",
    "daily_replacement_count",
    "replacements_today",
    "current_task_id",
    "current_task_title",
    "current_task_name",
    "current_task_object",
    "current_deadline",
    "current_task_next_step",
    "current_task_fear",
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
    "active_attempt",
    "active_flow",
    "last_safe_screen",
    "last_notification_context",
    "day_intro_sent",
    "crisis_redirected",
    "crisis_mode",
    "last_mini_lesson_date",
    # Spec: day/moment skill separation (section 3)
    "moment_skill_id",
    "moment_skill_date",
    "skill_step_history",
    # Spec: proactivity limits (section 4.4)
    "last_inactivity_reminder_date",
    "proactive_count_today",
    "proactive_count_date",
    "no_reminders_today",
    "no_reminders_date",
    "reminder_mode",
    "unanswered_proactive_count",
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


INTERNAL_TEST_USER_IDS = {312112015}
PRODUCT_ONCE_EVENTS = {
    "start",
    "diagnosis_completed",
    "recommended_track_shown",
    "day1_started",
    "analysis_action_started",
    "day_closed",
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
    "is_internal_test": "INTEGER DEFAULT 0",
    "analytics_event": "INTEGER DEFAULT 1",
    "dedupe_key": "TEXT",
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
        "current_day": 1,
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
        "notification_consent": 1,
        "notification_time": None,
        "timezone": "Europe/Vilnius",
        "reactivation_count": 0,
        "last_user_activity_at": None,
        "last_bot_reactivation_at": None,
        "reactivation_count_today": 0,
        "reactivation_date": None,
        "last_reactivation_variant": None,
        "last_bot_message_at": None,
        "pending_plan_change": None,
        "crisis_count": 0,
        "created_at": time.time(),
        "first_start_date": None,
        "test_answers": [],  # Временное хранилище для ответов теста
        "done_count": 0,
        "return_count": 0,
        "pending_return_after_disruption": 0,
        "pending_return_reason": None,
        "pending_return_date": None,
        "analysis_retry_count": 0,
        "analysis_action_transition_shown": 0,
        "has_started_training": 0,  # Флаг: 1 если юзер начал день 1
        "last_offer_shown_at": None,
        "offer_seen": 0,
        "previous_stage": None,
        "offer_mode": None,
        "last_offer_action": None,
        "pending_offer_request_format": None,
        "last_explanation_context": None,
        "profile_json": default_user_profile(trainer_key="marsha"),
        "profile_completed": 0,
        "diagnostic_completed": 0,
        "coach_style": "marsha",
        "user_type_hypothesis": None,
        "user_map_json": None,
        "skill_effects_json": None,
        "day_history_json": None,
        "attempts_count": 0,
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
        "row_revision": 0,
        "current_action_id": None,
        "current_action_context": None,
        "last_simplification_modality": None,
        "success_repeat_count": 0,
        "day_closed": 0,
        "today_closed": 0,
        "daily_training_completed": 0,
        "interaction_allowed": 1,
        "today_started": 0,
        "last_day_closed_at": None,
        "day_date": None,
        "day_status": "not_started",
        "current_day_id": None,
        "current_session_id": None,
        "daily_skill_id": None,
        "daily_skill_name": None,
        "daily_skill_status": None,
        "day_skill_progress": None,
        "active_flow": None,
        "last_safe_screen": None,
        "last_notification_context": None,
        "reminder_mode": "evening_only",
        "unanswered_proactive_count": 0,
        "daily_check_in_status": "pending",
        "daily_reminder_status": "enabled",
        "skill_attempts_today": 0,
        "skill_attempts": [],
        "streak_counted_today": 0,
        "current_skill_completed_count": 0,
        "daily_replacement_count": 0,
        "replacements_today": 0,
        "current_task_id": None,
        "current_task_title": None,
        "current_task_name": None,
        "current_task_object": None,
        "current_deadline": None,
        "current_task_next_step": None,
        "current_task_fear": None,
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
        "active_attempt": None,
        "day_intro_sent": 0,
        "crisis_redirected": 0,
        "crisis_mode": 0,
        "last_mini_lesson_date": None,
    }


async def _ensure_flow_and_mechanism_schema(db: aiosqlite.Connection) -> None:
    """Create durable experiment and behavioral-memory entities on startup."""
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS flow_states (
            user_id INTEGER PRIMARY KEY,
            current_step TEXT NOT NULL DEFAULT 'onboarding',
            active_experiment_id INTEGER,
            resume_step TEXT,
            revision INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS situation_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            task_summary TEXT NOT NULL,
            desired_action TEXT NOT NULL,
            context_domain TEXT NOT NULL,
            action_phase TEXT NOT NULL,
            emotion_intensity_0_100 INTEGER NOT NULL CHECK(emotion_intensity_0_100 BETWEEN 0 AND 100),
            energy_0_100 INTEGER NOT NULL CHECK(energy_0_100 BETWEEN 0 AND 100),
            urgency TEXT NOT NULL,
            raw_text_ref TEXT
        );
        CREATE TABLE IF NOT EXISTS mechanism_hypotheses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            situation_id INTEGER NOT NULL REFERENCES situation_snapshots(id),
            mechanism_code TEXT NOT NULL,
            confidence TEXT NOT NULL CHECK(confidence IN ('low','medium','high')),
            evidence_json TEXT NOT NULL DEFAULT '[]',
            unknowns_json TEXT NOT NULL DEFAULT '[]',
            disconfirming_questions_json TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL CHECK(source IN ('rules','llm','user_confirmed')),
            confirmed_by_user INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            situation_id INTEGER NOT NULL REFERENCES situation_snapshots(id),
            mechanism_hypothesis_id INTEGER NOT NULL REFERENCES mechanism_hypotheses(id),
            skill_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready',
            outcome TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS behavioral_experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            situation_id INTEGER NOT NULL REFERENCES situation_snapshots(id),
            mechanism_hypothesis_id INTEGER NOT NULL REFERENCES mechanism_hypotheses(id),
            skill_id TEXT NOT NULL,
            mechanism_code TEXT NOT NULL,
            context_domain TEXT NOT NULL,
            difficulty_level INTEGER NOT NULL CHECK(difficulty_level BETWEEN 1 AND 5),
            instruction_variant TEXT NOT NULL,
            target_action TEXT NOT NULL,
            success_criterion TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            status TEXT NOT NULL CHECK(status IN ('proposed','accepted','started','completed','abandoned','safety_stopped')),
            parent_experiment_id INTEGER REFERENCES behavioral_experiments(id),
            progression_type TEXT NOT NULL CHECK(progression_type IN ('first','repeat','simplify','advance','transfer','maintenance')),
            decision_reason_code TEXT NOT NULL,
            trainer_style TEXT NOT NULL,
            state_revision INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS behavioral_experiment_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER NOT NULL UNIQUE REFERENCES behavioral_experiments(id),
            criterion_met INTEGER NOT NULL,
            observed_result TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS experiment_outcomes (
            experiment_id INTEGER PRIMARY KEY REFERENCES behavioral_experiments(id),
            action_started TEXT NOT NULL CHECK(action_started IN ('yes','partial','no')),
            action_persisted TEXT NOT NULL CHECK(action_persisted IN ('yes','partial','no','not_applicable')),
            emotional_change TEXT NOT NULL CHECK(emotional_change IN ('better','same','worse','unknown')),
            before_intensity_0_100 INTEGER CHECK(before_intensity_0_100 BETWEEN 0 AND 100),
            after_intensity_0_100 INTEGER CHECK(after_intensity_0_100 BETWEEN 0 AND 100),
            success_criterion_met INTEGER NOT NULL CHECK(success_criterion_met IN (0,1)),
            independent_use INTEGER NOT NULL CHECK(independent_use IN (0,1)),
            user_note_short TEXT,
            failure_reason_code TEXT CHECK(failure_reason_code IN
                ('too_hard','wrong_mechanism','unclear_instruction','insufficient_repetition',
                 'wrong_timing','external_blocker','safety_deterioration','skill_mismatch','unknown')),
            captured_at TEXT NOT NULL,
            CHECK(success_criterion_met = 1 OR failure_reason_code IS NOT NULL)
        );
        CREATE TABLE IF NOT EXISTS behavioral_experiment_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER NOT NULL REFERENCES behavioral_experiments(id),
            outcome_id INTEGER REFERENCES behavioral_experiment_outcomes(id),
            decision TEXT NOT NULL,
            reason_code TEXT NOT NULL,
            policy_version TEXT NOT NULL DEFAULT 'post-experiment-v1',
            ranking_version TEXT NOT NULL DEFAULT 'ranking-v1',
            skill_version TEXT NOT NULL DEFAULT '1.0.0',
            next_experiment_id INTEGER REFERENCES behavioral_experiments(id),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_situations_user_created ON situation_snapshots(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_mechanisms_situation ON mechanism_hypotheses(situation_id);
        CREATE INDEX IF NOT EXISTS idx_experiments_user_status ON experiments(user_id, status);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_one_productive_experiment_per_user
            ON behavioral_experiments(user_id)
            WHERE status IN ('proposed','accepted','started');
        CREATE INDEX IF NOT EXISTS idx_behavioral_chain_parent ON behavioral_experiments(parent_experiment_id);

        CREATE TABLE IF NOT EXISTS user_mechanism_profile (
            user_id INTEGER NOT NULL,
            mechanism_code TEXT NOT NULL,
            context_domain TEXT NOT NULL,
            evidence_count INTEGER NOT NULL DEFAULT 0 CHECK(evidence_count >= 0),
            last_seen_at TEXT NOT NULL,
            typical_barriers_json TEXT NOT NULL DEFAULT '[]',
            confidence TEXT NOT NULL CHECK(confidence IN ('low','medium','high')),
            evidence_refs_json TEXT NOT NULL,
            PRIMARY KEY (user_id, mechanism_code, context_domain)
        );
        CREATE TABLE IF NOT EXISTS user_skill_effectiveness (
            user_id INTEGER NOT NULL,
            skill_id TEXT NOT NULL,
            mechanism_code TEXT NOT NULL,
            context_domain TEXT NOT NULL,
            attempts_count INTEGER NOT NULL DEFAULT 0 CHECK(attempts_count >= 0),
            successes_count INTEGER NOT NULL DEFAULT 0 CHECK(successes_count >= 0),
            independent_successes INTEGER NOT NULL DEFAULT 0 CHECK(independent_successes >= 0),
            worse_count INTEGER NOT NULL DEFAULT 0 CHECK(worse_count >= 0),
            last_used_at TEXT NOT NULL,
            effectiveness_band TEXT NOT NULL CHECK(effectiveness_band IN ('unknown','promising','working','unreliable','avoid')),
            preferred_difficulty INTEGER CHECK(preferred_difficulty BETWEEN 1 AND 5),
            preferred_trainer_style TEXT,
            migration_confidence TEXT NOT NULL DEFAULT 'high' CHECK(migration_confidence IN ('low','medium','high')),
            evidence_refs_json TEXT NOT NULL,
            PRIMARY KEY (user_id, skill_id, mechanism_code, context_domain)
        );
        CREATE TABLE IF NOT EXISTS behavioral_patterns (
            user_id INTEGER NOT NULL,
            pattern_code TEXT NOT NULL,
            summary TEXT NOT NULL CHECK(length(summary) BETWEEN 1 AND 280),
            evidence_refs TEXT NOT NULL,
            last_updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, pattern_code)
        );
        CREATE TABLE IF NOT EXISTS operational_raw_context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            raw_context TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_skill_preferences (
            user_id INTEGER NOT NULL,
            skill_id TEXT NOT NULL,
            recommendation_disabled INTEGER NOT NULL DEFAULT 0 CHECK(recommendation_disabled IN (0,1)),
            correction_ref TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, skill_id)
        );
        CREATE TABLE IF NOT EXISTS skill_mastery_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            skill_id TEXT NOT NULL,
            experiment_id INTEGER NOT NULL REFERENCES behavioral_experiments(id),
            from_status TEXT NOT NULL,
            to_status TEXT NOT NULL CHECK(to_status IN ('NEW','LEARNING','PRACTICING','MASTERED','GENERALIZING')),
            reason_code TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS skill_mastery (
            user_id INTEGER NOT NULL,
            skill_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('NEW','LEARNING','PRACTICING','GENERALIZING','MASTERED')),
            current_difficulty INTEGER NOT NULL CHECK(current_difficulty BETWEEN 1 AND 5),
            successful_practice_count INTEGER NOT NULL DEFAULT 0 CHECK(successful_practice_count >= 0),
            independent_use_count INTEGER NOT NULL DEFAULT 0 CHECK(independent_use_count >= 0),
            generalized_contexts_json TEXT NOT NULL DEFAULT '[]',
            failed_contexts_json TEXT NOT NULL DEFAULT '[]',
            scaffolding_level TEXT NOT NULL CHECK(scaffolding_level IN ('full','reduced','minimal','none')),
            last_used_at TEXT NOT NULL,
            regression_flag INTEGER NOT NULL DEFAULT 0 CHECK(regression_flag IN (0,1)),
            migration_confidence TEXT NOT NULL DEFAULT 'high' CHECK(migration_confidence IN ('low','medium','high')),
            version INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, skill_id)
        );
        CREATE TABLE IF NOT EXISTS skill_mastery_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            skill_id TEXT NOT NULL,
            experiment_id INTEGER NOT NULL REFERENCES behavioral_experiments(id),
            event_type TEXT NOT NULL CHECK(event_type IN
                ('first_use','success','independent_use','difficulty_up','transfer','mastered','regression')),
            from_status TEXT NOT NULL,
            to_status TEXT NOT NULL,
            context_domain TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS legacy_migration_links (
            source_table TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            migrated_at TEXT NOT NULL,
            PRIMARY KEY (source_table, source_id, target_type)
        );
        CREATE TABLE IF NOT EXISTS behavioral_analytics_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_name TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            situation_id INTEGER,
            experiment_id INTEGER,
            skill_id TEXT,
            mechanism_code TEXT,
            context_domain TEXT,
            outcome_label TEXT,
            count_value INTEGER NOT NULL DEFAULT 1,
            policy_version TEXT NOT NULL,
            ranking_version TEXT NOT NULL,
            skill_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            synced INTEGER NOT NULL DEFAULT 0 CHECK(synced IN (0,1)),
            sync_attempts INTEGER NOT NULL DEFAULT 0,
            last_sync_error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_behavioral_memory_lookup
            ON user_skill_effectiveness(user_id, mechanism_code, context_domain, effectiveness_band);
        CREATE INDEX IF NOT EXISTS idx_operational_context_expiry ON operational_raw_context(expires_at);
        CREATE INDEX IF NOT EXISTS idx_mastery_history_user_skill
            ON skill_mastery_history(user_id,skill_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_skill_mastery_events_user_skill
            ON skill_mastery_events(user_id,skill_id,created_at);
        CREATE INDEX IF NOT EXISTS idx_behavioral_analytics_funnel
            ON behavioral_analytics_events(event_name,created_at,user_id);
        """
    )


async def _insert_behavioral_analytics(
    db: aiosqlite.Connection, event: "BehavioralAnalyticsEvent", *, created_at: str | None = None,
) -> int:
    from core.behavioral_analytics import BehavioralAnalyticsEvent
    if not isinstance(event, BehavioralAnalyticsEvent):
        raise TypeError("event must be BehavioralAnalyticsEvent")
    cur = await db.execute(
        """INSERT INTO behavioral_analytics_events
           (event_name,user_id,situation_id,experiment_id,skill_id,mechanism_code,
            context_domain,outcome_label,count_value,policy_version,ranking_version,skill_version,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (event.event_name, event.user_id, event.situation_id, event.experiment_id,
         event.skill_id or None, event.mechanism_code or None, event.context_domain or None,
         event.outcome_label or None, event.count_value, event.policy_version,
         event.ranking_version, event.skill_version, created_at or _utc_iso()),
    )
    return int(cur.lastrowid)


async def record_behavioral_analytics_event(
    db_path: str, event: "BehavioralAnalyticsEvent", *, created_at: str | None = None,
) -> int:
    async with aiosqlite.connect(db_path) as db:
        await _ensure_flow_and_mechanism_schema(db)
        event_id = await _insert_behavioral_analytics(db, event, created_at=created_at)
        await db.commit()
        return event_id


async def get_behavioral_kpis(
    db_path: str, *, created_from: str | None = None, created_to: str | None = None,
) -> Dict[str, Any]:
    """Compute the August funnel from normalized facts; no message text is read."""
    from core.behavioral_analytics import build_kpis
    filters = []
    params: list[Any] = []
    if created_from:
        filters.append("created_at>=?")
        params.append(created_from)
    if created_to:
        filters.append("created_at<?")
        params.append(created_to)
    where = "WHERE " + " AND ".join(filters) if filters else ""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        event_rows = await (await db.execute(
            f"""SELECT event_name,COUNT(*) AS count,COUNT(DISTINCT user_id) AS users
                FROM behavioral_analytics_events {where} GROUP BY event_name""", params,
        )).fetchall()
        events = {row["event_name"]: int(row["count"]) for row in event_rows}
        event_users = {row["event_name"]: int(row["users"]) for row in event_rows}
        situation_filter = where.replace("created_at", "s.created_at")
        experiment_filter = where.replace("created_at", "COALESCE(b.started_at,b.completed_at)")
        situations = await (await db.execute(
            f"""SELECT COUNT(DISTINCT s.id) AS total,
                       COUNT(DISTINCT CASE WHEN b.id IS NOT NULL THEN s.id END) AS converted
                FROM situation_snapshots s LEFT JOIN behavioral_experiments b ON b.situation_id=s.id
                {situation_filter}""", params,
        )).fetchone()
        repeats = await (await db.execute(
            f"""SELECT COUNT(*) AS total,
                       COALESCE(SUM(CASE WHEN o.success_criterion_met=1 THEN 1 ELSE 0 END),0) AS successful
                FROM behavioral_experiments b LEFT JOIN experiment_outcomes o ON o.experiment_id=b.id
                {experiment_filter + (' AND ' if experiment_filter else ' WHERE ') + "b.progression_type!='first'"}""",
            params,
        )).fetchone()
        timing = await (await db.execute(
            """WITH per_skill AS (
                 SELECT user_id,skill_id,MIN(created_at) AS first_at,
                        MIN(CASE WHEN to_status='PRACTICING' THEN created_at END) AS practicing_at,
                        MIN(CASE WHEN to_status='MASTERED' THEN created_at END) AS mastered_at
                 FROM skill_mastery_events GROUP BY user_id,skill_id
               ) SELECT
                 AVG(CASE WHEN practicing_at IS NOT NULL THEN (julianday(practicing_at)-julianday(first_at))*86400 END),
                 AVG(CASE WHEN mastered_at IS NOT NULL THEN (julianday(mastered_at)-julianday(first_at))*86400 END)
               FROM per_skill"""
        )).fetchone()
    counts = {
        "started_experiments": events.get("experiment_started", 0),
        "completed_experiments": events.get("experiment_completed", 0),
        "action_started": events.get("action_started", 0),
        "situations": int(situations["total"] or 0),
        "situations_with_experiment": int(situations["converted"] or 0),
        "repeat_experiments": int(repeats["total"] or 0),
        "successful_repeats": int(repeats["successful"] or 0),
        "d3_value_proof_eligible": event_users.get("value_report_viewed", 0),
        "d3_users": max(event_users.get("value_report_viewed", 0), event_users.get("offer_shown", 0)),
        "worse_outcomes": 0,
        "independent_uses": events.get("independent_use", 0),
        "transfers": events.get("skill_transferred", 0),
        "value_reports": events.get("value_report_viewed", 0),
        "offers": events.get("offer_shown", 0),
        "verified_purchases": events.get("purchase_confirmed", 0),
        "time_to_practicing_seconds": round(float(timing[0]), 2) if timing and timing[0] is not None else None,
        "time_to_mastered_seconds": round(float(timing[1]), 2) if timing and timing[1] is not None else None,
    }
    # Worse is a bounded outcome label, not inferred from arbitrary event metadata.
    counts["worse_outcomes"] = sum(
        int(row[0]) for row in await _analytics_outcome_counts(db_path, "worse", created_from, created_to)
    )
    return {"counts": counts, "kpis": build_kpis(counts)}


async def _analytics_outcome_counts(
    db_path: str, outcome_label: str, created_from: str | None, created_to: str | None,
) -> list[tuple[int]]:
    clauses = ["event_name='experiment_completed'", "outcome_label=?"]
    params: list[Any] = [outcome_label]
    if created_from:
        clauses.append("created_at>=?")
        params.append(created_from)
    if created_to:
        clauses.append("created_at<?")
        params.append(created_to)
    async with aiosqlite.connect(db_path) as db:
        return await (await db.execute(
            f"SELECT COUNT(*) FROM behavioral_analytics_events WHERE {' AND '.join(clauses)}", params,
        )).fetchall()

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
                current_day INTEGER,
                day_number INTEGER,
                created_at REAL,
                updated_at TEXT,
                schema_version INTEGER DEFAULT 3,
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
                notification_consent INTEGER DEFAULT 1,
                notification_time TEXT,
                timezone TEXT DEFAULT 'Europe/Vilnius',
                reactivation_count INTEGER DEFAULT 0,
                pending_plan_change TEXT,
                crisis_count INTEGER,
                test_answers TEXT,
                done_count INTEGER,
                return_count INTEGER,
                pending_return_after_disruption INTEGER DEFAULT 0,
                pending_return_reason TEXT,
                pending_return_date TEXT,
                analysis_retry_count INTEGER,
                has_started_training INTEGER,
                last_offer_shown_at TEXT,
                offer_seen INTEGER DEFAULT 0,
                previous_stage TEXT,
                offer_mode TEXT,
                last_offer_action TEXT,
                pending_offer_request_format TEXT,
                profile_json TEXT DEFAULT '{}',
                profile_completed INTEGER DEFAULT 0,
                diagnostic_completed INTEGER DEFAULT 0,
                coach_style TEXT,
                user_type_hypothesis TEXT,
                user_map_json TEXT,
                skill_effects_json TEXT,
                day_history_json TEXT,
                attempts_count INTEGER DEFAULT 0,
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
                row_revision INTEGER DEFAULT 0,
                current_action_id TEXT,
                last_simplification_modality TEXT,
                success_repeat_count INTEGER DEFAULT 0,
                day_closed INTEGER DEFAULT 0,
                today_closed INTEGER DEFAULT 0,
                today_started INTEGER DEFAULT 0,
                last_day_closed_at TEXT,
                day_status TEXT DEFAULT 'not_started',
                last_user_activity_at TEXT,
                last_bot_reactivation_at TEXT,
                reactivation_count_today INTEGER DEFAULT 0,
                reactivation_date TEXT,
                last_reactivation_variant TEXT,
                last_bot_message_at TEXT,
                current_day_id TEXT,
                current_session_id TEXT,
                daily_skill_id TEXT,
                daily_skill_name TEXT,
                daily_skill_status TEXT,
                day_skill_progress TEXT,
                daily_check_in_status TEXT DEFAULT 'pending',
                daily_reminder_status TEXT DEFAULT 'enabled',
                skill_attempts_today INTEGER DEFAULT 0,
                streak_counted_today INTEGER DEFAULT 0,
                current_skill_completed_count INTEGER DEFAULT 0,
                daily_replacement_count INTEGER DEFAULT 0,
                replacements_today INTEGER DEFAULT 0,
                current_task_id TEXT,
                current_task_title TEXT,
                current_task_name TEXT,
                current_task_object TEXT,
                current_deadline TEXT,
                current_task_next_step TEXT,
                current_task_fear TEXT,
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
                closed_day_extra_step_count INTEGER DEFAULT 0,
                active_attempt TEXT,
                day_intro_sent INTEGER DEFAULT 0,
                crisis_redirected INTEGER DEFAULT 0,
                crisis_mode INTEGER DEFAULT 0
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
        # A pre-PATCH-15 database may not have these columns until migrate_db.
        init_user_columns = {row[1] for row in await (await db.execute("PRAGMA table_info(users)")).fetchall()}
        if "telegram_id" in init_user_columns:
            await db.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)")
        if "updated_at" in init_user_columns:
            await db.execute("CREATE INDEX IF NOT EXISTS idx_users_updated_at ON users(updated_at)")
        await db.execute(
            "INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (USER_STATE_SCHEMA_VERSION, _utc_iso()),
        )
        await ensure_events_schema(db)
        await _ensure_flow_and_mechanism_schema(db)
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
    u["current_day"] = day_value
    u["day_number"] = day_value
    # Legacy handlers still mutate ``stage`` directly. The dedicated
    # flow_states repository is authoritative for new-engine transitions;
    # this adapter mirrors legacy stage until those handlers are migrated.
    step_value = u.get("stage") or u.get("current_step") or "start"
    u["stage"] = step_value
    u["current_step"] = step_value
    access_value = u.get("payment_status") or u.get("access_status") or ("paid" if TEST_MODE else "trial")
    u["payment_status"] = access_value
    u["access_status"] = access_value
    trainer_value = u.get("trainer_key") or u.get("trainer") or "marsha"
    u["trainer_key"] = trainer_value
    u["trainer"] = trainer_value
    u["coach_style"] = u.get("coach_style") or trainer_value
    mode_value = u.get("input_mode") or u.get("mode") or "text"
    u["input_mode"] = mode_value
    u["mode"] = mode_value
    profile = _safe_json_dict(u.get("profile_json"))
    diagnostic_completed = bool(
        int(u.get("has_started_training") or 0) == 1
        or u.get("first_start_date")
        or str(u.get("stage") or "") in POST_DIAGNOSTIC_STAGES
    )
    u["diagnostic_completed"] = 1 if diagnostic_completed or int(u.get("diagnostic_completed") or 0) == 1 else 0
    u["profile_completed"] = 1 if int(u.get("diagnostic_completed") or 0) == 1 or int(u.get("profile_completed") or 0) == 1 else 0
    notifications_enabled = 1 if int(u.get("notifications_enabled", 1) or 0) == 1 else 0
    u["notifications_enabled"] = notifications_enabled
    u["notification_consent"] = notifications_enabled
    if not u.get("notification_time") and notifications_enabled:
        u["notification_time"] = "09:00"
    attempts_count = max(
        int(u.get("attempts_count") or 0),
        int(u.get("done_count") or 0),
        int(u.get("return_count") or 0),
        len(_as_list(profile.get("successful_skills"))),
        len(_as_list(profile.get("failed_skills"))),
        len(_as_list(profile.get("completed_skills_effect_unknown"))),
    )
    u["attempts_count"] = attempts_count
    u["offer_seen"] = 1 if u.get("last_offer_shown_at") or int(u.get("offer_seen") or 0) == 1 else 0
    u["crisis_mode"] = 1 if str(u.get("safety_mode") or "none") != "none" or int(u.get("crisis_redirected") or 0) == 1 else 0
    if not u.get("day_status") or str(u.get("day_status")).lower() == "open":
        u["day_status"] = "active" if (int(u.get("today_started") or 0) == 1 or int(u.get("has_started_training") or 0) == 1) else "not_started"
    u["day_skill_progress"] = (
        u.get("day_skill_progress")
        or u.get("daily_skill_status")
        or (_safe_json_dict(u.get("active_attempt")).get("attempt_status"))
        or "not_started"
    )
    if not u.get("daily_check_in_status"):
        u["daily_check_in_status"] = "done" if u.get("last_morning_checkin_date") else "pending"
    if not u.get("daily_reminder_status"):
        u["daily_reminder_status"] = "enabled" if notifications_enabled else "disabled"
    profile_main = profile.get("main_hypothesis") or profile.get("main_pattern") or profile.get("avoidance_pattern")
    if profile_main and not u.get("user_type_hypothesis"):
        u["user_type_hypothesis"] = str(profile_main)
    if profile.get("development_map") and not u.get("user_map_json"):
        u["user_map_json"] = profile.get("development_map")
    if not u.get("skill_effects_json"):
        u["skill_effects_json"] = {
            "helpful": _as_list(profile.get("successful_skills"))[-12:],
            "not_helpful": _as_list(profile.get("failed_skills"))[-12:],
            "unknown": _as_list(profile.get("completed_skills_effect_unknown"))[-12:],
        }
    if not u.get("day_history_json") and profile.get("development_history"):
        u["day_history_json"] = profile.get("development_history")
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
        if 'active_attempt' in u and u.get('active_attempt'):
            try:
                u['active_attempt'] = json.loads(u['active_attempt']) if isinstance(u['active_attempt'], str) else u['active_attempt']
            except Exception:
                u['active_attempt'] = None
        else:
            u['active_attempt'] = None
        if 'skill_attempts' in u and u.get('skill_attempts'):
            try:
                parsed = json.loads(u['skill_attempts']) if isinstance(u['skill_attempts'], str) else u['skill_attempts']
                u['skill_attempts'] = parsed if isinstance(parsed, list) else []
            except Exception:
                u['skill_attempts'] = []
        else:
            u['skill_attempts'] = []
        for json_field in ("active_flow", "last_safe_screen", "last_notification_context"):
            if json_field in u and u.get(json_field):
                try:
                    parsed = json.loads(u[json_field]) if isinstance(u[json_field], str) else u[json_field]
                    u[json_field] = parsed if isinstance(parsed, dict) else None
                except Exception:
                    u[json_field] = None
            else:
                u[json_field] = None
        u = sync_user_state_aliases(u)
        u["_loaded_row_revision"] = int(u.get("row_revision") or 0)
        return u

async def save_user(u: Dict[str, Any], db_path: str):
    """Persist one user snapshot with optimistic concurrency protection."""
    state = sync_user_state_aliases(dict(u))
    expected_revision = int(u.get("_loaded_row_revision", state.get("row_revision") or 0))
    state["row_revision"] = expected_revision + 1
    state["updated_at"] = _utc_iso()
    state["schema_version"] = USER_STATE_SCHEMA_VERSION
    cols = USER_FIELDS
    vals = []
    for c in cols:
        v = state.get(c)
        # Serialize lists/dicts to JSON for storage
        if isinstance(v, (list, dict)):
            try:
                v = json.dumps(v, ensure_ascii=False)
            except Exception:
                v = None
        vals.append(v)
    placeholders = ",".join(["?"] * len(cols))
    cols_sql = ",".join(cols)
    update_cols = [column for column in cols if column != "user_id"]
    assignments = ",".join(f"{column}=?" for column in update_cols)
    storage = dict(zip(cols, vals))

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            f"UPDATE users SET {assignments} WHERE user_id=? AND COALESCE(row_revision,0)=?",
            tuple(storage[column] for column in update_cols) + (state["user_id"], expected_revision),
        )
        changed = int((await (await db.execute("SELECT changes()")).fetchone())[0] or 0)
        if changed == 0:
            existing = await (await db.execute(
                "SELECT COALESCE(row_revision,0) FROM users WHERE user_id=?", (state["user_id"],),
            )).fetchone()
            if existing is not None:
                await db.rollback()
                raise StaleUserWriteError(
                    f"user {state['user_id']} changed concurrently: expected row revision "
                    f"{expected_revision}, current {int(existing[0] or 0)}"
                )
            await db.execute(
                f"INSERT INTO users ({cols_sql}) VALUES ({placeholders})",
                tuple(vals),
            )
        await db.commit()
    u.update({column: state.get(column) for column in USER_FIELDS})
    u["_loaded_row_revision"] = int(state["row_revision"])

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
    "schema_version": "INTEGER DEFAULT 3",
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
    "notification_consent": "INTEGER DEFAULT 1",
    "notification_time": "TEXT",
    "timezone": "TEXT DEFAULT 'Europe/Vilnius'",
    "reactivation_count": "INTEGER DEFAULT 0",
    "last_user_activity_at": "TEXT",
    "last_bot_reactivation_at": "TEXT",
    "reactivation_count_today": "INTEGER DEFAULT 0",
    "reactivation_date": "TEXT",
    "last_reactivation_variant": "TEXT",
    "last_bot_message_at": "TEXT",
    "pending_plan_change": "TEXT",   # отложенная правка плана после кризиса
    "crisis_count": "INTEGER",       # лимит в trial
    "test_answers": "TEXT",
    "done_count": "INTEGER",
    "return_count": "INTEGER",
    "pending_return_after_disruption": "INTEGER DEFAULT 0",
    "pending_return_reason": "TEXT",
    "pending_return_date": "TEXT",
    "analysis_retry_count": "INTEGER",  # сколько раз пользователь сказал "ты меня не понял"
    "analysis_action_transition_shown": "INTEGER DEFAULT 0",
    "has_started_training": "INTEGER",  # 1 если юзер начал день 1
    "pending_skill_id": "TEXT",
    "pending_skill_day": "INTEGER",
    "today_target": "TEXT",
    "current_day": "INTEGER",
    "last_offer_shown_at": "TEXT",
    "offer_seen": "INTEGER DEFAULT 0",
    "previous_stage": "TEXT",
    "offer_mode": "TEXT",
    "last_offer_action": "TEXT",
    "pending_offer_request_format": "TEXT",
    "last_explanation_context": "TEXT",
    "profile_json": "TEXT DEFAULT '{}'",
    "profile_completed": "INTEGER DEFAULT 0",
    "diagnostic_completed": "INTEGER DEFAULT 0",
    "coach_style": "TEXT",
    "user_type_hypothesis": "TEXT",
    "user_map_json": "TEXT",
    "skill_effects_json": "TEXT",
    "day_history_json": "TEXT",
    "attempts_count": "INTEGER DEFAULT 0",
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
    "row_revision": "INTEGER DEFAULT 0",
    "current_action_id": "TEXT",
    "current_action_context": "TEXT",
    "last_simplification_modality": "TEXT",
    "success_repeat_count": "INTEGER DEFAULT 0",
    "day_closed": "INTEGER DEFAULT 0",
    "today_closed": "INTEGER DEFAULT 0",
    "daily_training_completed": "INTEGER DEFAULT 0",
    "interaction_allowed": "INTEGER DEFAULT 1",
    "today_started": "INTEGER DEFAULT 0",
    "last_day_closed_at": "TEXT",
    "day_date": "TEXT",
    "day_status": "TEXT DEFAULT 'not_started'",
    "current_day_id": "TEXT",
    "current_session_id": "TEXT",
    "daily_skill_id": "TEXT",
    "daily_skill_name": "TEXT",
    "daily_skill_status": "TEXT",
    "day_skill_progress": "TEXT",
    "daily_check_in_status": "TEXT DEFAULT 'pending'",
    "daily_reminder_status": "TEXT DEFAULT 'enabled'",
    "skill_attempts_today": "INTEGER DEFAULT 0",
    "skill_attempts": "TEXT",
    "streak_counted_today": "INTEGER DEFAULT 0",
    "current_skill_completed_count": "INTEGER DEFAULT 0",
    "daily_replacement_count": "INTEGER DEFAULT 0",
    "replacements_today": "INTEGER DEFAULT 0",
    "current_task_id": "TEXT",
    "current_task_title": "TEXT",
    "current_task_name": "TEXT",
    "current_task_object": "TEXT",
    "current_deadline": "TEXT",
    "current_task_next_step": "TEXT",
    "current_task_fear": "TEXT",
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
    "closed_day_extra_step_count": "INTEGER DEFAULT 0",
    "active_attempt": "TEXT",
    "active_flow": "TEXT",
    "last_safe_screen": "TEXT",
    "last_notification_context": "TEXT",
    "day_intro_sent": "INTEGER DEFAULT 0",
    "crisis_redirected": "INTEGER DEFAULT 0",
    "crisis_mode": "INTEGER DEFAULT 0",
    "last_mini_lesson_date": "TEXT",
    # Spec: day/moment skill separation (section 3)
    "moment_skill_id": "TEXT",
    "moment_skill_date": "TEXT",
    "skill_step_history": "TEXT",
    # Spec: proactivity limits (section 4.4)
    "last_inactivity_reminder_date": "TEXT",
    "proactive_count_today": "INTEGER DEFAULT 0",
    "proactive_count_date": "TEXT",
    "no_reminders_today": "INTEGER DEFAULT 0",
    "no_reminders_date": "TEXT",
    "reminder_mode": "TEXT DEFAULT 'evening_only'",
    "unanswered_proactive_count": "INTEGER DEFAULT 0",
}

async def migrate_db(db_path: str):
    """Мигрировать БД структуру"""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("PRAGMA table_info(users)")
        cols = [r[1] for r in await cur.fetchall()]

        for col, ctype in EXTRA_USER_COLS.items():
            if col not in cols:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {ctype}")

        cols = [r[1] for r in await (await db.execute("PRAGMA table_info(users)")).fetchall()]

        # Non-destructive backfill: preserve legacy values and expose explicit
        # persistent state columns used to resume flows after deploys.
        await db.execute("UPDATE users SET telegram_id = user_id WHERE telegram_id IS NULL")
        day_source = "day" if "day" in cols else "1"
        await db.execute(f"UPDATE users SET day_number = COALESCE(day_number, {day_source}, 1)")
        await db.execute("UPDATE users SET current_step = COALESCE(current_step, stage, 'start')")
        payment_source = "payment_status" if "payment_status" in cols else "'trial'"
        trainer_source = "trainer_key" if "trainer_key" in cols else "'marsha'"
        mode_source = "input_mode" if "input_mode" in cols else "'text'"
        await db.execute(f"UPDATE users SET access_status = COALESCE(access_status, {payment_source}, 'trial')")
        await db.execute(f"UPDATE users SET trainer = COALESCE(trainer, {trainer_source}, 'marsha')")
        await db.execute(f"UPDATE users SET mode = COALESCE(mode, {mode_source}, 'text')")
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
        await _ensure_flow_and_mechanism_schema(db)

        # PATCH-15: additive columns for databases that created these tables
        # before migration confidence became explicit. Never rebuild/drop them.
        for table, column, declaration in (
            ("user_skill_effectiveness", "migration_confidence", "TEXT NOT NULL DEFAULT 'high'"),
            ("skill_mastery", "migration_confidence", "TEXT NOT NULL DEFAULT 'high'"),
            ("behavioral_experiment_decisions", "policy_version", "TEXT NOT NULL DEFAULT 'post-experiment-v1'"),
            ("behavioral_experiment_decisions", "ranking_version", "TEXT NOT NULL DEFAULT 'ranking-v1'"),
            ("behavioral_experiment_decisions", "skill_version", "TEXT NOT NULL DEFAULT '1.0.0'"),
        ):
            existing = {row[1] for row in await (await db.execute(f"PRAGMA table_info({table})")).fetchall()}
            if column not in existing:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

        await ensure_events_schema(db)

        await db.commit()

async def startup_schema_check(db_path: str) -> Dict[str, Any]:
    """Fail startup clearly when an additive migration was not applied."""
    required = {
        "users": {"user_id", "stage", "profile_json", "row_revision"},
        "flow_states": {"user_id", "current_step", "revision"},
        "behavioral_experiments": {"id", "user_id", "skill_id", "status"},
        "experiment_outcomes": {"experiment_id", "action_started", "success_criterion_met"},
        "user_skill_effectiveness": {"user_id", "skill_id", "migration_confidence"},
        "skill_mastery": {"user_id", "skill_id", "status", "migration_confidence"},
        "skill_mastery_events": {"experiment_id", "event_type"},
        "legacy_migration_links": {"source_table", "source_id", "target_id"},
        "behavioral_analytics_events": {"event_name", "policy_version", "ranking_version", "skill_version", "synced"},
        "behavioral_experiment_decisions": {"experiment_id", "decision", "reason_code", "policy_version", "ranking_version", "skill_version"},
        "schema_migrations": {"version", "applied_at"},
    }
    errors: list[str] = []
    async with aiosqlite.connect(db_path) as db:
        tables = {row[0] for row in await (await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )).fetchall()}
        for table, columns in required.items():
            if table not in tables:
                errors.append(f"missing table: {table}")
                continue
            present = {row[1] for row in await (await db.execute(f"PRAGMA table_info({table})")).fetchall()}
            for column in sorted(columns - present):
                errors.append(f"missing column: {table}.{column}")
        migration = await (await db.execute(
            "SELECT 1 FROM schema_migrations WHERE version=?", (USER_STATE_SCHEMA_VERSION,)
        )).fetchone() if "schema_migrations" in tables else None
        if not migration:
            errors.append(f"missing schema migration: {USER_STATE_SCHEMA_VERSION}")
    if errors:
        raise RuntimeError("STARTUP_SCHEMA_CHECK_FAILED: " + "; ".join(errors))
    return {"ok": True, "schema_version": USER_STATE_SCHEMA_VERSION, "checked_tables": len(required)}


def _legacy_strategy_ids(profile_raw: Any) -> List[str]:
    profile = _safe_json_dict(profile_raw)
    values: List[str] = []
    for key in ("working_strategies", "successful_skills"):
        for item in _as_list(profile.get(key)):
            value = str(item or "").strip()
            if value and value not in values:
                values.append(value)
    for key in ("best_skill", "last_successful_skill"):
        value = str(profile.get(key) or "").strip()
        if value and value not in values:
            values.append(value)
    return values[:50]


async def migrate_legacy_data(db_path: str) -> Dict[str, int]:
    """Idempotently adapt legacy state with low-confidence, non-mastered facts."""
    counts = {"flow_states": 0, "attempts": 0, "strategies": 0}
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("BEGIN IMMEDIATE")
        try:
            users = await (await db.execute(
                "SELECT user_id,stage,current_step,profile_json,trainer_key FROM users"
            )).fetchall()
            for user in users:
                current_step = str(user["current_step"] or user["stage"] or "onboarding")
                cur = await db.execute(
                    """INSERT OR IGNORE INTO flow_states(user_id,current_step,revision,updated_at)
                       VALUES(?,?,0,?)""",
                    (user["user_id"], current_step, _utc_iso()),
                )
                counts["flow_states"] += max(0, int(cur.rowcount))

                for skill_id in _legacy_strategy_ids(user["profile_json"]):
                    source_id = f"{user['user_id']}:{skill_id}"
                    already = await (await db.execute(
                        """SELECT 1 FROM legacy_migration_links
                           WHERE source_table='users.profile_json' AND source_id=? AND target_type='skill_effectiveness'""",
                        (source_id,),
                    )).fetchone()
                    if already:
                        continue
                    now = _utc_iso()
                    await db.execute(
                        """INSERT OR IGNORE INTO user_skill_effectiveness
                           (user_id,skill_id,mechanism_code,context_domain,attempts_count,successes_count,
                            independent_successes,worse_count,last_used_at,effectiveness_band,
                            preferred_difficulty,preferred_trainer_style,migration_confidence,evidence_refs_json)
                           VALUES(?,?, 'unclear_next_action','other',0,0,0,0,?,'unknown',NULL,?,'low',?)""",
                        (user["user_id"], skill_id, now, user["trainer_key"],
                         json.dumps([f"legacy_profile:{user['user_id']}"])),
                    )
                    await db.execute(
                        """INSERT OR IGNORE INTO skill_mastery
                           (user_id,skill_id,status,current_difficulty,successful_practice_count,
                            independent_use_count,generalized_contexts_json,failed_contexts_json,
                            scaffolding_level,last_used_at,regression_flag,migration_confidence,version)
                           VALUES(?,?,'NEW',1,0,0,'[]','[]','full',?,0,'low',1)""",
                        (user["user_id"], skill_id, now),
                    )
                    await db.execute(
                        """INSERT INTO legacy_migration_links
                           (source_table,source_id,target_type,target_id,migrated_at)
                           VALUES('users.profile_json',?,'skill_effectiveness',0,?)""",
                        (source_id, now),
                    )
                    counts["strategies"] += 1

            attempts = await (await db.execute("SELECT * FROM skill_attempts ORDER BY attempt_id")).fetchall()
            for attempt in attempts:
                source_id = str(attempt["attempt_id"])
                already = await (await db.execute(
                    """SELECT 1 FROM legacy_migration_links
                       WHERE source_table='skill_attempts' AND source_id=? AND target_type='behavioral_experiment'""",
                    (source_id,),
                )).fetchone()
                if already:
                    continue
                now = str(attempt["created_at"] or _utc_iso())
                task_ref = str(attempt["task_id"] or "legacy task")[:120]
                situation = await db.execute(
                    """INSERT INTO situation_snapshots
                       (user_id,created_at,task_summary,desired_action,context_domain,action_phase,
                        emotion_intensity_0_100,energy_0_100,urgency,raw_text_ref)
                       VALUES(?,?,?,?, 'other','start',50,50,'unknown',NULL)""",
                    (attempt["user_id"], now, task_ref, "проверить короткий первый шаг"),
                )
                situation_id = int(situation.lastrowid)
                hypothesis = await db.execute(
                    """INSERT INTO mechanism_hypotheses
                       (situation_id,mechanism_code,confidence,evidence_json,unknowns_json,
                        disconfirming_questions_json,source,confirmed_by_user)
                       VALUES(?,'unclear_next_action','low',?,'[]','[]','rules',0)""",
                    (situation_id, json.dumps([f"legacy_attempt:{source_id}"])),
                )
                result = str(attempt["result"] or "").lower()
                success = result in {"done", "completed", "success", "helped"}
                partial = result in {"partial", "started", "tried"}
                experiment = await db.execute(
                    """INSERT INTO behavioral_experiments
                       (user_id,situation_id,mechanism_hypothesis_id,skill_id,mechanism_code,
                        context_domain,difficulty_level,instruction_variant,target_action,success_criterion,
                        started_at,completed_at,status,parent_experiment_id,progression_type,
                        decision_reason_code,trainer_style,state_revision)
                       VALUES(?,?,?,?, 'unclear_next_action','other',1,?,?,?, ?,?,'completed',NULL,
                              'first','LEGACY_MIGRATION',?,0)""",
                    (attempt["user_id"], situation_id, int(hypothesis.lastrowid),
                     str(attempt["skill_id"] or "legacy_skill"), "Legacy skill attempt",
                     "проверить действие", "зафиксирован результат", now, now, "marsha"),
                )
                experiment_id = int(experiment.lastrowid)
                failure = None if success else ("unknown" if not attempt["barrier"] else "external_blocker")
                await db.execute(
                    """INSERT INTO experiment_outcomes
                       (experiment_id,action_started,action_persisted,emotional_change,
                        before_intensity_0_100,after_intensity_0_100,success_criterion_met,
                        independent_use,user_note_short,failure_reason_code,captured_at)
                       VALUES(?,?,?,?,NULL,NULL,?,0,NULL,?,?)""",
                    (experiment_id, "yes" if success else "partial" if partial else "no",
                     "yes" if success else "partial" if partial else "not_applicable", "unknown",
                     int(success), failure, now),
                )
                await db.execute(
                    """INSERT INTO legacy_migration_links
                       (source_table,source_id,target_type,target_id,migrated_at)
                       VALUES('skill_attempts',?,'behavioral_experiment',?,?)""",
                    (source_id, experiment_id, _utc_iso()),
                )
                counts["attempts"] += 1
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return counts


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
        is_internal_test = int(user_id in INTERNAL_TEST_USER_IDS)
        if is_internal_test:
            clean_data["is_internal_test"] = True
        dedupe_key = str(clean_data.get("dedupe_key") or clean_data.get("analytics_key") or "")
        analytics_event = 0 if is_internal_test else int(clean_data.get("analytics_event", True) is not False)
        if event_name in PRODUCT_ONCE_EVENTS:
            if not dedupe_key:
                dedupe_key = f"product_once:{user_id}:{event_name}"
            clean_data["product_metric_once"] = True

        event_data_s = json.dumps(clean_data, ensure_ascii=False)
        ts = time.time()
        created_at = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(db_path) as db:
            await ensure_events_schema(db)
            if dedupe_key:
                cur = await db.execute(
                    "SELECT 1 FROM events WHERE user_id=? AND dedupe_key=? LIMIT 1",
                    (user_id, dedupe_key),
                )
                if await cur.fetchone():
                    analytics_event = 0
                    clean_data["duplicate_product_metric"] = True
                    event_data_s = json.dumps(clean_data, ensure_ascii=False)
            await db.execute(
                """
                INSERT INTO events(
                    user_id, event_name, event_data, stage, created_at,
                    synced, sync_attempts, last_sync_error, ts, event, meta,
                    is_internal_test, analytics_event, dedupe_key
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    is_internal_test,
                    analytics_event,
                    dedupe_key or None,
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
    existing_status = ""
    async with aiosqlite.connect(db_path) as db:
        if existing:
            cur = await db.execute("SELECT status FROM user_days WHERE day_id=? AND user_id=?", (existing, u["user_id"]))
            row = await cur.fetchone()
            if row and row[0] == "active":
                return str(existing)
            if row:
                existing_status = str(row[0] or "")
        base_day_id = f"{u['user_id']}:{day_number}"
        day_id = base_day_id
        if existing_status and existing_status != "active":
            safe_date = str(calendar_date or _utc_iso()[:10])[:10]
            day_id = f"{base_day_id}:{safe_date}"
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
        attempt_metadata = {"result": result, "barrier": barrier, "dedupe_key": f"product_once:{u['user_id']}:attempt_started"}
        if u["user_id"] in INTERNAL_TEST_USER_IDS:
            attempt_metadata["is_internal_test"] = True
            attempt_metadata["analytics_event"] = False
        await db.execute(
            "INSERT INTO action_events (user_id, day_id, attempt_id, event_type, skill_id, task_id, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (u["user_id"], day_id, attempt_id, "attempt_started", skill_id, task_id, json.dumps(attempt_metadata, ensure_ascii=False), _utc_iso()),
        )
        await db.commit()
        return attempt_id


async def create_situation_snapshot(db_path: str, snapshot: "SituationSnapshot") -> int:
    """Persist only the concise snapshot; raw text is referenced, never copied."""
    from core.mechanism_model import SituationSnapshot
    if not isinstance(snapshot, SituationSnapshot):
        raise TypeError("snapshot must be SituationSnapshot")
    async with aiosqlite.connect(db_path) as db:
        await _ensure_flow_and_mechanism_schema(db)
        cur = await db.execute(
            """INSERT INTO situation_snapshots
               (user_id,created_at,task_summary,desired_action,context_domain,action_phase,
                emotion_intensity_0_100,energy_0_100,urgency,raw_text_ref)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (snapshot.user_id, snapshot.created_at or _utc_iso(), snapshot.task_summary[:240],
             snapshot.desired_action[:240], snapshot.context_domain, snapshot.action_phase,
             snapshot.emotion_intensity_0_100, snapshot.energy_0_100, snapshot.urgency,
             snapshot.raw_text_ref),
        )
        situation_id = int(cur.lastrowid)
        from core.behavioral_analytics import BehavioralAnalyticsEvent
        await _insert_behavioral_analytics(db, BehavioralAnalyticsEvent(
            "situation_captured", snapshot.user_id, situation_id=situation_id,
            context_domain=snapshot.context_domain,
        ), created_at=snapshot.created_at or _utc_iso())
        await db.commit()
        return situation_id


async def create_mechanism_hypothesis(db_path: str, hypothesis: "MechanismHypothesis") -> int:
    from core.mechanism_model import MechanismHypothesis
    if not isinstance(hypothesis, MechanismHypothesis):
        raise TypeError("hypothesis must be MechanismHypothesis")
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            """INSERT INTO mechanism_hypotheses
               (situation_id,mechanism_code,confidence,evidence_json,unknowns_json,
                disconfirming_questions_json,source,confirmed_by_user) VALUES(?,?,?,?,?,?,?,?)""",
            (hypothesis.situation_id, hypothesis.mechanism_code, hypothesis.confidence,
             json.dumps(hypothesis.evidence, ensure_ascii=False),
             json.dumps(hypothesis.unknowns, ensure_ascii=False),
             json.dumps(hypothesis.disconfirming_questions, ensure_ascii=False),
             hypothesis.source, int(hypothesis.confirmed_by_user)),
        )
        hypothesis_id = int(cur.lastrowid)
        if hypothesis.confirmed_by_user or hypothesis.source == "user_confirmed":
            from core.behavioral_analytics import BehavioralAnalyticsEvent
            owner = await (await db.execute(
                "SELECT user_id,context_domain FROM situation_snapshots WHERE id=?", (hypothesis.situation_id,),
            )).fetchone()
            if owner:
                await _insert_behavioral_analytics(db, BehavioralAnalyticsEvent(
                    "mechanism_confirmed", int(owner[0]), situation_id=hypothesis.situation_id,
                    mechanism_code=hypothesis.mechanism_code, context_domain=str(owner[1]),
                ))
        await db.commit()
        return hypothesis_id


async def create_experiment(
    db_path: str, *, user_id: int, situation_id: int,
    mechanism_hypothesis_id: int, skill_id: str,
) -> int:
    """Create a clean experiment linked to its situation and working mechanism."""
    if not all((user_id, situation_id, mechanism_hypothesis_id, skill_id)):
        raise ValueError("Every experiment requires user, situation, mechanism, and skill")
    now = _utc_iso()
    async with aiosqlite.connect(db_path) as db:
        # A new row starts with NULL outcome by construction; prior outcomes
        # can therefore never leak into a new experiment.
        cur = await db.execute(
            """INSERT INTO experiments
               (user_id,situation_id,mechanism_hypothesis_id,skill_id,status,outcome,created_at,updated_at)
               VALUES(?,?,?,?, 'ready', NULL, ?, ?)""",
            (user_id, situation_id, mechanism_hypothesis_id, skill_id, now, now),
        )
        await db.commit()
        return int(cur.lastrowid)


async def create_behavioral_experiment(
    db_path: str, experiment: "BehavioralExperiment", *, mechanism_hypothesis_id: int,
) -> int:
    """Create a new, linked record; pending fields are never reused."""
    from core.experiment_core import BehavioralExperiment
    from core.mechanism_model import MECHANISM_CODES
    if not isinstance(experiment, BehavioralExperiment):
        raise TypeError("experiment must be BehavioralExperiment")
    if experiment.mechanism_code not in MECHANISM_CODES:
        raise ValueError("Experiment requires an MVP behavioral mechanism")
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        link = await (await db.execute(
            """SELECT s.user_id,s.context_domain,h.mechanism_code
               FROM situation_snapshots s JOIN mechanism_hypotheses h ON h.situation_id=s.id
               WHERE s.id=? AND h.id=?""",
            (experiment.situation_id, mechanism_hypothesis_id),
        )).fetchone()
        if not link or int(link["user_id"]) != experiment.user_id:
            raise ValueError("Situation and mechanism must belong to the experiment user")
        if link["mechanism_code"] != experiment.mechanism_code or link["context_domain"] != experiment.context_domain:
            raise ValueError("Experiment must preserve its situation and mechanism chain")
        try:
            cur = await db.execute(
                """INSERT INTO behavioral_experiments
                   (user_id,situation_id,mechanism_hypothesis_id,skill_id,mechanism_code,context_domain,
                    difficulty_level,instruction_variant,target_action,success_criterion,started_at,
                    completed_at,status,parent_experiment_id,progression_type,decision_reason_code,
                    trainer_style,state_revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (experiment.user_id, experiment.situation_id, mechanism_hypothesis_id,
                 experiment.skill_id, experiment.mechanism_code, experiment.context_domain,
                 experiment.difficulty_level, experiment.instruction_variant,
                 experiment.target_action, experiment.success_criterion, experiment.started_at,
                 experiment.completed_at, experiment.status, experiment.parent_experiment_id,
                 experiment.progression_type, experiment.decision_reason_code,
                 experiment.trainer_style, experiment.state_revision),
            )
            experiment_id = int(cur.lastrowid)
            from core.behavioral_analytics import BehavioralAnalyticsEvent
            await _insert_behavioral_analytics(db, BehavioralAnalyticsEvent(
                "experiment_proposed", experiment.user_id, situation_id=experiment.situation_id,
                experiment_id=experiment_id, skill_id=experiment.skill_id,
                mechanism_code=experiment.mechanism_code, context_domain=experiment.context_domain,
            ))
            await db.commit()
        except aiosqlite.IntegrityError as exc:
            raise ValueError("User already has an active productive experiment or the chain is invalid") from exc
        return experiment_id


async def transition_behavioral_experiment(
    db_path: str, experiment_id: int, *, status: str, expected_revision: int,
) -> int:
    """Revisioned experiment lifecycle transition with legal status edges."""
    edges = {
        "proposed": {"accepted", "abandoned", "safety_stopped"},
        "accepted": {"started", "abandoned", "safety_stopped"},
        "started": {"completed", "abandoned", "safety_stopped"},
    }
    async with aiosqlite.connect(db_path) as db:
        row = await (await db.execute(
            """SELECT status,state_revision,user_id,situation_id,skill_id,mechanism_code,context_domain
               FROM behavioral_experiments WHERE id=?""", (experiment_id,)
        )).fetchone()
        if not row or int(row[1]) != expected_revision:
            raise ValueError("STALE_EXPERIMENT")
        if status not in edges.get(str(row[0]), set()):
            raise ValueError("INVALID_EXPERIMENT_TRANSITION")
        now = _utc_iso()
        cur = await db.execute(
            """UPDATE behavioral_experiments SET status=?,state_revision=state_revision+1,
               started_at=CASE WHEN ?='started' THEN COALESCE(started_at,?) ELSE started_at END,
               completed_at=CASE WHEN ? IN ('completed','abandoned','safety_stopped') THEN ? ELSE completed_at END
               WHERE id=? AND state_revision=?""",
            (status, status, now, status, now, experiment_id, expected_revision),
        )
        if cur.rowcount != 1:
            raise ValueError("STALE_EXPERIMENT")
        if status in {"started", "completed"}:
            from core.behavioral_analytics import BehavioralAnalyticsEvent
            await _insert_behavioral_analytics(db, BehavioralAnalyticsEvent(
                f"experiment_{status}", int(row[2]), situation_id=int(row[3]),
                experiment_id=experiment_id, skill_id=str(row[4]), mechanism_code=str(row[5]),
                context_domain=str(row[6]),
            ), created_at=now)
        await db.commit()
        return expected_revision + 1


async def record_behavioral_outcome_and_decision(
    db_path: str, experiment_id: int, *, criterion_met: bool,
    observed_result: str, decision: str, reason_code: str,
    next_experiment_id: int | None = None,
    policy_version: str = "post-experiment-v1", ranking_version: str = "ranking-v1",
    skill_version: str = "1.0.0",
) -> tuple[int, int]:
    """Atomically finish the trace experiment → outcome → decision."""
    if not all(value.strip() for value in (
        observed_result, decision, reason_code, policy_version, ranking_version, skill_version,
    )):
        raise ValueError("Outcome and decision must be explicit")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        row = await (await db.execute(
            "SELECT status FROM behavioral_experiments WHERE id=?", (experiment_id,)
        )).fetchone()
        if not row or row[0] not in {"completed", "safety_stopped"}:
            raise ValueError("Outcome can be recorded only for a completed or safety-stopped experiment")
        now = _utc_iso()
        outcome = await db.execute(
            """INSERT INTO behavioral_experiment_outcomes
               (experiment_id,criterion_met,observed_result,created_at) VALUES(?,?,?,?)""",
            (experiment_id, int(criterion_met), observed_result[:500], now),
        )
        decision_row = await db.execute(
            """INSERT INTO behavioral_experiment_decisions
               (experiment_id,outcome_id,decision,reason_code,policy_version,ranking_version,
                skill_version,next_experiment_id,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (experiment_id, int(outcome.lastrowid), decision, reason_code, policy_version,
             ranking_version, skill_version, next_experiment_id, now),
        )
        await db.execute(
            "UPDATE behavioral_experiments SET decision_reason_code=? WHERE id=?",
            (reason_code, experiment_id),
        )
        await db.commit()
        return int(outcome.lastrowid), int(decision_row.lastrowid)


def _effectiveness_band(attempts: int, successes: int, independent: int, worse: int) -> str:
    """Return a conservative, explainable effectiveness classification."""
    if worse >= 2:
        return "avoid"
    if independent >= 1 or successes >= 2:
        return "working"
    if successes == 1:
        return "promising"
    if attempts >= 3:
        return "unreliable"
    return "unknown"


async def _update_behavioral_memory_in_transaction(
    db: aiosqlite.Connection, outcome: "ExperimentOutcome", captured_at: str,
) -> None:
    """Project one immutable experiment result into compact durable memory."""
    db.row_factory = aiosqlite.Row
    experiment = await (await db.execute(
        """SELECT user_id,skill_id,mechanism_code,context_domain,difficulty_level,trainer_style
           FROM behavioral_experiments WHERE id=?""", (outcome.experiment_id,),
    )).fetchone()
    if not experiment:
        raise ValueError("Memory evidence experiment does not exist")
    evidence_ref = f"experiment:{outcome.experiment_id}"
    mechanism_row = await (await db.execute(
        """SELECT evidence_count,typical_barriers_json,evidence_refs_json
           FROM user_mechanism_profile
           WHERE user_id=? AND mechanism_code=? AND context_domain=?""",
        (experiment["user_id"], experiment["mechanism_code"], experiment["context_domain"]),
    )).fetchone()
    barriers = json.loads(mechanism_row["typical_barriers_json"]) if mechanism_row else []
    if outcome.failure_reason_code and outcome.failure_reason_code not in barriers:
        barriers = [*barriers, outcome.failure_reason_code][-8:]
    mechanism_refs = json.loads(mechanism_row["evidence_refs_json"]) if mechanism_row else []
    if evidence_ref not in mechanism_refs:
        mechanism_refs.append(evidence_ref)
    evidence_count = int(mechanism_row["evidence_count"] if mechanism_row else 0) + 1
    confidence = "high" if evidence_count >= 5 else "medium" if evidence_count >= 2 else "low"
    await db.execute(
        """INSERT INTO user_mechanism_profile
           (user_id,mechanism_code,context_domain,evidence_count,last_seen_at,
            typical_barriers_json,confidence,evidence_refs_json) VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id,mechanism_code,context_domain) DO UPDATE SET
            evidence_count=excluded.evidence_count,last_seen_at=excluded.last_seen_at,
            typical_barriers_json=excluded.typical_barriers_json,confidence=excluded.confidence,
            evidence_refs_json=excluded.evidence_refs_json""",
        (experiment["user_id"], experiment["mechanism_code"], experiment["context_domain"],
         evidence_count, captured_at, json.dumps(barriers, ensure_ascii=False), confidence,
         json.dumps(mechanism_refs)),
    )

    skill_row = await (await db.execute(
        """SELECT attempts_count,successes_count,independent_successes,worse_count,evidence_refs_json
           FROM user_skill_effectiveness WHERE user_id=? AND skill_id=? AND mechanism_code=? AND context_domain=?""",
        (experiment["user_id"], experiment["skill_id"], experiment["mechanism_code"], experiment["context_domain"]),
    )).fetchone()
    attempts = int(skill_row["attempts_count"] if skill_row else 0) + 1
    successes = int(skill_row["successes_count"] if skill_row else 0) + int(outcome.success_criterion_met)
    independent = int(skill_row["independent_successes"] if skill_row else 0) + int(
        outcome.success_criterion_met and outcome.independent_use
    )
    worse = int(skill_row["worse_count"] if skill_row else 0) + int(outcome.emotional_change == "worse")
    skill_refs = json.loads(skill_row["evidence_refs_json"]) if skill_row else []
    if evidence_ref not in skill_refs:
        skill_refs.append(evidence_ref)
    successful = bool(outcome.success_criterion_met)
    await db.execute(
        """INSERT INTO user_skill_effectiveness
           (user_id,skill_id,mechanism_code,context_domain,attempts_count,successes_count,
            independent_successes,worse_count,last_used_at,effectiveness_band,
            preferred_difficulty,preferred_trainer_style,evidence_refs_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id,skill_id,mechanism_code,context_domain) DO UPDATE SET
            attempts_count=excluded.attempts_count,successes_count=excluded.successes_count,
            independent_successes=excluded.independent_successes,worse_count=excluded.worse_count,
            last_used_at=excluded.last_used_at,effectiveness_band=excluded.effectiveness_band,
            preferred_difficulty=COALESCE(excluded.preferred_difficulty,user_skill_effectiveness.preferred_difficulty),
            preferred_trainer_style=COALESCE(excluded.preferred_trainer_style,user_skill_effectiveness.preferred_trainer_style),
            evidence_refs_json=excluded.evidence_refs_json""",
        (experiment["user_id"], experiment["skill_id"], experiment["mechanism_code"],
         experiment["context_domain"], attempts, successes, independent, worse, captured_at,
         _effectiveness_band(attempts, successes, independent, worse),
         experiment["difficulty_level"] if successful else None,
         experiment["trainer_style"] if successful else None, json.dumps(skill_refs)),
    )


async def get_behavioral_memory(
    db_path: str, *, user_id: int, mechanism_code: str, context_domain: str,
) -> Dict[str, Any]:
    """Return reusable barriers and successful skills for a similar situation."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        mechanism = await (await db.execute(
            """SELECT evidence_count,last_seen_at,typical_barriers_json,confidence,evidence_refs_json
               FROM user_mechanism_profile WHERE user_id=? AND mechanism_code=? AND context_domain=?""",
            (user_id, mechanism_code, context_domain),
        )).fetchone()
        skills = await (await db.execute(
            """SELECT skill_id,effectiveness_band,attempts_count,successes_count,independent_successes,
                      preferred_difficulty,preferred_trainer_style,evidence_refs_json
               FROM user_skill_effectiveness
               WHERE user_id=? AND mechanism_code=? AND context_domain=?
                 AND effectiveness_band IN ('working','promising')
               ORDER BY CASE effectiveness_band WHEN 'working' THEN 0 ELSE 1 END,
                        independent_successes DESC,successes_count DESC""",
            (user_id, mechanism_code, context_domain),
        )).fetchall()
    return {
        "mechanism_code": mechanism_code,
        "context_domain": context_domain,
        "barriers": json.loads(mechanism["typical_barriers_json"]) if mechanism else [],
        "mechanism_evidence_refs": json.loads(mechanism["evidence_refs_json"]) if mechanism else [],
        "working_skills": [dict(row) | {"evidence_refs": json.loads(row["evidence_refs_json"])} for row in skills],
    }


async def get_skill_map_records(db_path: str, *, user_id: int) -> List[Dict[str, Any]]:
    """Return structured evidence for the user-facing working-skills map."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT e.skill_id,e.mechanism_code,e.context_domain,e.attempts_count,
                      e.successes_count,e.independent_successes,e.worse_count,e.last_used_at,
                      e.effectiveness_band,e.preferred_difficulty,e.preferred_trainer_style,
                      e.evidence_refs_json,COALESCE(p.recommendation_disabled,0) AS recommendation_disabled
               FROM user_skill_effectiveness e
               LEFT JOIN user_skill_preferences p ON p.user_id=e.user_id AND p.skill_id=e.skill_id
               WHERE e.user_id=? ORDER BY e.last_used_at DESC,e.skill_id""",
            (user_id,),
        )).fetchall()
    return [dict(row) | {"evidence_refs": json.loads(row["evidence_refs_json"])} for row in rows]


async def set_skill_recommendation_disabled(
    db_path: str, *, user_id: int, skill_id: str, disabled: bool, correction_id: str,
) -> None:
    """Apply the user's explicit recommendation preference without deleting evidence."""
    if not skill_id.strip() or not correction_id.strip():
        raise ValueError("skill_id and explicit correction reference are required")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO user_skill_preferences
               (user_id,skill_id,recommendation_disabled,correction_ref,updated_at) VALUES(?,?,?,?,?)
               ON CONFLICT(user_id,skill_id) DO UPDATE SET
               recommendation_disabled=excluded.recommendation_disabled,
               correction_ref=excluded.correction_ref,updated_at=excluded.updated_at""",
            (user_id, skill_id, int(disabled), f"user_correction:{correction_id}", _utc_iso()),
        )
        await db.commit()


async def get_disabled_skill_ids(db_path: str, *, user_id: int) -> frozenset[str]:
    async with aiosqlite.connect(db_path) as db:
        rows = await (await db.execute(
            "SELECT skill_id FROM user_skill_preferences WHERE user_id=? AND recommendation_disabled=1",
            (user_id,),
        )).fetchall()
    return frozenset(str(row[0]) for row in rows)


async def get_experiment_journal_records(
    db_path: str, *, user_id: int, root_experiment_id: int | None = None,
) -> List[Dict[str, Any]]:
    """Reconstruct the journal only from normalized experiment-owned tables."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        params: list[Any] = [user_id]
        chain_filter = ""
        if root_experiment_id is not None:
            chain_filter = """AND b.id IN (
                WITH RECURSIVE chain(id) AS (
                    SELECT id FROM behavioral_experiments WHERE id=? AND user_id=?
                    UNION ALL
                    SELECT child.id FROM behavioral_experiments child JOIN chain ON child.parent_experiment_id=chain.id
                ) SELECT id FROM chain
            )"""
            params.extend((root_experiment_id, user_id))
        rows = await (await db.execute(
            f"""SELECT b.id AS experiment_id,b.parent_experiment_id,b.progression_type,b.skill_id,
                       b.mechanism_code,b.context_domain,b.difficulty_level,b.instruction_variant,
                       b.target_action,b.success_criterion,b.status,b.started_at,b.completed_at,
                       s.task_summary,s.desired_action,h.confidence AS mechanism_confidence,
                       o.action_started,o.action_persisted,o.emotional_change,o.success_criterion_met,
                       o.independent_use,o.failure_reason_code,o.user_note_short,o.captured_at,
                       d.decision AS next_action,d.reason_code AS decision_reason_code,
                       d.next_experiment_id
                FROM behavioral_experiments b
                JOIN situation_snapshots s ON s.id=b.situation_id
                JOIN mechanism_hypotheses h ON h.id=b.mechanism_hypothesis_id
                LEFT JOIN experiment_outcomes o ON o.experiment_id=b.id
                LEFT JOIN behavioral_experiment_decisions d ON d.id=(
                    SELECT d2.id FROM behavioral_experiment_decisions d2
                    WHERE d2.experiment_id=b.id ORDER BY d2.id DESC LIMIT 1
                )
                WHERE b.user_id=? {chain_filter}
                ORDER BY COALESCE(b.started_at,b.completed_at,''),b.id""",
            params,
        )).fetchall()
    return [dict(row) for row in rows]


async def get_value_proof_metrics(db_path: str, *, user_id: int) -> Dict[str, Any]:
    """Measure offer value from normalized experiments, never calendar age alone."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """SELECT COUNT(o.experiment_id) AS completed_experiments,
                      COALESCE(SUM(CASE WHEN o.success_criterion_met=1 OR o.action_started='partial'
                                        THEN 1 ELSE 0 END),0) AS successful_or_partial
               FROM behavioral_experiments b
               JOIN experiment_outcomes o ON o.experiment_id=b.id
               WHERE b.user_id=? AND b.status='completed'""",
            (user_id,),
        )).fetchone()
        latest = await (await db.execute(
            """SELECT b.instruction_variant,b.skill_id,b.mechanism_code,o.action_started,
                      o.emotional_change,o.success_criterion_met,o.failure_reason_code
               FROM behavioral_experiments b JOIN experiment_outcomes o ON o.experiment_id=b.id
               WHERE b.user_id=? AND b.status='completed'
               ORDER BY o.captured_at DESC LIMIT 1""",
            (user_id,),
        )).fetchone()
    return {
        "completed_experiments": int(row["completed_experiments"] if row else 0),
        "successful_or_partial": int(row["successful_or_partial"] if row else 0),
        "latest_experiment": dict(latest) if latest else {},
    }


async def record_skill_mastery_transition(
    db_path: str, *, user_id: int, skill_id: str, experiment_id: int,
    from_status: str, to_status: str, reason_code: str,
) -> int:
    """Persist mastery history separately while requiring experiment evidence."""
    allowed = {"NEW", "LEARNING", "PRACTICING", "MASTERED", "GENERALIZING"}
    if from_status not in allowed or to_status not in allowed or not reason_code.strip():
        raise ValueError("Valid mastery statuses and reason_code are required")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        evidence = await (await db.execute(
            """SELECT situation_id,mechanism_code,context_domain FROM behavioral_experiments
               WHERE id=? AND user_id=? AND skill_id=?""",
            (experiment_id, user_id, skill_id),
        )).fetchone()
        if not evidence:
            raise ValueError("Mastery transition requires a matching experiment")
        cur = await db.execute(
            """INSERT INTO skill_mastery_history
               (user_id,skill_id,experiment_id,from_status,to_status,reason_code,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (user_id, skill_id, experiment_id, from_status, to_status, reason_code, _utc_iso()),
        )
        await db.commit()
        return int(cur.lastrowid)


async def get_skill_mastery_history(
    db_path: str, *, user_id: int, skill_id: str | None = None,
) -> List[Dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        query = "SELECT * FROM skill_mastery_history WHERE user_id=?"
        params: list[Any] = [user_id]
        if skill_id is not None:
            query += " AND skill_id=?"
            params.append(skill_id)
        query += " ORDER BY created_at,id"
        rows = await (await db.execute(query, params)).fetchall()
    return [dict(row) for row in rows]


async def apply_skill_mastery_signal(
    db_path: str, *, user_id: int, skill_id: str, signal: "LearningSignal",
    criteria: "LearningCriteria", initial_difficulty: int = 1,
) -> "LearningUpdate":
    """Atomically update mastery and append experiment-linked objective events."""
    from core.learning_engine import (
        LearningCriteria, LearningSignal, SkillMasteryState, apply_learning_signal, initial_mastery,
    )
    if not isinstance(signal, LearningSignal) or not isinstance(criteria, LearningCriteria):
        raise TypeError("signal and criteria must be Learning Engine values")
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("BEGIN IMMEDIATE")
        evidence = await (await db.execute(
            """SELECT situation_id,mechanism_code,context_domain FROM behavioral_experiments
               WHERE id=? AND user_id=? AND skill_id=?""",
            (signal.experiment_id, user_id, skill_id),
        )).fetchone()
        if not evidence:
            await db.rollback()
            raise ValueError("Mastery signal requires a matching experiment")
        row = await (await db.execute(
            "SELECT * FROM skill_mastery WHERE user_id=? AND skill_id=?", (user_id, skill_id),
        )).fetchone()
        if row:
            state = SkillMasteryState(
                user_id, skill_id, row["status"], int(row["current_difficulty"]),
                int(row["successful_practice_count"]), int(row["independent_use_count"]),
                tuple(json.loads(row["generalized_contexts_json"])),
                tuple(json.loads(row["failed_contexts_json"])), row["scaffolding_level"],
                row["last_used_at"], bool(row["regression_flag"]), int(row["version"]),
            )
        else:
            state = initial_mastery(user_id, skill_id, difficulty=initial_difficulty)
        update = apply_learning_signal(state, signal, criteria)
        value = update.state
        try:
            await db.execute(
                """INSERT INTO skill_mastery
                   (user_id,skill_id,status,current_difficulty,successful_practice_count,
                    independent_use_count,generalized_contexts_json,failed_contexts_json,
                    scaffolding_level,last_used_at,regression_flag,version)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id,skill_id) DO UPDATE SET
                    status=excluded.status,current_difficulty=excluded.current_difficulty,
                    successful_practice_count=excluded.successful_practice_count,
                    independent_use_count=excluded.independent_use_count,
                    generalized_contexts_json=excluded.generalized_contexts_json,
                    failed_contexts_json=excluded.failed_contexts_json,
                    scaffolding_level=excluded.scaffolding_level,last_used_at=excluded.last_used_at,
                    regression_flag=excluded.regression_flag,version=excluded.version""",
                (user_id, skill_id, value.status, value.current_difficulty,
                 value.successful_practice_count, value.independent_use_count,
                 json.dumps(value.generalized_contexts), json.dumps(value.failed_contexts),
                 value.scaffolding_level, value.last_used_at or _utc_iso(),
                 int(value.regression_flag), value.version),
            )
            now = signal.occurred_at or _utc_iso()
            for event in update.events:
                await db.execute(
                    """INSERT INTO skill_mastery_events
                       (user_id,skill_id,experiment_id,event_type,from_status,to_status,context_domain,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (user_id, skill_id, event.experiment_id, event.event_type,
                     event.from_status, event.to_status, event.context_domain, now),
                )
                analytics_name = {
                    "difficulty_up": "skill_advanced", "transfer": "skill_transferred",
                    "mastered": "skill_mastered", "regression": "skill_regressed",
                }.get(event.event_type)
                if analytics_name:
                    from core.behavioral_analytics import BehavioralAnalyticsEvent
                    await _insert_behavioral_analytics(db, BehavioralAnalyticsEvent(
                        analytics_name, user_id, situation_id=int(evidence["situation_id"]),
                        experiment_id=event.experiment_id, skill_id=skill_id,
                        mechanism_code=str(evidence["mechanism_code"]),
                        context_domain=event.context_domain,
                    ), created_at=now)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        return update


async def get_skill_mastery(db_path: str, *, user_id: int, skill_id: str) -> Optional[Dict[str, Any]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM skill_mastery WHERE user_id=? AND skill_id=?", (user_id, skill_id),
        )).fetchone()
    if not row:
        return None
    result = dict(row)
    result["generalized_contexts"] = json.loads(result.pop("generalized_contexts_json"))
    result["failed_contexts"] = json.loads(result.pop("failed_contexts_json"))
    return result


async def correct_behavioral_pattern(
    db_path: str, *, user_id: int, pattern_code: str, summary: str,
    correction_id: str, delete: bool = False,
) -> None:
    """Apply an explicit user correction without modifying experiment history."""
    if not correction_id.strip():
        raise ValueError("An explicit user correction reference is required")
    if not delete and (not summary.strip() or len(summary) > 280):
        raise ValueError("Pattern summary must contain 1..280 characters")
    async with aiosqlite.connect(db_path) as db:
        if delete:
            await db.execute(
                "DELETE FROM behavioral_patterns WHERE user_id=? AND pattern_code=?",
                (user_id, pattern_code),
            )
        else:
            await db.execute(
                """INSERT INTO behavioral_patterns(user_id,pattern_code,summary,evidence_refs,last_updated_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(user_id,pattern_code) DO UPDATE SET
                   summary=excluded.summary,evidence_refs=excluded.evidence_refs,last_updated_at=excluded.last_updated_at""",
                (user_id, pattern_code, summary.strip(),
                 json.dumps([f"user_correction:{correction_id}"]), _utc_iso()),
            )
        await db.commit()


async def store_operational_context(
    db_path: str, *, user_id: int, raw_context: str, ttl_seconds: int = 86400,
) -> int:
    """Store short-lived raw context outside durable behavioral memory."""
    if not raw_context or not 60 <= ttl_seconds <= 7 * 86400:
        raise ValueError("Operational context TTL must be between 60 seconds and 7 days")
    created = datetime.now(timezone.utc)
    expires = datetime.fromtimestamp(created.timestamp() + ttl_seconds, timezone.utc)
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "INSERT INTO operational_raw_context(user_id,raw_context,created_at,expires_at) VALUES(?,?,?,?)",
            (user_id, raw_context, created.isoformat(), expires.isoformat()),
        )
        await db.commit()
        return int(cur.lastrowid)


async def purge_expired_operational_context(db_path: str, *, now: str | None = None) -> int:
    """Delete expired raw context without touching structured memory or experiments."""
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "DELETE FROM operational_raw_context WHERE expires_at <= ?", (now or _utc_iso(),),
        )
        await db.commit()
        return int(cur.rowcount)


async def capture_experiment_outcome(
    db_path: str, outcome: "ExperimentOutcome", *, expected_revision: int,
    expected_flow_revision: int | None = None,
) -> str:
    """Persist all outcome axes and stop productivity immediately on worsening."""
    from core.outcome_model import ExperimentOutcome, next_action_policy
    if not isinstance(outcome, ExperimentOutcome):
        raise TypeError("outcome must be ExperimentOutcome")
    terminal_status = "safety_stopped" if outcome.requires_safety_handoff else "completed"
    failure_reason = "safety_deterioration" if outcome.requires_safety_handoff else outcome.failure_reason_code
    captured_at = outcome.captured_at or _utc_iso()
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("BEGIN IMMEDIATE")
        row = await (await db.execute(
            "SELECT status,state_revision FROM behavioral_experiments WHERE id=?",
            (outcome.experiment_id,),
        )).fetchone()
        if not row or row[0] != "started" or int(row[1]) != expected_revision:
            await db.rollback()
            raise ValueError("STALE_OR_INACTIVE_EXPERIMENT")
        if outcome.requires_safety_handoff:
            if expected_flow_revision is None:
                await db.rollback()
                raise ValueError("SAFETY_HANDOFF_REQUIRES_FLOW_REVISION")
            flow = await (await db.execute(
                "SELECT current_step,revision FROM flow_states WHERE user_id=(SELECT user_id FROM behavioral_experiments WHERE id=?)",
                (outcome.experiment_id,),
            )).fetchone()
            if not flow or int(flow[1]) != expected_flow_revision:
                await db.rollback()
                raise ValueError("STALE_FLOW_STATE")
        try:
            await db.execute(
                """INSERT INTO experiment_outcomes
                   (experiment_id,action_started,action_persisted,emotional_change,
                    before_intensity_0_100,after_intensity_0_100,success_criterion_met,
                    independent_use,user_note_short,failure_reason_code,captured_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (outcome.experiment_id, outcome.action_started, outcome.action_persisted,
                 outcome.emotional_change, outcome.before_intensity_0_100,
                 outcome.after_intensity_0_100, int(outcome.success_criterion_met),
                 int(outcome.independent_use), outcome.user_note_short,
                 failure_reason, captured_at),
            )
            # Durable memory is a projection of a referenced experiment, never
            # of raw conversation text or an unverified model inference.
            await _update_behavioral_memory_in_transaction(db, outcome, captured_at)
            analytics_experiment = await (await db.execute(
                """SELECT user_id,situation_id,skill_id,mechanism_code,context_domain
                   FROM behavioral_experiments WHERE id=?""", (outcome.experiment_id,),
            )).fetchone()
            from core.behavioral_analytics import BehavioralAnalyticsEvent
            analytics_base = dict(
                user_id=int(analytics_experiment["user_id"]),
                situation_id=int(analytics_experiment["situation_id"]),
                experiment_id=outcome.experiment_id,
                skill_id=str(analytics_experiment["skill_id"]),
                mechanism_code=str(analytics_experiment["mechanism_code"]),
                context_domain=str(analytics_experiment["context_domain"]),
            )
            await _insert_behavioral_analytics(db, BehavioralAnalyticsEvent(
                "experiment_completed", outcome_label=outcome.emotional_change, **analytics_base,
            ), created_at=captured_at)
            if outcome.action_started in {"yes", "partial"}:
                await _insert_behavioral_analytics(db, BehavioralAnalyticsEvent(
                    "action_started", outcome_label=outcome.action_started, **analytics_base,
                ), created_at=captured_at)
            if outcome.action_persisted in {"yes", "partial"}:
                await _insert_behavioral_analytics(db, BehavioralAnalyticsEvent(
                    "action_persisted", outcome_label=outcome.action_persisted, **analytics_base,
                ), created_at=captured_at)
            if outcome.independent_use:
                await _insert_behavioral_analytics(db, BehavioralAnalyticsEvent(
                    "independent_use", outcome_label=outcome.action_started, **analytics_base,
                ), created_at=captured_at)
            cur = await db.execute(
                """UPDATE behavioral_experiments SET status=?,completed_at=?,state_revision=state_revision+1,
                   decision_reason_code=? WHERE id=? AND status='started' AND state_revision=?""",
                (terminal_status, captured_at,
                 "SAFETY_HANDOFF_REQUIRED" if outcome.requires_safety_handoff else "OUTCOME_CAPTURED",
                 outcome.experiment_id, expected_revision),
            )
            if cur.rowcount != 1:
                raise ValueError("STALE_OR_INACTIVE_EXPERIMENT")
            if outcome.requires_safety_handoff:
                flow_cur = await db.execute(
                    """UPDATE flow_states SET resume_step=current_step,current_step='safety_triage',
                       revision=revision+1,updated_at=CURRENT_TIMESTAMP
                       WHERE user_id=(SELECT user_id FROM behavioral_experiments WHERE id=?) AND revision=?""",
                    (outcome.experiment_id, expected_flow_revision),
                )
                if flow_cur.rowcount != 1:
                    raise ValueError("STALE_FLOW_STATE")
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    return next_action_policy(outcome)


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


async def save_current_task(u: Dict[str, Any], db_path: str, *, title: str, description: str = "", context: str = "", next_step: str = "", object_name: str = "", deadline: str = "", fear: str = "") -> str:
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
    u["current_task_name"] = title
    u["current_task_object"] = object_name or None
    u["current_deadline"] = deadline or None
    u["current_task_next_step"] = next_step or None
    u["current_task_fear"] = fear or None
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
    u["current_task_next_step"] = next_step


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
    "extra_step_after_day_closed",
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
    metadata = dict(metadata or {})
    if user_id in INTERNAL_TEST_USER_IDS:
        metadata["is_internal_test"] = True
        metadata["analytics_event"] = False
    dedupe_key = metadata.get("dedupe_key") or metadata.get("analytics_key")
    async with aiosqlite.connect(db_path) as db:
        if dedupe_key:
            pattern = f'%"dedupe_key": "{dedupe_key}"%'
            cur = await db.execute(
                "SELECT 1 FROM action_events WHERE user_id=? AND event_type=? AND metadata LIKE ? LIMIT 1",
                (user_id, event_type, pattern),
            )
            if await cur.fetchone():
                metadata["duplicate_product_metric"] = True
                metadata["analytics_event"] = False
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
    if profile.get("avoidance_reason") == "fear_of_bad_result" or profile.get("main_pattern") in {"anxiety_avoidance", "shame_self_attack", "perfectionism_start_block"}:
        _append_unique_clean(reported, ["страх ошибки и оценки"], limit=5)
    if int(profile.get("downscale_count") or 0):
        _append_unique_clean(reported, ["слишком большой первый шаг"], limit=5)

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

    bundle = [
        "Убрать телефон на 3 минуты.",
        "Открыть задачу.",
        "Написать одну плохую строку.",
    ]
    recovery = [
        "не ругать себя",
        "назвать механизм",
        "сделать один минимальный вход",
    ]
    text = (
        "🧭 Твоя рабочая карта\n\n"
        "Что ты описал — это уже видно:\n"
        f"{_map_bullets(reported, 'пока есть только общий сигнал: вход в задачу становится слишком дорогим', limit=2)}\n\n"
        "Что мы пока предполагаем:\n"
        f"{_map_bullets(hypotheses, 'пока проверяем, какой механизм сильнее всего мешает старту', limit=2)}\n\n"
        "Что уже проверили:\n"
        f"{_map_bullets(checked, 'завершённых проверок ещё нет — первый результат обновит карту', limit=2)}\n\n"
        "Что проверим следующим:\n"
        f"— {next_test}\n\n"
        "Что будем делать:\n"
        "— подбирать следующий вход по механизму, а не повторять случайный совет;\n"
        "— сохранять результат каждой попытки и менять то, что не подошло.\n\n"
        "Что надо развивать:\n"
        "— START: начинать без долгой внутренней борьбы;\n"
        "— STAY: оставаться в задаче после первого шага;\n"
        "— RETURN: возвращаться после телефона, паузы или срыва.\n\n"
        "Твоя ближайшая связка:\n"
        + "\n".join(f"{i}. {step}" for i, step in enumerate(bundle, start=1))
        + "\n\nКогда сорвался:\n"
        + "\n".join(f"— {step};" for step in recovery)
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
