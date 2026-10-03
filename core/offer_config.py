"""Release-safe commercial configuration with one source of price truth."""

from __future__ import annotations

from decimal import Decimal
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_decimal(name: str, default: str) -> Decimal:
    return Decimal(os.getenv(name, default).strip())


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)).strip())


# New launch minimum replaces the legacy fixed monthly package.
# HUMAN_SKILL_SESSION_EUR is retained as a Python alias for callers; the old
# environment variable no longer overrides the agreed minimum.
HUMAN_SKILL_SESSION_EUR = _env_decimal("PERSONAL_MONTH_FROM_EUR", "99")
GROUP_SESSION_COUNT = _env_int("GROUP_SESSION_COUNT", 8)
GROUP_PROGRAM_EUR = _env_decimal("GROUP_PROGRAM_EUR", "240")
GROUP_SESSION_EUR_MIN = GROUP_PROGRAM_EUR / GROUP_SESSION_COUNT if GROUP_SESSION_COUNT else Decimal("0")
GROUP_SESSION_EUR_MAX = GROUP_SESSION_EUR_MIN
PROGRAM_REVIEW_DAY = _env_int("PROGRAM_REVIEW_DAY", 28)
SKILLER_ACTION_ROUTER_ENABLED = _env_bool("SKILLER_ACTION_ROUTER_ENABLED", False)

if GROUP_SESSION_COUNT < 1 or GROUP_SESSION_EUR_MIN <= 0 or GROUP_SESSION_EUR_MAX < GROUP_SESSION_EUR_MIN:
    raise RuntimeError("Invalid group offer price configuration")
if GROUP_PROGRAM_EUR <= 0 or PROGRAM_REVIEW_DAY < 3:
    raise RuntimeError("Invalid launch offer configuration")
if HUMAN_SKILL_SESSION_EUR <= 0:
    raise RuntimeError("Invalid human skill-session price configuration")


def format_eur_compact(value: Decimal) -> str:
    """Render whole-euro prices without .00 and preserve real decimals."""
    if value == value.to_integral():
        return format(value.quantize(Decimal("1")), "f")
    return format(value.normalize(), "f")

