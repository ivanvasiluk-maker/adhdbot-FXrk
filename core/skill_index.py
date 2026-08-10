"""Immutable lookup indexes over a validated skill library."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from core.skill_schema import Skill


@dataclass(frozen=True)
class SkillIndex:
    by_mechanism: dict[str, tuple[str, ...]]
    by_context: dict[str, tuple[str, ...]]
    by_action_phase: dict[str, tuple[str, ...]]
    fallback_graph: dict[str, tuple[str, ...]]
    next_graph: dict[str, tuple[str, ...]]

    @classmethod
    def build(cls, skills: Iterable[Skill]) -> "SkillIndex":
        mechanism: dict[str, list[str]] = defaultdict(list)
        context: dict[str, list[str]] = defaultdict(list)
        phase: dict[str, list[str]] = defaultdict(list)
        fallback: dict[str, tuple[str, ...]] = {}
        next_graph: dict[str, tuple[str, ...]] = {}
        for skill in sorted(skills, key=lambda item: item.id):
            for value in skill.mechanisms:
                mechanism[value].append(skill.id)
            for value in skill.contexts:
                context[value].append(skill.id)
            for value in skill.action_phases:
                phase[value].append(skill.id)
            fallback[skill.id] = tuple(skill.fallback_skills)
            next_graph[skill.id] = tuple(skill.next_skills)
        freeze = lambda values: {key: tuple(value) for key, value in values.items()}
        return cls(freeze(mechanism), freeze(context), freeze(phase), fallback, next_graph)
