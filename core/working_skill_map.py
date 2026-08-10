"""PATCH-09: evidence-first user map, deliberately not a skill catalogue."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

CONTEXT_LABELS = {
    "work": "Работа", "study": "Учёба", "relationships": "Отношения",
    "health": "Здоровье", "home": "Дом", "finance": "Финансы", "other": "Другое",
}
STATUS_LABELS = {
    "promising": "Похоже, помогает", "working": "Работает для меня",
    "MASTERED": "Освоен и помогает", "LEARNING": "Знакомлюсь с навыком",
    "PRACTICING": "Сейчас тренирую", "GENERALIZING": "Пробую в новых ситуациях",
    "unreliable": "Пока работает нестабильно", "avoid": "Пока лучше не использовать",
}


@dataclass(frozen=True)
class SkillMapEntry:
    skill_id: str
    title_user: str
    context_domain: str
    effectiveness_band: str
    mastery_status: str
    attempts_count: int
    successes_count: int
    independent_successes: int
    last_used_at: str
    evidence_refs: tuple[str, ...]
    recommendation_disabled: bool = False

    @property
    def has_success_evidence(self) -> bool:
        return self.successes_count > 0 and any(ref.startswith("experiment:") for ref in self.evidence_refs)


@dataclass(frozen=True)
class SkillMap:
    works_for_me: tuple[SkillMapEntry, ...]
    training_now: tuple[SkillMapEntry, ...]
    not_fit_yet: tuple[SkillMapEntry, ...]
    by_context: Mapping[str, tuple[SkillMapEntry, ...]]


def build_working_skill_map(entries: Iterable[SkillMapEntry]) -> SkillMap:
    values = tuple(entries)
    works = tuple(item for item in values if (
        item.effectiveness_band in {"promising", "working"} or item.mastery_status == "MASTERED"
    ) and item.has_success_evidence)
    training = tuple(item for item in values if item.mastery_status in {"LEARNING", "PRACTICING", "GENERALIZING"})
    not_fit = tuple(item for item in values if item.effectiveness_band in {"unreliable", "avoid"})
    by_context = {
        context: tuple(item for item in values if item.context_domain == context)
        for context in CONTEXT_LABELS
        if any(item.context_domain == context for item in values)
    }
    return SkillMap(works, training, not_fit, by_context)


def entry_from_record(record: Mapping, *, title_user: str, mastery_status: str = "") -> SkillMapEntry:
    return SkillMapEntry(
        skill_id=str(record.get("skill_id") or ""), title_user=title_user,
        context_domain=str(record.get("context_domain") or "other"),
        effectiveness_band=str(record.get("effectiveness_band") or "unknown"),
        mastery_status=mastery_status, attempts_count=int(record.get("attempts_count") or 0),
        successes_count=int(record.get("successes_count") or 0),
        independent_successes=int(record.get("independent_successes") or 0),
        last_used_at=str(record.get("last_used_at") or ""),
        evidence_refs=tuple(record.get("evidence_refs") or ()),
        recommendation_disabled=bool(record.get("recommendation_disabled")),
    )


def _next_step(entry: SkillMapEntry) -> str:
    if entry.recommendation_disabled:
        return "Рекомендация отключена по твоему выбору."
    if entry.effectiveness_band in {"avoid", "unreliable"}:
        return "Можно оставить этот навык в стороне и подобрать другой способ."
    if entry.mastery_status == "GENERALIZING":
        return "Следующий шаг — попробовать в другой подходящей ситуации."
    if entry.independent_successes:
        return "Можно использовать снова, когда появится похожая ситуация."
    return "Следующий шаг — ещё одна короткая попытка с другой задачей или вариантом."


def _render_entry(entry: SkillMapEntry) -> str:
    status = STATUS_LABELS.get(entry.mastery_status) or STATUS_LABELS.get(entry.effectiveness_band) or "Пока собираем данные"
    context = CONTEXT_LABELS.get(entry.context_domain, "Другое")
    last_used = entry.last_used_at[:10] if entry.last_used_at else "ещё не зафиксировано"
    return (
        f"• {entry.title_user}\n"
        f"  {status}. Где: {context}. Самостоятельно: {entry.independent_successes}. "
        f"Последнее использование: {last_used}.\n"
        f"  {_next_step(entry)}\n"
        "  Действия: использовать снова · исправить вывод · не предлагать · посмотреть эксперименты"
    )


def render_working_skill_map(skill_map: SkillMap) -> str:
    """Render evidence in plain language; never expose ids, scores, or percentages."""
    def section(title: str, items: tuple[SkillMapEntry, ...], empty: str) -> str:
        body = "\n".join(_render_entry(item) for item in items) if items else f"• {empty}"
        return f"{title}\n{body}"

    context_lines = []
    for context, items in skill_map.by_context.items():
        successful_titles = [item.title_user for item in items if item.has_success_evidence]
        if successful_titles:
            context_lines.append(f"• {CONTEXT_LABELS.get(context, 'Другое')}: {', '.join(successful_titles)}")
    return "\n\n".join((
        "Что помогает именно мне",
        section("Работает для меня", skill_map.works_for_me, "пока проверяем первые навыки"),
        section("Сейчас тренирую", skill_map.training_now, "сейчас нет навыка на закреплении"),
        section("Пока не подошло", skill_map.not_fit_yet, "пока нет навыков, которые стоит отложить"),
        "По контекстам\n" + ("\n".join(context_lines) if context_lines else "• пока недостаточно успешных попыток"),
    ))
