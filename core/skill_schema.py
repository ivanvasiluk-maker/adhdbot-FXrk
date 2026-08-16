"""PATCH-06: validated Skill v2 registry with a legacy compatibility loader."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from collections.abc import Iterator
from typing import Any, Iterable, Literal, Mapping

from core.skill_taxonomy import ACTION_PHASES, APPROACHES, CONTEXTS, QUALITY_STATUSES

Approach = Literal["CBT", "DBT", "ACT", "BA", "ERP", "MBCT", "RO_DBT", "CBT_I", "OTHER"]
QualityStatus = Literal["production", "reviewed", "experimental", "disabled"]


@dataclass(frozen=True)
class Skill:
    id: str
    version: int
    title_user: str
    title_internal: str
    approach: Approach
    mechanisms: tuple[str, ...]
    action_phases: tuple[str, ...]
    contexts: tuple[str, ...]
    contraindications: tuple[str, ...]
    safety_tags: tuple[str, ...]
    prerequisites: tuple[str, ...]
    next_skills: tuple[str, ...]
    fallback_skills: tuple[str, ...]
    difficulty_levels: tuple[int, ...]
    min_variant: str
    standard_variant: str
    completion_criterion: str
    feedback_questions: tuple[str, ...]
    mastery_criteria: str
    minimum_successes: int
    maintenance_rule: str
    generalization_contexts: tuple[str, ...]
    evidence_source_internal: str
    quality_status: QualityStatus
    trainer_variants: Mapping[str, str]


class SkillRegistryError(ValueError):
    pass


class SkillRegistry:
    """Keeps the full library separate from the small rankable contour."""

    def __init__(self, skills: Iterable[Skill], *, include_reviewed: bool = False):
        self._skills: dict[str, Skill] = {}
        for skill in skills:
            if skill.id in self._skills:
                raise SkillRegistryError(f"duplicate skill id: {skill.id}")
            self._skills[skill.id] = skill
        self._validate()
        self.include_reviewed = include_reviewed

    def _validate(self) -> None:
        ids = set(self._skills)
        for skill in self._skills.values():
            if not skill.id or skill.version < 1 or not skill.title_user or not skill.title_internal:
                raise SkillRegistryError(f"{skill.id or '<empty>'}: identity fields are required")
            if skill.approach not in APPROACHES or skill.quality_status not in QUALITY_STATUSES:
                raise SkillRegistryError(f"{skill.id}: invalid approach or quality status")
            if not skill.mechanisms:
                raise SkillRegistryError(f"{skill.id}: at least one mechanism is required")
            if not skill.completion_criterion.strip():
                raise SkillRegistryError(f"{skill.id}: completion criterion is required")
            if not skill.min_variant.strip() or not skill.standard_variant.strip():
                raise SkillRegistryError(f"{skill.id}: minimum and standard variants are required")
            if skill.minimum_successes < 1 or not str(skill.mastery_criteria).strip():
                raise SkillRegistryError(f"{skill.id}: objective mastery criteria are required")
            if not skill.feedback_questions:
                raise SkillRegistryError(f"{skill.id}: at least one outcome question is required")
            if not skill.difficulty_levels or any(level not in range(1, 6) for level in skill.difficulty_levels):
                raise SkillRegistryError(f"{skill.id}: difficulty must be within 1..5")
            if set(skill.trainer_variants) != {"marsha", "skinny", "beck"}:
                raise SkillRegistryError(f"{skill.id}: all trainer variants are required")
            references = (*skill.fallback_skills, *skill.next_skills, *skill.prerequisites)
            missing = sorted(set(references) - ids)
            if missing:
                raise SkillRegistryError(f"{skill.id}: missing references: {', '.join(missing)}")
            if skill.quality_status == "production" and not skill.fallback_skills:
                raise SkillRegistryError(f"{skill.id}: production skill requires simplify fallback and min variant")

    def get(self, skill_id: str) -> Skill | None:
        return self._skills.get(skill_id)

    def all(self) -> tuple[Skill, ...]:
        return tuple(self._skills.values())

    def rankable(self, *, tester: bool = False) -> tuple[Skill, ...]:
        allowed = {"production"}
        if tester and self.include_reviewed:
            allowed.add("reviewed")
        return tuple(skill for skill in self._skills.values() if skill.quality_status in allowed)

    def rankable_ids(self, *, tester: bool = False) -> frozenset[str]:
        return frozenset(skill.id for skill in self.rankable(tester=tester))

    def legacy_view(self) -> dict[str, dict[str, Any]]:
        """Expose V1-shaped cards while call sites migrate incrementally."""
        return {
            skill.id: {
                "skill_id": skill.id, "name": skill.title_user,
                "how": skill.standard_variant, "minimum": skill.min_variant,
                "completion_criterion": skill.completion_criterion,
                "quality_status": skill.quality_status, "skill_v2": asdict(skill),
            }
            for skill in self._skills.values()
        }


class SkillAdapter(Mapping[str, dict[str, Any]]):
    """Read-only V1 mapping backed by the Registry as the source of identity.

    Existing flows can keep their dictionary-shaped contract while all lookup,
    membership and iteration are resolved through the validated Registry.
    """

    def __init__(self, registry: SkillRegistry, legacy: Mapping[str, Mapping[str, Any]] | None = None):
        self.registry = registry
        self._legacy = legacy or {}

    def __getitem__(self, skill_id: str) -> dict[str, Any]:
        skill = self.registry.get(skill_id)
        if skill is None:
            raise KeyError(skill_id)
        value = dict(self._legacy.get(skill_id, {}))
        value.update(self.registry.legacy_view()[skill_id])
        return value

    def __iter__(self) -> Iterator[str]:
        return (skill.id for skill in self.registry.all())

    def __len__(self) -> int:
        return len(self.registry.all())


def adapt_legacy_skills(
    legacy: Mapping[str, Mapping[str, Any]], *, production_limit: int = 40,
) -> list[Skill]:
    """Convert SKILLS_DB without requiring a flag-day rewrite of bot call sites."""
    ids = list(legacy)
    simplify_anchor = "open_only" if "open_only" in legacy else ids[0]
    alternate_anchor = "task_naming" if "task_naming" in legacy else simplify_anchor
    fallback_sink = ids[-1]
    result: list[Skill] = []
    production_ids = (set(ids[:max(0, production_limit - 2)]) | {simplify_anchor, alternate_anchor}) - {fallback_sink}
    for index, (skill_id, raw) in enumerate(legacy.items()):
        title = str(raw.get("name") or raw.get("title") or skill_id).strip()
        standard = str(raw.get("how") or raw.get("steps") or raw.get("goal") or title).strip()
        minimum = str(raw.get("minimum") or standard).strip()
        mechanism = str(raw.get("mechanism") or "executive_start_deficit")
        quality: QualityStatus = "production" if skill_id in production_ids else "reviewed"
        fallback = "" if skill_id == fallback_sink else fallback_sink
        result.append(Skill(
            id=skill_id, version=2, title_user=title, title_internal=skill_id,
            approach="OTHER", mechanisms=(mechanism,), action_phases=("start",), contexts=CONTEXTS,
            contraindications=(), safety_tags=(), prerequisites=(), next_skills=(),
            fallback_skills=(fallback,) if fallback else (), difficulty_levels=(1, 2, 3), min_variant=minimum,
            standard_variant=standard, completion_criterion=minimum,
            feedback_questions=("Что изменилось после попытки?",),
            mastery_criteria="Навык успешно применён самостоятельно в нескольких ситуациях",
            minimum_successes=2, maintenance_rule="Повторить при сходном барьере",
            generalization_contexts=CONTEXTS, evidence_source_internal="legacy_skills_db",
            quality_status=quality,
            trainer_variants={"marsha": standard, "skinny": standard, "beck": standard},
        ))
    return result
