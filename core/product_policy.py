"""Executable feature gate implementing the Product Constitution."""

from __future__ import annotations

from typing import Literal, TypedDict

from core.product_config import LEARNING_ENGINE_ENABLED, RANKING_ENGINE_ENABLED

BehavioralGoal = Literal["start", "return", "persist", "recover", "master"]
BEHAVIORAL_GOALS = frozenset({"start", "return", "persist", "recover", "master"})


class FeatureDecision(TypedDict):
    allowed: bool
    behavioral_goal: str | None
    reason_code: str
    explanation: str


def _decision(allowed: bool, goal: str | None, code: str, explanation: str) -> FeatureDecision:
    return {
        "allowed": allowed,
        "behavioral_goal": goal,
        "reason_code": code,
        "explanation": explanation,
    }


def evaluate_feature(feature_spec: dict) -> FeatureDecision:
    """Decide whether a proposed user-facing feature may enter development.

    A proposal must name one constitutional behavior and a metric (or an
    explicitly measurable expected effect). Engine-dependent proposals are
    additionally protected by their rollout flags.
    """
    if not isinstance(feature_spec, dict):
        return _decision(False, None, "INVALID_FEATURE_SPEC", "Feature specification must be a dictionary.")

    raw_goal = feature_spec.get("behavioral_goal")
    goal = raw_goal.strip().lower() if isinstance(raw_goal, str) else None
    if not goal:
        return _decision(False, None, "MISSING_BEHAVIORAL_GOAL", "Choose start, return, persist, recover, or master.")
    if goal not in BEHAVIORAL_GOALS:
        return _decision(False, goal, "INVALID_BEHAVIORAL_GOAL", f"'{goal}' is not a constitutional behavioral goal.")

    metric = feature_spec.get("success_metric") or feature_spec.get("measurable_effect")
    if not isinstance(metric, str) or not metric.strip():
        return _decision(False, goal, "MISSING_MEASURABLE_EFFECT", "Define success_metric or measurable_effect for the chosen behavior.")

    required_engine = str(feature_spec.get("requires_engine") or "").strip().lower()
    if required_engine == "learning" and not LEARNING_ENGINE_ENABLED:
        return _decision(False, goal, "LEARNING_ENGINE_DISABLED", "The learning engine rollout flag is disabled.")
    if required_engine == "ranking" and not RANKING_ENGINE_ENABLED:
        return _decision(False, goal, "RANKING_ENGINE_DISABLED", "The ranking engine rollout flag is disabled.")
    if required_engine and required_engine not in {"learning", "ranking"}:
        return _decision(False, goal, "UNKNOWN_ENGINE", f"Unknown required engine: '{required_engine}'.")

    return _decision(True, goal, "ALLOWED", "The proposal has a constitutional goal and a measurable outcome.")
