"""Map reviewed spreadsheet rows into canonical, review-safe skill cards."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

ALIASES = {
    "skill_id": ("skill_id", "id", "код", "код навыка", "skill code"),
    "version": ("version", "версия"),
    "status": ("status", "quality_status", "статус"),
    "title": ("title", "title_user", "название", "название навыка"),
    "short_title": ("short_title", "короткое название"),
    "source_family": ("source_family", "approach", "подход"),
    "mechanisms": ("mechanisms", "mechanism", "механизмы", "механизм"),
    "action_targets": ("action_targets", "action_phases", "фазы действия"),
    "contexts": ("contexts", "контексты", "контекст"),
    "contraindications": ("contraindications", "противопоказания", "не применять"),
    "fallback_skills": ("fallback_skills", "fallback", "упрощение"),
    "next_skills": ("next_skills", "следующие навыки"),
    "minimum": ("min_variant", "minimum", "минимум", "минимальная версия"),
    "standard": ("standard_variant", "instruction", "how", "инструкция", "обычная версия"),
    "completion": ("completion_criteria", "completion_criterion", "критерий завершения"),
    "source": ("source_references", "evidence_source_internal", "источник", "первоисточник"),
    "reviewer_status": ("reviewer_status", "review status", "проверка редактора"),
}


@dataclass(frozen=True)
class ImportProblem:
    row_number: int
    skill_id: str
    message: str


def _normalized(row: Mapping[str, str]) -> dict[str, str]:
    return {re.sub(r"\s+", " ", key.strip().lower()): str(value or "").strip() for key, value in row.items()}


def _value(row: Mapping[str, str], key: str, default: str = "") -> str:
    return next((row[name] for name in ALIASES[key] if row.get(name)), default)


def _list(value: str) -> list[str]:
    if not value.strip():
        return []
    if value.lstrip().startswith("["):
        parsed = json.loads(value)
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in re.split(r"[,;|\n]", value) if item.strip()]


def map_rows(rows: Iterable[Mapping[str, str]], *, source_ref: str) -> tuple[list[dict], list[ImportProblem]]:
    cards: list[dict] = []
    problems: list[ImportProblem] = []
    for number, original in enumerate(rows, 2):
        row = _normalized(original)
        skill_id = _value(row, "skill_id")
        title = _value(row, "title")
        instruction = _value(row, "standard")
        if not skill_id and not title:
            continue
        missing = [name for name, value in (("skill_id", skill_id), ("title", title),
                                              ("standard_variant", instruction)) if not value]
        if missing:
            problems.append(ImportProblem(number, skill_id, "missing " + ", ".join(missing)))
            continue
        status = _value(row, "status", "experimental").lower()
        if status not in {"production", "reviewed", "experimental", "disabled"}:
            problems.append(ImportProblem(number, skill_id, f"unknown status {status!r}"))
            continue
        minimum = _value(row, "minimum", instruction)
        source = _value(row, "source", source_ref)
        card = {
            "skill_id": skill_id, "version": _value(row, "version", "1.0.0"), "status": status,
            "title": title, "short_title": _value(row, "short_title", title),
            "source_family": _value(row, "source_family", "OTHER").upper(),
            "mechanisms": _list(_value(row, "mechanisms")),
            "action_targets": _list(_value(row, "action_targets", "start")),
            "contexts": _list(_value(row, "contexts", "other")),
            "contraindications": _list(_value(row, "contraindications")),
            "safety_tags": ["imported_requires_safety_review"], "prerequisites": [],
            "fallback_skills": _list(_value(row, "fallback_skills")),
            "next_skills": _list(_value(row, "next_skills")),
            "difficulty_levels": [{"level": 1, "instruction_key": "minimum"},
                                  {"level": 2, "instruction_key": "standard"}],
            "variants": {"minimum": minimum, "standard": instruction},
            "minimum_successes": 2,
            "mastery_criteria": {"successful_practice_count": 2, "independent_use_count": 2},
            "maintenance_rule": "on_similar_mechanism", "generalization_contexts": [],
            "completion_criteria": _value(row, "completion", minimum),
            "feedback_schema": {"action_started": "required", "emotional_after": "required"},
            "safety_level": "review_required" if status != "production" else "standard",
            "source_references": [{"internal_ref": source}],
            "reviewer_status": _value(row, "reviewer_status", "unreviewed"),
            "trainer_texts": {"marsha": instruction, "skinny": instruction, "beck": instruction},
            "import_metadata": {"source": source_ref, "sheet": original.get("_sheet", "")},
        }
        # Imports never silently promote content: production requires explicit review fields.
        if status == "production" and card["reviewer_status"] not in {"reviewed", "approved"}:
            problems.append(ImportProblem(number, skill_id, "production row is not explicitly reviewed"))
            continue
        if status == "production":
            required = {
                "mechanisms": card["mechanisms"], "contraindications": card["contraindications"],
                "fallback_skills": card["fallback_skills"], "completion_criteria": card["completion_criteria"],
                "source_references": source.strip(),
            }
            missing_production = [key for key, value in required.items() if not value]
            if missing_production:
                problems.append(ImportProblem(
                    number, skill_id, "production row missing " + ", ".join(missing_production),
                ))
                continue
        cards.append(card)
    return cards, problems
