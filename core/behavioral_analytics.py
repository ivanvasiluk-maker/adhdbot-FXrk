"""PATCH-16: privacy-minimal behavioral funnel events and KPI definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

ANALYTICS_POLICY_VERSION = "behavioral-kpi-v1"
EVENT_NAMES = frozenset({
    "situation_captured", "mechanism_confirmed", "experiment_proposed", "experiment_started",
    "experiment_completed", "action_started", "action_persisted", "skill_simplified",
    "skill_replaced", "skill_advanced", "skill_transferred", "independent_use",
    "skill_mastered", "skill_regressed", "value_report_viewed", "offer_shown",
    "purchase_confirmed",
})
OUTCOME_LABELS = frozenset({"", "yes", "partial", "no", "better", "same", "worse", "unknown"})


@dataclass(frozen=True)
class BehavioralAnalyticsEvent:
    event_name: str
    user_id: int
    situation_id: int | None = None
    experiment_id: int | None = None
    skill_id: str = ""
    mechanism_code: str = ""
    context_domain: str = ""
    outcome_label: str = ""
    count_value: int = 1
    policy_version: str = ANALYTICS_POLICY_VERSION
    ranking_version: str = "ranking-v1"
    skill_version: int = 2

    def __post_init__(self) -> None:
        if self.event_name not in EVENT_NAMES:
            raise ValueError(f"Unsupported behavioral analytics event: {self.event_name}")
        if self.user_id <= 0 or self.count_value < 0:
            raise ValueError("Analytics ids/counts must be non-negative")
        if self.outcome_label not in OUTCOME_LABELS:
            raise ValueError("Only bounded outcome taxonomy is allowed")
        for name in ("skill_id", "mechanism_code", "context_domain"):
            value = getattr(self, name)
            if len(value) > 80 or any(char in value for char in "\n\r"):
                raise ValueError(f"{name} must be a short taxonomy id")
        if not self.policy_version or not self.ranking_version or self.skill_version < 1:
            raise ValueError("Policy, ranking, and skill versions are required")


def safe_sheet_payload(event: BehavioralAnalyticsEvent, *, anonymous_user_id: str) -> dict[str, Any]:
    """Return only identifiers, taxonomy, counts, timestamps added by the caller, and versions."""
    return {
        "event_name": event.event_name,
        "anonymous_user_id": anonymous_user_id,
        "situation_id": event.situation_id,
        "experiment_id": event.experiment_id,
        "skill_id": event.skill_id,
        "mechanism_code": event.mechanism_code,
        "context_domain": event.context_domain,
        "outcome_label": event.outcome_label,
        "count_value": event.count_value,
        "policy_version": event.policy_version,
        "ranking_version": event.ranking_version,
        "skill_version": event.skill_version,
    }


def rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def build_kpis(counts: Mapping[str, int | float]) -> dict[str, float | int | None]:
    """Keep action start and independent use as separate, auditable measures."""
    started = int(counts.get("started_experiments", 0))
    completed = int(counts.get("completed_experiments", 0))
    return {
        "action_start_rate": rate(int(counts.get("action_started", 0)), started),
        "situation_to_experiment_conversion": rate(
            int(counts.get("situations_with_experiment", 0)), int(counts.get("situations", 0)),
        ),
        "completion_rate": rate(completed, started),
        "repeat_successful_use_rate": rate(
            int(counts.get("successful_repeats", 0)), int(counts.get("repeat_experiments", 0)),
        ),
        "d3_value_proof_eligibility_rate": rate(
            int(counts.get("d3_value_proof_eligible", 0)), int(counts.get("d3_users", 0)),
        ),
        "worse_rate": rate(int(counts.get("worse_outcomes", 0)), completed),
        "independent_use_rate": rate(int(counts.get("independent_uses", 0)), completed),
        "transfer_rate": rate(int(counts.get("transfers", 0)), completed),
        "value_report_to_offer_rate": rate(
            int(counts.get("offers", 0)), int(counts.get("value_reports", 0)),
        ),
        "offer_to_verified_purchase_rate": rate(
            int(counts.get("verified_purchases", 0)), int(counts.get("offers", 0)),
        ),
        "time_to_practicing_seconds": counts.get("time_to_practicing_seconds"),
        "time_to_mastered_seconds": counts.get("time_to_mastered_seconds"),
    }
