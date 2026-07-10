"""Soft user reactivation layer.

This module is intentionally small and additive: it only decides whether an
existing user may receive a gentle reminder, builds the reminder keyboard/text,
and records the reactivation state. It does not own diagnostics, skills,
payments, crisis flows, or day-closing mechanics.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

DAY_NOT_STARTED = "not_started"
DAY_ACTIVE = "active"
DAY_CLOSED = "closed"

REACTIVATION_MIN_HOURS = 4
REACTIVATION_MAX_PER_DAY = 2
BOT_MESSAGE_COOLDOWN_HOURS = 2
ALLOWED_START = dt.time(9, 0)
ALLOWED_END = dt.time(21, 0)

REACTIVATION_VARIANTS = [
    (
        "v1",
        "Кажется, мы немного зависли. Это не проблема — с прокрастинацией так часто и бывает.\n"
        "Давай не начинать заново, а сделаем один маленький шаг.",
        ["▶️ Продолжить", "⚡ Навык на 2 минуты", "💤 Не сейчас"],
    ),
    (
        "v2",
        "Прокрастинируешь или просто отвлёкся?\n"
        "Можно вернуться без чувства вины. Я помогу выбрать одно короткое действие.",
        ["Продолжить с места остановки", "Дать действие попроще", "Напомнить позже"],
    ),
    (
        "v3",
        "Я здесь. Не обязательно собираться с силами на всю задачу.\n"
        "Можем потренироваться всего пару минут.",
        ["Начать короткую тренировку", "Вернуться к моей ситуации", "Сегодня не буду"],
    ),
]


def utc_iso(now: Optional[dt.datetime] = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).isoformat()


def parse_dt(value: Any) -> Optional[dt.datetime]:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return None


def user_tz(u: Dict[str, Any]) -> ZoneInfo:
    try:
        return ZoneInfo(u.get("timezone") or "Europe/Vilnius")
    except Exception:
        return ZoneInfo("Europe/Vilnius")


def local_now(u: Dict[str, Any], now: Optional[dt.datetime] = None) -> dt.datetime:
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return now.astimezone(user_tz(u))


def normalize_day_status(u: Dict[str, Any]) -> str:
    raw = str(u.get("day_status") or "").lower()
    if raw == DAY_CLOSED or int(u.get("day_closed") or u.get("today_closed") or 0) == 1:
        return DAY_CLOSED
    if raw in {DAY_ACTIVE, "open"} or int(u.get("today_started") or 0) == 1 or int(u.get("has_started_training") or 0) == 1:
        return DAY_ACTIVE
    return DAY_NOT_STARTED


def reset_daily_limit_if_needed(u: Dict[str, Any], today: str) -> None:
    if u.get("reactivation_date") != today:
        u["reactivation_date"] = today
        u["reactivation_count_today"] = 0


def mark_user_activity(u: Dict[str, Any], *, active: bool = True, now: Optional[dt.datetime] = None) -> None:
    ts = utc_iso(now)
    u["last_user_activity_at"] = ts
    u["last_active"] = time.time()
    if active and normalize_day_status(u) != DAY_CLOSED:
        u["day_status"] = DAY_ACTIVE


def mark_bot_auto_message(u: Dict[str, Any], *, now: Optional[dt.datetime] = None) -> None:
    u["last_bot_message_at"] = utc_iso(now)


def hours_since_last_activity(u: Dict[str, Any], now: Optional[dt.datetime] = None) -> Optional[float]:
    last = parse_dt(u.get("last_user_activity_at"))
    if not last:
        legacy = float(u.get("last_active") or 0)
        if legacy > 0:
            last = dt.datetime.fromtimestamp(legacy, tz=dt.timezone.utc)
    if not last:
        return None
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    return max(0.0, (current.astimezone(dt.timezone.utc) - last).total_seconds() / 3600)


def _hours_since(value: Any, now: dt.datetime) -> Optional[float]:
    parsed = parse_dt(value)
    if not parsed:
        return None
    return max(0.0, (now.astimezone(dt.timezone.utc) - parsed).total_seconds() / 3600)


def choose_variant(u: Dict[str, Any]) -> tuple[str, str, ReplyKeyboardMarkup]:
    previous = str(u.get("last_reactivation_variant") or "")
    start = int(u.get("reactivation_count_today") or 0) % len(REACTIVATION_VARIANTS)
    for offset in range(len(REACTIVATION_VARIANTS)):
        variant_id, text, buttons = REACTIVATION_VARIANTS[(start + offset) % len(REACTIVATION_VARIANTS)]
        if variant_id != previous or len(REACTIVATION_VARIANTS) == 1:
            keyboard = [[KeyboardButton(text=b)] for b in buttons]
            keyboard.append([KeyboardButton(text="🔕 Не напоминать")])
            return variant_id, text, ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)
    variant_id, text, buttons = REACTIVATION_VARIANTS[0]
    return variant_id, text, ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=b)] for b in buttons], resize_keyboard=True)


def can_send_reactivation(u: Dict[str, Any], *, now: Optional[dt.datetime] = None) -> tuple[bool, str, Dict[str, Any]]:
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    local = local_now(u, current)
    today = local.date().isoformat()
    reset_daily_limit_if_needed(u, today)
    status = normalize_day_status(u)
    meta = {"day_status": status, "hours_since_last_activity": hours_since_last_activity(u, current)}
    if int(u.get("notifications_enabled") if u.get("notifications_enabled") is not None else 1) != 1:
        return False, "notifications_disabled", meta
    if status != DAY_ACTIVE:
        return False, "day_not_active", meta
    if not (ALLOWED_START <= local.time() <= ALLOWED_END):
        return False, "outside_allowed_time", meta
    if str(u.get("safety_mode") or "none") not in {"none", "inactive"} or int(u.get("crisis_mode") or 0) == 1:
        return False, "safety_or_crisis", meta
    if str(u.get("stage") or "") in {"offer", "curator_path"} or str(u.get("payment_status") or "") in {"manual_pending", "payment_pending"}:
        return False, "payment_or_technical_flow", meta
    if int(u.get("reactivation_count_today") or 0) >= REACTIVATION_MAX_PER_DAY:
        return False, "daily_limit", meta
    inactive_hours = meta["hours_since_last_activity"]
    if inactive_hours is None or inactive_hours < REACTIVATION_MIN_HOURS:
        return False, "too_early", meta
    last_reactivation_hours = _hours_since(u.get("last_bot_reactivation_at"), current)
    if last_reactivation_hours is not None and last_reactivation_hours < REACTIVATION_MIN_HOURS:
        return False, "reactivation_cooldown", meta
    last_bot_hours = _hours_since(u.get("last_bot_message_at"), current)
    if last_bot_hours is not None and last_bot_hours < BOT_MESSAGE_COOLDOWN_HOURS:
        return False, "recent_bot_message", meta
    return True, "ok", meta


def mark_reactivation_sent(u: Dict[str, Any], variant_id: str, *, now: Optional[dt.datetime] = None) -> None:
    current = now or dt.datetime.now(dt.timezone.utc)
    today = local_now(u, current).date().isoformat()
    reset_daily_limit_if_needed(u, today)
    u["last_bot_reactivation_at"] = utc_iso(current)
    u["last_bot_message_at"] = utc_iso(current)
    u["reactivation_count_today"] = int(u.get("reactivation_count_today") or 0) + 1
    u["last_reactivation_variant"] = variant_id
