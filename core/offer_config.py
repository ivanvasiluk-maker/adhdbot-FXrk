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


HUMAN_SKILL_SESSION_EUR = _env_decimal("HUMAN_SKILL_SESSION_EUR", "39")
GROUP_SESSION_EUR_MIN = _env_decimal("GROUP_SESSION_EUR_MIN", "30")
GROUP_SESSION_EUR_MAX = _env_decimal("GROUP_SESSION_EUR_MAX", "30")
GROUP_SESSION_COUNT = _env_int("GROUP_SESSION_COUNT", 8)
SKILLER_ACTION_ROUTER_ENABLED = _env_bool("SKILLER_ACTION_ROUTER_ENABLED", False)

if GROUP_SESSION_COUNT < 1 or GROUP_SESSION_EUR_MIN <= 0 or GROUP_SESSION_EUR_MAX < GROUP_SESSION_EUR_MIN:
    raise RuntimeError("Invalid group offer price configuration")
if HUMAN_SKILL_SESSION_EUR <= 0:
    raise RuntimeError("Invalid human skill-session price configuration")


def format_eur_compact(value: Decimal) -> str:
    """Render whole-euro prices without .00 and preserve real decimals."""
    if value == value.to_integral():
        return format(value.quantize(Decimal("1")), "f")
    return format(value.normalize(), "f")
