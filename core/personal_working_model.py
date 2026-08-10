"""Evidence-counted, user-visible working model.

The model contains short structured labels only.  It deliberately does not copy
conversation transcripts into durable profile storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(frozen=True)
class PersonalWorkingModel:
    recurring_barriers: dict[str, int] = field(default_factory=dict)
    successful_skills: dict[str, int] = field(default_factory=dict)
    failed_skills: dict[str, int] = field(default_factory=dict)
    effective_step_size: str = ""
    common_contexts: dict[str, int] = field(default_factory=dict)
    helpful_interventions: dict[str, int] = field(default_factory=dict)
    unhelpful_interventions: dict[str, int] = field(default_factory=dict)
    confidence: str = "hypothesis"
    evidence_count: int = 0
    last_updated: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "recurring_barriers": self.recurring_barriers,
            "successful_skills": self.successful_skills,
            "failed_skills": self.failed_skills,
            "effective_step_size": self.effective_step_size,
            "common_contexts": self.common_contexts,
            "helpful_interventions": self.helpful_interventions,
            "unhelpful_interventions": self.unhelpful_interventions,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "last_updated": self.last_updated,
        }


def update_working_model(
    previous: Mapping[str, Any] | None,
    *,
    barrier: str,
    skill_title: str,
    context: str,
    successful: bool,
    evidence_ref: str,
    step_size: str = "",
) -> PersonalWorkingModel:
    """Add one normalized observation; an evidence reference is mandatory."""
    if not str(evidence_ref or "").strip():
        raise ValueError("PersonalWorkingModel updates require evidence_ref")
    old = dict(previous or {})
    barriers = _counts(old.get("recurring_barriers"))
    successes = _counts(old.get("successful_skills"))
    failures = _counts(old.get("failed_skills"))
    contexts = _counts(old.get("common_contexts"))
    helpful = _counts(old.get("helpful_interventions"))
    unhelpful = _counts(old.get("unhelpful_interventions"))
    _bump(barriers, barrier)
    _bump(contexts, context)
    if successful:
        _bump(successes, skill_title)
        _bump(helpful, skill_title)
    else:
        _bump(failures, skill_title)
        _bump(unhelpful, skill_title)
    count = int(old.get("evidence_count") or 0) + 1
    return PersonalWorkingModel(
        barriers, successes, failures, step_size or str(old.get("effective_step_size") or ""),
        contexts, helpful, unhelpful, _confidence(count), count,
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def render_working_model(model: Mapping[str, Any]) -> str:
    count = int(model.get("evidence_count") or 0)
    if not count:
        return "Пока недостаточно проверенных попыток, чтобы делать выводы о твоих рабочих паттернах."
    prefix = "Сегодня появилась гипотеза" if count == 1 else "Кажется, это повторяется" if count <= 3 else "Похоже, это один из твоих рабочих паттернов"
    barrier = _top(model.get("recurring_barriers"), "барьер пока уточняется")
    helped = _top(model.get("helpful_interventions"), "полезный способ пока проверяем")
    unhelpful = _top(model.get("unhelpful_interventions"), "пока нет устойчиво бесполезного способа")
    return (
        "Вот что я пока понял о тебе.\n\n"
        f"{prefix}: чаще встречается «{barrier}».\n"
        f"Помогало: {helped}.\n"
        f"Пока не помогало: {unhelpful}.\n\n"
        "Это рабочая версия, а не диагноз: её можно исправить следующими результатами."
    )


def _counts(value: Any) -> dict[str, int]:
    return {str(k): int(v) for k, v in dict(value or {}).items() if str(k).strip() and int(v) > 0}


def _bump(values: dict[str, int], key: str) -> None:
    key = " ".join(str(key or "").split())[:100]
    if key:
        values[key] = values.get(key, 0) + 1


def _confidence(count: int) -> str:
    return "hypothesis" if count == 1 else "repeating" if count <= 3 else "working_pattern"


def _top(value: Any, fallback: str) -> str:
    values = _counts(value)
    return max(values, key=lambda key: (values[key], key)) if values else fallback
