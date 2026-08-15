"""Structural, taxonomy, safety and graph validation for file skill cards."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from core.skill_schema import ACTION_PHASES, APPROACHES, CONTEXTS, QUALITY_STATUSES, Skill
from core.skill_taxonomy import MECHANISM_CODES

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
FORBIDDEN_PROMISES = ("гарантированно вылеч", "лечит сдвг", "поставит диагноз")


@dataclass(frozen=True)
class ValidationIssue:
    skill_id: str
    code: str
    message: str
    fatal: bool


def validate_skill(skill: Skill, *, source_version: str | None = None) -> list[ValidationIssue]:
    fatal = skill.quality_status == "production"
    issues: list[ValidationIssue] = []

    def add(code: str, message: str, *, always_fatal: bool = False) -> None:
        issues.append(ValidationIssue(skill.id, code, message, always_fatal or fatal))

    if source_version is not None and not SEMVER.fullmatch(source_version):
        add("INVALID_SEMVER", f"version must be semver, got {source_version!r}")
    if not skill.id.strip() or not skill.title_user.strip() or not skill.title_internal.strip():
        add("MISSING_IDENTITY", "id and titles are required", always_fatal=True)
    if skill.approach not in APPROACHES:
        add("UNKNOWN_APPROACH", skill.approach, always_fatal=True)
    if not skill.mechanisms:
        add("MISSING_MECHANISM", "at least one mechanism is required")
    unknown_mechanisms = sorted(set(skill.mechanisms) - MECHANISM_CODES)
    if unknown_mechanisms:
        add("UNKNOWN_MECHANISM", ", ".join(unknown_mechanisms))
    unknown_phases = sorted(set(skill.action_phases) - set(ACTION_PHASES))
    if unknown_phases:
        add("UNKNOWN_ACTION_PHASE", ", ".join(unknown_phases))
    unknown_contexts = sorted(set(skill.contexts) - set(CONTEXTS))
    if unknown_contexts:
        add("UNKNOWN_CONTEXT", ", ".join(unknown_contexts))
    if skill.quality_status not in QUALITY_STATUSES:
        add("UNKNOWN_STATUS", skill.quality_status, always_fatal=True)
    if not skill.evidence_source_internal.strip():
        add("MISSING_SOURCE", "source reference is required")
    if not skill.feedback_questions:
        add("MISSING_FEEDBACK", "at least one feedback question is required")
    if not skill.min_variant.strip() or not skill.standard_variant.strip():
        add("MISSING_INSTRUCTION", "minimum and standard instructions are required", always_fatal=True)
    if not skill.completion_criterion.strip():
        add("MISSING_COMPLETION", "completion criterion is required")
    if skill.quality_status == "production" and not skill.fallback_skills:
        add("MISSING_FALLBACK", "fallback_skills or an explicit fallback policy is required")
    if skill.quality_status == "production" and not skill.contraindications:
        add("MISSING_CONTRAINDICATIONS", "conditions of non-use must be explicit")
    combined = " ".join((skill.title_user, skill.min_variant, skill.standard_variant)).lower()
    for phrase in FORBIDDEN_PROMISES:
        if phrase in combined:
            add("FORBIDDEN_PROMISE", phrase, always_fatal=True)
    if len(skill.standard_variant) > 1200:
        add("INSTRUCTION_TOO_LONG", "standard instruction exceeds 1200 characters")
    return issues


def validate_references(skills: Iterable[Skill]) -> list[ValidationIssue]:
    values = tuple(skills)
    ids = {skill.id for skill in values}
    issues: list[ValidationIssue] = []
    for skill in values:
        missing = sorted(set((*skill.prerequisites, *skill.next_skills, *skill.fallback_skills)) - ids)
        if missing:
            issues.append(ValidationIssue(
                skill.id, "MISSING_REFERENCE", ", ".join(missing), skill.quality_status == "production",
            ))
    return issues


def graph_cycles(skills: Iterable[Skill], relation: str) -> tuple[tuple[str, ...], ...]:
    graph = {skill.id: tuple(getattr(skill, relation)) for skill in skills}
    cycles: set[tuple[str, ...]] = set()

    def visit(node: str, path: tuple[str, ...]) -> None:
        if node in path:
            cycle = path[path.index(node):] + (node,)
            cycles.add(cycle)
            return
        for target in graph.get(node, ()):
            visit(target, path + (node,))

    for node in graph:
        visit(node, ())
    return tuple(sorted(cycles))


def fallback_cycles(skills: Iterable[Skill]) -> tuple[tuple[str, ...], ...]:
    return graph_cycles(skills, "fallback_skills")


def prerequisite_cycles(skills: Iterable[Skill]) -> tuple[tuple[str, ...], ...]:
    return graph_cycles(skills, "prerequisites")


def next_skill_cycles(skills: Iterable[Skill]) -> tuple[tuple[str, ...], ...]:
    return graph_cycles(skills, "next_skills")
