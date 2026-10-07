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
    explicit_user_correction: str = ""
    evidence_refs: tuple[str, ...] = ()
    hypothesis_rejected: bool = False
    evidence_records: dict[str, dict[str, Any]] = field(default_factory=dict)
    observed_skills: dict[str, int] = field(default_factory=dict)

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
            "explicit_user_correction": self.explicit_user_correction,
            "evidence_refs": list(self.evidence_refs),
            "hypothesis_rejected": self.hypothesis_rejected,
            "evidence_records": self.evidence_records,
            "observed_skills": self.observed_skills,
        }


def update_working_model(
    previous: Mapping[str, Any] | None,
    *,
    barrier: str,
    skill_title: str,
    context: str,
    successful: bool | None,
    evidence_ref: str,
    step_size: str = "",
) -> PersonalWorkingModel:
    """Add one normalized observation; an evidence reference is mandatory."""
    if not str(evidence_ref or "").strip():
        raise ValueError("PersonalWorkingModel updates require evidence_ref")
    barrier, skill_title, context = (" ".join(str(value or "").split())[:100] for value in (barrier, skill_title, context))
    old = dict(previous or {})
    refs = tuple(str(ref) for ref in old.get("evidence_refs", []) if ref)
    records = {key: dict(value) for key, value in (old.get("evidence_records") or {}).items()}
    observation = dict(barrier=barrier, skill=skill_title, context=context, successful=successful)
    prior = records.get(evidence_ref)
    if evidence_ref in refs and (prior is None or prior == observation):
        fields = PersonalWorkingModel.__dataclass_fields__
        return PersonalWorkingModel(**{key: value for key, value in old.items() if key in fields})
    barriers = _counts(old.get("recurring_barriers"))
    successes = _counts(old.get("successful_skills"))
    failures = _counts(old.get("failed_skills"))
    contexts = _counts(old.get("common_contexts"))
    helpful = _counts(old.get("helpful_interventions"))
    unhelpful = _counts(old.get("unhelpful_interventions"))
    observed = _counts(old.get("observed_skills")) or {key: successes.get(key, 0) + failures.get(key, 0) for key in successes.keys() | failures.keys()}
    if prior:
        for values, key in ((barriers, prior['barrier']), (contexts, prior['context']), (observed, prior['skill'])):
            _decrement(values, key)
        if prior['successful'] is True:
            _decrement(successes, prior['skill']); _decrement(helpful, prior['skill'])
        elif prior['successful'] is False:
            _decrement(failures, prior['skill']); _decrement(unhelpful, prior['skill'])
    records[evidence_ref] = observation
    _bump(observed, skill_title)
    _bump(barriers, barrier)
    _bump(contexts, context)
    if successful:
        _bump(successes, skill_title)
        _bump(helpful, skill_title)
    elif successful is False:
        _bump(failures, skill_title)
        _bump(unhelpful, skill_title)
    count = int(old.get("evidence_count") or 0) + (0 if prior else 1)
    return PersonalWorkingModel(
        barriers, successes, failures, step_size or str(old.get("effective_step_size") or ""),
        contexts, helpful, unhelpful, ("needs_recheck" if old.get("hypothesis_rejected") else "user_corrected" if old.get("explicit_user_correction") else _confidence(count)), count,
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        str(old.get("explicit_user_correction") or ""), refs if prior else (*refs, evidence_ref),
        bool(old.get("hypothesis_rejected")), records, observed,
    )


def render_working_model(model: Mapping[str, Any]) -> str:
    count = int(model.get("evidence_count") or 0)
    if not count:
        correction = " ".join(str(model.get("explicit_user_correction") or "").split())
        return ("Пока нет результатов попыток. После практики здесь появится то, что вам помогало."
                + (f"\n\nВаше уточнение: «{correction}»." if correction else ""))
    successes = _counts(model.get("helpful_interventions"))
    failures = _counts(model.get("unhelpful_interventions"))
    observed = _counts(model.get("observed_skills"))
    helped = [f"— {name}: помогло в {value} из {max(observed.get(name, 0), value + failures.get(name, 0))} попыток"
              for name, value in sorted(successes.items(), key=lambda item: -item[1])[:3]]
    correction = " ".join(str(model.get("explicit_user_correction") or "").split())
    return (
        "Что мы узнали из ваших попыток\n\n" +
        ("Предыдущую гипотезу вы отвергли; причину ещё уточняем.\n\n" if model.get("hypothesis_rejected") else f"Чаще мешало: {_top(model.get('recurring_barriers'), 'пока неясно')}.\n\n") +
        "Что помогало\n" + ("\n".join(helped) if helped else "Пока нет подтверждённого способа.") +
        (f"\n\nВаше уточнение: «{correction}»." if correction else "") +
        "\n\nПока неясно, повторится ли результат в другой ситуации. Вы можете исправить этот вывод."
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



def _decrement(values: dict[str, int], key: str) -> None:
    key = " ".join(str(key or "").split())[:100]
    count = values.get(key, 0) - 1
    if count > 0:
        values[key] = count
    else:
        values.pop(key, None)
