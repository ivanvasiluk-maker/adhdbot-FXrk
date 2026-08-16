"""Map reviewed spreadsheet rows into canonical, review-safe skill cards."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

ALIASES = {
    "skill_id": ("skill_id", "id", "код", "код навыка", "skill code"),
    "version": ("version", "версия"),
    "status": ("status", "quality_status", "статус", "статус проверки"),
    "title": ("title", "title_user", "название", "название навыка", "навык"),
    "short_title": ("short_title", "короткое название"),
    "source_family": ("source_family", "approach", "подход"),
    "mechanisms": ("mechanisms", "mechanism", "механизмы", "механизм", "предполагаемый механизм"),
    "action_targets": ("action_targets", "action_phases", "фазы действия"),
    "contexts": ("contexts", "контексты", "контекст", "домен"),
    "contraindications": ("contraindications", "противопоказания", "не применять", "безопасность/ограничения"),
    "fallback_skills": ("fallback_skills", "fallback", "упрощение"),
    "next_skills": ("next_skills", "следующие навыки"),
    "minimum": ("min_variant", "minimum", "минимум", "минимальная версия", "мини-версия"),
    "standard": ("standard_variant", "instruction", "how", "инструкция", "обычная версия", "алгоритм"),
    "completion": ("completion_criteria", "completion_criterion", "критерий завершения"),
    "source": ("source_references", "evidence_source_internal", "источник", "первоисточник"),
    "reviewer_status": ("reviewer_status", "review status", "проверка редактора"),
}

DRAFT_STATUSES = (
    "черновик",
    "draft",
)

BSEB_MECHANISMS = {
    "внешняя опора для исполнительных функций и снижение неопределённости": "executive_start_deficit",
    "повышение осознания времени и калибровка временных прогнозов": "unclear_next_action",
    "снижение порога начала и перевод намерения в наблюдаемое действие": "executive_start_deficit",
    "структурирование выбора, препятствий и следующего действия": "choice_overload",
    "снижение конкурирующих стимулов и облегчение возврата внимания": "attention_drift",
    "снижение избегания, завышенных стандартов и ожидания мотивации": "perfectionism_error_fear",
    "перенос хранения информации из рабочей памяти во внешнюю среду": "unclear_next_action",
    "создание паузы и выбор регулирующего действия вместо автоматической реакции": "emotional_avoidance",
    "увеличение задержки между импульсом и действием": "emotional_avoidance",
    "закрепление поведения через устойчивые сигналы и упрощение последовательности": "low_reward",
    "повышение ясности, предсказуемости и совместной регуляции": "evaluation_avoidance",
    "адаптация требований среды и профилактика распада системы": "recovery_after_lapse",
}

BSEB_CONTEXTS = {
    "Коммуникация и отношения": "communication",
    "Рутины и самообслуживание": "health",
    "Среда, работа и поддержание": "work",
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


def _status(value: str) -> str:
    normalized = value.strip().lower()
    if any(marker in normalized for marker in DRAFT_STATUSES):
        return "experimental"
    return normalized or "experimental"


def _version(value: str) -> str:
    normalized = value.strip().lower().removeprefix("v") or "1.0.0"
    parts = normalized.split(".")
    if len(parts) == 1 and parts[0].isdigit():
        return f"{parts[0]}.0.0"
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return f"{normalized}.0"
    return normalized


def _mechanisms(value: str) -> list[str]:
    normalized = value.strip().lower()
    return [BSEB_MECHANISMS[normalized]] if normalized in BSEB_MECHANISMS else _list(value)


def _contexts(value: str) -> list[str]:
    normalized = value.strip()
    if normalized in BSEB_CONTEXTS:
        return [BSEB_CONTEXTS[normalized]]
    # BSEB domains describe a task barrier rather than a life domain.
    if normalized and normalized[0:1].isupper():
        return ["other"]
    return _list(value) or ["other"]


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
        status = _status(_value(row, "status", "experimental"))
        if status not in {"production", "reviewed", "experimental", "disabled"}:
            problems.append(ImportProblem(number, skill_id, f"unknown status {status!r}"))
            continue
        minimum = _value(row, "minimum", instruction)
        source = _value(row, "source", source_ref)
        card = {
            "skill_id": skill_id, "version": _version(_value(row, "version", "1.0.0")), "status": status,
            "title": title, "short_title": _value(row, "short_title", title),
            "source_family": _value(row, "source_family", "OTHER").upper(),
            "mechanisms": _mechanisms(_value(row, "mechanisms")),
            "action_targets": _list(_value(row, "action_targets", "start")),
            "contexts": _contexts(_value(row, "contexts", "other")),
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
