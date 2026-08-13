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


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)).strip()))
    except (TypeError, ValueError):
        return default


LEARNING_ENGINE_ENABLED = _env_bool("LEARNING_ENGINE_ENABLED", False)
RANKING_ENGINE_ENABLED = _env_bool("RANKING_ENGINE_ENABLED", False)
ACTIVE_SKILL_QUALITY_LEVEL = os.getenv("ACTIVE_SKILL_QUALITY_LEVEL", "validated").strip() or "validated"
INCLUDE_REVIEWED_SKILLS_FOR_TESTERS = _env_bool("INCLUDE_REVIEWED_SKILLS_FOR_TESTERS", False)
SKILL_LIBRARY_SOURCE_URL = os.getenv(
    "SKILL_LIBRARY_SOURCE_URL",
    "https://docs.google.com/spreadsheets/d/19A4NkJzZJj7mVCqSq5jmY5t1pqD8BrDb/edit",
).strip()
BASE_OFFER_EUR = _env_decimal("BASE_OFFER_EUR", "5.00")
OFFER_EARLIEST_DAY = _env_int("OFFER_EARLIEST_DAY", 3)
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
NEW_ARCHITECTURE_ENABLED = _env_bool("NEW_ARCHITECTURE_ENABLED", False)
NEW_ARCHITECTURE_TEST_COHORT_ENABLED = _env_bool("NEW_ARCHITECTURE_TEST_COHORT_ENABLED", True)
SKILL_REGISTRY_ENABLED = _env_bool("SKILL_REGISTRY_ENABLED", False)
SKILL_LIBRARY_PATH = os.getenv("SKILL_LIBRARY_PATH", "data/skills").strip() or "data/skills"
SKILL_LIBRARY_MANIFEST_PATH = os.getenv(
    "SKILL_LIBRARY_MANIFEST_PATH", "data/skills_manifest.json",
).strip() or "data/skills_manifest.json"
SKILL_LIBRARY_FAIL_CLOSED = _env_bool("SKILL_LIBRARY_FAIL_CLOSED", True)
SKILL_LIBRARY_ALLOWED_STATUSES = frozenset(
    value.strip() for value in os.getenv("SKILL_LIBRARY_ALLOWED_STATUSES", "production").split(",")
    if value.strip()
)
try:
    SKILL_LIBRARY_COHORT_PERCENT = min(100, max(0, int(os.getenv("SKILL_LIBRARY_COHORT_PERCENT", "0"))))
except ValueError:
    SKILL_LIBRARY_COHORT_PERCENT = 0


def _env_id_set(name: str) -> frozenset[int]:
    values = set()
    for raw in os.getenv(name, "").split(","):
        try:
            if raw.strip():
                values.add(int(raw.strip()))
        except ValueError:
            continue
    return frozenset(values)


NEW_ARCHITECTURE_COHORT_IDS = _env_id_set("NEW_ARCHITECTURE_COHORT_IDS")
ADMIN_IDS = _env_id_set("ADMIN_IDS")


def format_eur(value: Decimal = BASE_OFFER_EUR) -> str:
    """Return a stable, display-ready euro amount without a currency symbol."""
    return format(value, ".2f")


def assert_production_payment_safety(*, payment_accept_any: bool, app_env: str = APP_ENV) -> None:
    """Test payment confirmation must never become a production verifier."""
    if app_env.strip().lower() in {"production", "prod"} and payment_accept_any:
        raise RuntimeError("PAYMENT_ACCEPT_ANY is forbidden in production")


def use_new_architecture(user_id: int, *, is_test_user: bool = False) -> bool:
    """Global rollout or an explicit admin/test cohort; defaults to legacy flow."""
    if NEW_ARCHITECTURE_ENABLED:
        return True
    if not NEW_ARCHITECTURE_TEST_COHORT_ENABLED:
        return False
    return is_test_user or user_id in ADMIN_IDS or user_id in NEW_ARCHITECTURE_COHORT_IDS
