"""Product-level configuration shared by policy and delivery code."""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation


def _env_bool(name: str, default: bool) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in {"1", "true", "yes", "on"}


def _env_decimal(name: str, default: str) -> Decimal:
    try:
        value = Decimal(os.getenv(name, default).strip())
    except (InvalidOperation, AttributeError):
        return Decimal(default)
    return value if value >= 0 else Decimal(default)


LEARNING_ENGINE_ENABLED = _env_bool("LEARNING_ENGINE_ENABLED", False)
RANKING_ENGINE_ENABLED = _env_bool("RANKING_ENGINE_ENABLED", False)
ACTIVE_SKILL_QUALITY_LEVEL = os.getenv("ACTIVE_SKILL_QUALITY_LEVEL", "validated").strip() or "validated"
BASE_OFFER_EUR = _env_decimal("BASE_OFFER_EUR", "14.98")


def format_eur(value: Decimal = BASE_OFFER_EUR) -> str:
    """Return a stable, display-ready euro amount without a currency symbol."""
    return format(value, ".2f")
