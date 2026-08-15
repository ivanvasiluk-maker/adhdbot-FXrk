"""Versioned JSON skill-card loader and policy-facing registry API."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from core.skill_index import SkillIndex
from core.skill_schema import Skill
from core.skill_validator import (
    ValidationIssue, fallback_cycles, next_skill_cycles, prerequisite_cycles,
    validate_references, validate_skill,
)


class SkillLibraryError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedCard:
    skill: Skill
    semver: str
    source_path: str
    content_hash: str


def _strings(value: Any) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in (value or ()) if str(item).strip())


def _card(raw: dict[str, Any], source: Path) -> LoadedCard:
    semver = str(raw.get("version") or "").strip()
    levels = raw.get("difficulty_levels") or []
    difficulty = tuple(int(item["level"] if isinstance(item, dict) else item) for item in levels)
    trainers = raw.get("trainer_texts") or raw.get("trainer_variants") or {}
    mastery = raw.get("mastery_criteria") or {}
    feedback = raw.get("feedback_schema") or {}
    references = raw.get("source_references") or ()
    source_reference = ";".join(
        str(item.get("internal_ref") or "") if isinstance(item, dict) else str(item) for item in references
    ).strip(";")
    variants = raw.get("variants") or {}
    skill = Skill(
        id=str(raw.get("skill_id") or "").strip(),
        version=int(semver.split(".")[0]) if semver and semver.split(".")[0].isdigit() else 0,
        title_user=str(raw.get("title") or "").strip(),
        title_internal=str(raw.get("short_title") or raw.get("skill_id") or "").strip(),
        approach=str(raw.get("source_family") or "OTHER").upper(),
        mechanisms=_strings(raw.get("mechanisms")),
        action_phases=_strings(raw.get("action_targets")),
        contexts=_strings(raw.get("contexts")),
        contraindications=_strings(raw.get("contraindications")),
        safety_tags=_strings(raw.get("safety_tags") or (raw.get("safety_level") or "standard",)),
        prerequisites=_strings(raw.get("prerequisites")),
        next_skills=_strings(raw.get("next_skills")),
        fallback_skills=_strings(raw.get("fallback_skills")),
        difficulty_levels=difficulty,
        min_variant=str(variants.get("minimum") or raw.get("min_variant") or "").strip(),
        standard_variant=str(variants.get("standard") or raw.get("standard_variant") or "").strip(),
        completion_criterion=str(raw.get("completion_criteria") or raw.get("completion_criterion") or "").strip(),
        feedback_questions=tuple(str(key) for key in feedback) if isinstance(feedback, dict) else _strings(feedback),
        mastery_criteria=json.dumps(mastery, ensure_ascii=False, sort_keys=True) if isinstance(mastery, dict) else str(mastery),
        minimum_successes=int(raw.get("minimum_successes") or (mastery.get("successful_practice_count", 0) if isinstance(mastery, dict) else 0)),
        maintenance_rule=str(raw.get("maintenance_rule") or "").strip(),
        generalization_contexts=_strings(raw.get("generalization_contexts")),
        evidence_source_internal=source_reference,
        quality_status=str(raw.get("status") or "experimental"),
        trainer_variants={key: str(value).strip() for key, value in trainers.items()},
    )
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    try:
        source_path = str(source.relative_to(Path.cwd()))
    except ValueError:
        source_path = str(source)
    return LoadedCard(skill, semver, source_path, hashlib.sha256(canonical).hexdigest())


class FileSkillRegistry:
    """Loads approved cards and isolates non-production validation failures."""

    def __init__(self, cards: Iterable[LoadedCard], issues: Iterable[ValidationIssue] = ()):
        self._cards: dict[tuple[str, str], LoadedCard] = {}
        self._issues = list(issues)
        for card in cards:
            key = (card.skill.id, card.semver)
            if key in self._cards:
                raise SkillLibraryError(f"duplicate skill version: {key[0]}@{key[1]}")
            self._cards[key] = card
        latest = self._latest_cards()
        self.index = SkillIndex.build(card.skill for card in latest.values())

    @classmethod
    def load(
        cls, path: str | Path, *, fail_closed: bool = True,
        baseline_skills: Iterable[Skill] = (),
    ) -> "FileSkillRegistry":
        root = Path(path)
        parsed: list[LoadedCard] = []
        for skill in baseline_skills:
            payload = json.dumps(asdict(skill), ensure_ascii=False, sort_keys=True, default=list).encode()
            parsed.append(LoadedCard(
                skill, f"{skill.version}.0.0", "legacy-registry-adapter",
                hashlib.sha256(payload).hexdigest(),
            ))
        parse_issues: list[ValidationIssue] = []
        for source in sorted(root.glob("*.json")):
            payload = json.loads(source.read_text(encoding="utf-8"))
            rows = payload if isinstance(payload, list) else [payload]
            for raw in rows:
                card = _card(raw, source)
                issues = [*_raw_issues(raw, card.skill), *validate_skill(card.skill, source_version=card.semver)]
                fatal = [issue for issue in issues if issue.fatal]
                if fatal:
                    # Fail closed only for cards claiming production eligibility.
                    # Draft contours are isolated with inspectable errors and can
                    # never enter an allowed/rankable candidate set.
                    if fail_closed and card.skill.quality_status == "production":
                        raise SkillLibraryError(_format_issues(fatal))
                    parse_issues.extend(issues)
                    continue
                if issues:
                    parse_issues.extend(issues)
                    continue
                parsed.append(card)
        graph_issues = validate_references(card.skill for card in parsed)
        fatal_graph = [issue for issue in graph_issues if issue.fatal]
        cycles = {
            "fallback": fallback_cycles(card.skill for card in parsed),
            "prerequisite": prerequisite_cycles(card.skill for card in parsed),
            "next_skill": next_skill_cycles(card.skill for card in parsed),
        }
        found_cycles = {name: value for name, value in cycles.items() if value}
        if fatal_graph or found_cycles:
            detail = _format_issues(fatal_graph)
            for name, values in found_cycles.items():
                detail += f" {name} cycles: " + ", ".join(" -> ".join(cycle) for cycle in values)
            if fail_closed:
                raise SkillLibraryError(detail.strip())
        return cls(parsed, (*parse_issues, *graph_issues))

    def _latest_cards(self) -> dict[str, LoadedCard]:
        latest: dict[str, LoadedCard] = {}
        for card in self._cards.values():
            previous = latest.get(card.skill.id)
            if previous is None or _semver_key(card.semver) > _semver_key(previous.semver):
                latest[card.skill.id] = card
        return latest

    def get(self, skill_id: str, version: str | None = None) -> Skill | None:
        if version is not None:
            card = self._cards.get((skill_id, version))
        else:
            card = self._latest_cards().get(skill_id)
        return card.skill if card else None

    def get_canonical(self, skill_id: str, version: str | None = None) -> LoadedCard | None:
        if version is not None:
            return self._cards.get((skill_id, version))
        return self._latest_cards().get(skill_id)

    def get_candidates(self, mechanism: str, context: str, action_target: str,
                       allowed_statuses: Iterable[str] = ("production",)) -> tuple[Skill, ...]:
        allowed = set(allowed_statuses)
        ids = set(self.index.by_mechanism.get(mechanism, ()))
        ids &= set(self.index.by_context.get(context, ()))
        ids &= set(self.index.by_action_phase.get(action_target, ()))
        latest = self._latest_cards()
        return tuple(latest[value].skill for value in sorted(ids) if latest[value].skill.quality_status in allowed)

    def allowed(self, statuses: Iterable[str] = ("production",)) -> tuple[Skill, ...]:
        """Return only the latest version of explicitly allowed cards."""
        allowed = set(statuses)
        return tuple(
            card.skill for _, card in sorted(self._latest_cards().items())
            if card.skill.quality_status in allowed
        )

    def get_fallbacks(self, skill_id: str, reason_code: str) -> tuple[Skill, ...]:
        if reason_code in {"SAFETY_DETERIORATION", "WORSE"}:
            return ()
        return tuple(filter(None, (self.get(value) for value in self.index.fallback_graph.get(skill_id, ()))))

    def get_next_level(self, skill_id: str, current_level: int) -> int | None:
        skill = self.get(skill_id)
        return next((level for level in skill.difficulty_levels if level > current_level), None) if skill else None

    def get_generalization_options(self, skill_id: str, excluded_contexts: Iterable[str]) -> tuple[str, ...]:
        skill = self.get(skill_id)
        excluded = set(excluded_contexts)
        return tuple(value for value in (skill.generalization_contexts if skill else ()) if value not in excluded)

    def is_allowed_for_user(self, skill_id: str, cohort: str) -> bool:
        skill = self.get(skill_id)
        return bool(skill and (skill.quality_status == "production" or (
            cohort in {"admin", "tester"} and skill.quality_status == "reviewed"
        )))

    def explain_validation_error(self, skill_id: str) -> tuple[str, ...]:
        return tuple(f"{issue.code}: {issue.message}" for issue in self._issues if issue.skill_id == skill_id)

    def manifest(self) -> dict[str, Any]:
        counts = self.contour_counts()
        return {"schema_version": 2, "skills": sum(counts.values()), **counts, "cards": [
            {"skill_id": card.skill.id, "version": card.semver, "sha256": card.content_hash,
             "status": card.skill.quality_status, "source": card.source_path}
            for card in sorted(self._cards.values(), key=lambda item: (item.skill.id, _semver_key(item.semver)))
        ]}

    def contour_counts(self) -> dict[str, int]:
        counts = {status: 0 for status in ("production", "reviewed", "experimental", "disabled")}
        for card in self._latest_cards().values():
            counts[card.skill.quality_status] = counts.get(card.skill.quality_status, 0) + 1
        return counts


def _semver_key(value: str) -> tuple[int, int, int]:
    try:
        return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]
    except (TypeError, ValueError):
        return (0, 0, 0)


def _format_issues(issues: Iterable[ValidationIssue]) -> str:
    return "; ".join(f"{item.skill_id}:{item.code}:{item.message}" for item in issues)


def _raw_issues(raw: dict[str, Any], skill: Skill) -> list[ValidationIssue]:
    if skill.quality_status != "production":
        return []
    issues = []
    if str(raw.get("reviewer_status") or "").strip().lower() not in {"reviewed", "approved"}:
        issues.append(ValidationIssue(skill.id, "NOT_REVIEWED", "production card requires reviewer_status", True))
    if not raw.get("source_references"):
        issues.append(ValidationIssue(skill.id, "MISSING_SOURCE", "production card requires source_references", True))
    variants = raw.get("variants") or {}
    if not variants.get("minimum") or not variants.get("standard"):
        issues.append(ValidationIssue(skill.id, "MISSING_VARIANT", "minimum and standard variants are required", True))
    if not skill.fallback_skills and not str(raw.get("fallback_policy") or "").strip():
        issues.append(ValidationIssue(skill.id, "MISSING_FALLBACK", "fallback_skills or fallback_policy required", True))
    return issues
