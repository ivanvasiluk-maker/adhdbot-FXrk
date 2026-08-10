"""PATCH-10: transparent journal reconstructed from normalized experiments."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

MECHANISM_LABELS = {
    "evaluation_avoidance": "страх оценки мешает приблизиться к задаче",
    "executive_start_deficit": "трудно перейти от намерения к первому действию",
    "choice_overload": "слишком много вариантов мешают выбрать шаг",
    "low_activation": "для старта сейчас мало энергии",
    "rumination": "мысли мешают перейти к действию",
    "emotional_avoidance": "неприятное состояние усиливает избегание",
    "perfectionism_error_fear": "страх ошибки делает первый шаг дорогим",
    "attention_drift": "внимание быстро уходит от задачи",
    "unclear_next_action": "следующее физическое действие пока неясно",
    "low_reward": "у действия мало быстрой отдачи",
    "overwhelm": "задача ощущается слишком большой",
    "recovery_after_lapse": "нужен мягкий возврат после паузы",
}
PROGRESSION_LABELS = {
    "first": "первая проверка", "repeat": "повтор с изменением",
    "simplify": "упрощение", "advance": "усложнение",
    "transfer": "перенос в новый контекст", "maintenance": "поддержание",
}


@dataclass(frozen=True)
class JournalEntry:
    experiment_id: int
    parent_experiment_id: int | None
    progression_type: str
    situation: str
    hypothesis: str
    action: str
    result: str
    conclusion: str
    next_step: str
    occurred_at: str


def _result(record: Mapping[str, Any]) -> str:
    if record.get("action_started") is None:
        return "Результат ещё не зафиксирован."
    started = {"yes": "действие начато", "partial": "получилось начать частично", "no": "начать не получилось"}.get(
        str(record.get("action_started")), "результат отмечен",
    )
    emotion = {"better": "стало легче", "same": "состояние не изменилось", "worse": "стало хуже", "unknown": "изменение состояния неясно"}.get(
        str(record.get("emotional_change")), "",
    )
    return f"{started}; {emotion}." if emotion else f"{started}."


def _conclusion(record: Mapping[str, Any]) -> str:
    if bool(record.get("success_criterion_met")):
        return "Проверяемый критерий выполнен."
    reason = {
        "too_hard": "Шаг оказался слишком трудным.",
        "wrong_mechanism": "Гипотеза о причине затруднения не подтвердилась.",
        "skill_mismatch": "Этот способ пока не подошёл.",
        "external_blocker": "Попытке помешало внешнее препятствие.",
        "safety_deterioration": "Попытка остановлена из-за ухудшения состояния.",
    }.get(str(record.get("failure_reason_code") or ""))
    return reason or "Данных пока недостаточно для устойчивого вывода."


def _next_step(record: Mapping[str, Any]) -> str:
    action = str(record.get("next_action") or "")
    return {
        "repeat": "Повторить с другой задачей или вариантом.",
        "simplify": "Сделать следующий эксперимент проще.",
        "replace": "Подобрать другой навык под механизм.",
        "advance": "Попробовать следующий уровень сложности.",
        "transfer": "Проверить навык в новом контексте.",
        "maintain": "Использовать навык по необходимости.",
        "stop": "Остановиться: текущая проблема решена.",
        "safety": "Не продолжать эксперимент и перейти к безопасной поддержке.",
    }.get(action, "Следующий шаг ещё не выбран.")


def journal_entry_from_record(record: Mapping[str, Any]) -> JournalEntry:
    mechanism = MECHANISM_LABELS.get(str(record.get("mechanism_code") or ""), "проверяем рабочую гипотезу")
    criterion = str(record.get("success_criterion") or "").strip()
    return JournalEntry(
        experiment_id=int(record["experiment_id"]),
        parent_experiment_id=record.get("parent_experiment_id"),
        progression_type=str(record.get("progression_type") or "first"),
        situation=str(record.get("task_summary") or "Короткая ситуация не указана"),
        hypothesis=f"{mechanism}. Проверка: {criterion}",
        action=str(record.get("instruction_variant") or record.get("target_action") or ""),
        result=_result(record), conclusion=_conclusion(record), next_step=_next_step(record),
        occurred_at=str(record.get("captured_at") or record.get("started_at") or ""),
    )


def build_experiment_journal(records: Iterable[Mapping[str, Any]]) -> tuple[JournalEntry, ...]:
    return tuple(journal_entry_from_record(record) for record in records)


def render_experiment_journal(entries: Iterable[JournalEntry]) -> str:
    values = tuple(entries)
    if not values:
        return "Журнал экспериментов пока пуст."
    rendered = ["Журнал экспериментов"]
    for number, entry in enumerate(values, 1):
        progression = PROGRESSION_LABELS.get(entry.progression_type, "следующая проверка")
        rendered.append(
            f"{number}. {progression}\n"
            f"Ситуация: {entry.situation}\n"
            f"Что проверяли: {entry.hypothesis}\n"
            f"Действие: {entry.action}\n"
            f"Результат: {entry.result}\n"
            f"Вывод: {entry.conclusion}\n"
            f"Следующий шаг: {entry.next_step}"
        )
    return "\n\n".join(rendered)


def anonymized_journal_export(
    entries: Iterable[JournalEntry], *, user_id: int, secret_salt: str,
) -> list[dict[str, Any]]:
    """Export normalized analytics with stable opaque ids and no user text."""
    if not secret_salt:
        raise ValueError("A private export salt is required")
    anonymous_user = hmac.new(secret_salt.encode(), str(user_id).encode(), hashlib.sha256).hexdigest()[:20]
    return [{
        "anonymous_user_id": anonymous_user,
        "anonymous_experiment_id": hmac.new(
            secret_salt.encode(), f"{user_id}:{entry.experiment_id}".encode(), hashlib.sha256,
        ).hexdigest()[:20],
        "progression_type": entry.progression_type,
        "has_parent": entry.parent_experiment_id is not None,
        "occurred_at": entry.occurred_at,
    } for entry in entries]
