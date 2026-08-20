"""PATCH-14: objective skill mastery and progressive removal of AI scaffolding."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Literal, Mapping, Sequence

from core.skill_schema import Skill

MasteryStatus = Literal["NEW", "LEARNING", "PRACTICING", "GENERALIZING", "MASTERED"]
ScaffoldingLevel = Literal["full", "reduced", "minimal", "none"]
MasteryEventType = Literal[
    "first_use", "success", "independent_use", "difficulty_up", "transfer", "mastered", "regression",
]

ExperimentResult = Literal["STRONG_SUCCESS", "WEAK_SUCCESS", "EXECUTED_ONLY", "FAILED", "UNKNOWN"]
TargetFunction = Literal["START", "STAY", "RETURN", "EMOTION_REGULATION"]

SKILL_TARGET_FUNCTIONS: dict[str, TargetFunction] = {
    "bad_draft": "START", "bad_first_step": "START", "open_only": "START",
    "open_without_timer": "START", "one_visible_step": "START", "visible_next_step": "START",
    "task_naming": "START", "name_task_one_word": "START",
    "one_tab_focus": "STAY", "phone_far_3min": "STAY", "phone_away_3_min": "STAY",
    "consolidation_hold_3min": "STAY", "consolidation_remove_obstacle": "STAY",
    "restart_after_slip": "RETURN", "restart_after_break": "RETURN",
    "consolidation_easy_return": "RETURN",
    "body_first": "EMOTION_REGULATION", "body_before_task": "EMOTION_REGULATION",
    "one_breath": "EMOTION_REGULATION", "crisis_grounding": "EMOTION_REGULATION",
}


def target_function_for_skill(skill_id: str) -> TargetFunction:
    return SKILL_TARGET_FUNCTIONS.get(str(skill_id or ""), "START")


def classify_experiment_result(
    *, completed: bool | None, subjective_effect: str | None = None,
    after_action: str | bool | None = None,
) -> ExperimentResult:
    """Classify evidence without treating mere execution as effectiveness."""
    if completed is False:
        return "FAILED"
    if completed is not True:
        return "UNKNOWN"
    effect = str(subjective_effect or "").strip().lower()
    after = str(after_action or "").strip().lower()
    if after_action is True:
        after = "continued_target_task"
    elif after_action is False:
        # A boolean from old records cannot distinguish stop from switching.
        after = "no_continuation"
    if after in {"unknown", "пока не знаю", "", "none"}:
        return "UNKNOWN"
    positive = effect in {"helped", "a_little", "some", "помогло", "немного"}
    if after in {"continued_target_task", "continued", "продолжил задачу"}:
        return "STRONG_SUCCESS" if positive else "EXECUTED_ONLY"
    if after in {"stopped_after_step", "stopped", "остановился после шага"}:
        return "WEAK_SUCCESS" if positive else "EXECUTED_ONLY"
    return "EXECUTED_ONLY"


def experiment_feedback(result: ExperimentResult, *, subjective_effect: str = "", after_action: str = "") -> str:
    if result == "STRONG_SUCCESS":
        return "Похоже, этот вход сработал: после микрошага ты продолжил задачу.\nЗапишем это как положительный сигнал, но проверим ещё раз позже."
    if result == "WEAK_SUCCESS":
        return "Сам микро-шаг дал некоторый эффект, но дальше ты остановился.\nЗначит, запуск стал легче, но удержание в задаче пока остаётся отдельной проблемой."
    if result == "FAILED":
        return "Этот вариант сейчас не зашёл.\nНе будем давить тем же способом — попробуем другой вход."
    if result == "UNKNOWN":
        return "Эксперимент выполнен, но пока мало данных, чтобы понять эффект.\nОставим результат неопределённым."
    after = after_action.lower()
    effect = subjective_effect.lower()
    if after in {"did_something_else", "сделал что-то другое"}:
        return "Начать действие получилось, но после него ты переключился на другую задачу.\nЗначит, проблема сейчас может быть не только во входе, но и в удержании внимания."
    if effect in {"did_not_help", "not_helped", "не помогло"}:
        return "Действие выполнено, но заметного эффекта не было.\nНе будем объявлять этот навык рабочим."
    return "Эксперимент выполнен, но пока нет признака, что он помог продолжить нужную задачу.\nНе считаем навык рабочим — просто сохраняем результат."


def skill_effectiveness(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Aggregate classified attempts; execution alone never increments success."""
    output: dict[str, dict[str, int]] = {}
    keys = {"STRONG_SUCCESS": "strong_successes", "WEAK_SUCCESS": "weak_successes",
            "EXECUTED_ONLY": "executed_only", "FAILED": "failures", "UNKNOWN": "unknown"}
    for record in records:
        sid = str(record.get("skill_id") or "")
        if not sid:
            continue
        row = output.setdefault(sid, {"attempts": 0, "strong_successes": 0, "weak_successes": 0,
                                      "executed_only": 0, "failures": 0, "unknown": 0})
        result = str(record.get("experiment_result") or classify_experiment_result(
            completed=record.get("completed"), subjective_effect=record.get("subjective_effect"),
            after_action=record.get("after_action")))
        row["attempts"] += 1
        row[keys.get(result, "unknown")] += 1
    return output


def recommended_target_function(records: Sequence[Mapping[str, Any]], *, wants_return: bool = False) -> TargetFunction:
    if wants_return:
        return "RETURN"
    starts = [r for r in records if str(r.get("target_function")) in {"START", "EMOTION_REGULATION"}]
    strong = sum(str(r.get("experiment_result")) == "STRONG_SUCCESS" for r in starts)
    lost = sum(str(r.get("after_action")) in {"stopped_after_step", "did_something_else"} for r in starts)
    return "STAY" if strong >= 1 and lost >= 2 else "START"


def choose_next_skill(
    candidate_skills: Sequence[Mapping[str, Any]], history: Sequence[Mapping[str, Any]],
    *, wants_return: bool = False,
) -> Mapping[str, Any]:
    """Choose by function and cooldown, relaxing only when every candidate is blocked."""
    target = recommended_target_function(history, wants_return=wants_return)
    recent_ids = {str(r.get("skill_id")) for r in history[-3:]}
    blocked: set[str] = set(recent_ids)
    for index, record in enumerate(history):
        sid = str(record.get("skill_id") or "")
        result = str(record.get("experiment_result") or "UNKNOWN")
        gap = len(history) - index - 1
        explicit_no = str(record.get("subjective_effect") or "") in {"did_not_help", "not_helped"}
        cooldown = 5 if result == "FAILED" or explicit_no else 3 if result == "EXECUTED_ONLY" else 1
        if sid and gap < cooldown:
            blocked.add(sid)
    matching = [s for s in candidate_skills if str(s.get("target_function")) == target]
    ordered = matching + [s for s in candidate_skills if s not in matching]
    for skill in ordered:
        if str(skill.get("skill_id")) not in blocked:
            return skill
    if not ordered:
        raise LookupError("No candidate skills")
    return {**ordered[0], "repeat_explanation_required": True}


def prioritize_mechanisms(user_selected_mechanism: str, model_inferred_mechanism: str) -> tuple[str, str | None]:
    """The explicit user answer owns the first experiment."""
    primary = user_selected_mechanism or model_inferred_mechanism
    secondary = model_inferred_mechanism if model_inferred_mechanism and model_inferred_mechanism != primary else None
    return primary, secondary


@dataclass(frozen=True)
class SkillMasteryState:
    user_id: int
    skill_id: str
    status: MasteryStatus = "NEW"
    current_difficulty: int = 1
    successful_practice_count: int = 0
    independent_use_count: int = 0
    generalized_contexts: tuple[str, ...] = ()
    failed_contexts: tuple[str, ...] = ()
    scaffolding_level: ScaffoldingLevel = "full"
    last_used_at: str = ""
    regression_flag: bool = False
    version: int = 1


@dataclass(frozen=True)
class LearningCriteria:
    minimum_successes: int
    independent_uses_for_generalizing: int = 1
    transfer_contexts_for_mastery: int = 1

    def __post_init__(self) -> None:
        if min(self.minimum_successes, self.independent_uses_for_generalizing, self.transfer_contexts_for_mastery) < 1:
            raise ValueError("Learning criteria must be positive")


@dataclass(frozen=True)
class LearningSignal:
    experiment_id: int
    context_domain: str
    attempted: bool = True
    successful: bool = False
    independent: bool = False
    used_without_prompt: bool = False
    is_new_context: bool = False
    failure_reason_code: str | None = None
    regression: bool = False
    occurred_at: str = ""


@dataclass(frozen=True)
class MasteryEvent:
    event_type: MasteryEventType
    experiment_id: int
    from_status: MasteryStatus
    to_status: MasteryStatus
    context_domain: str


@dataclass(frozen=True)
class LearningUpdate:
    state: SkillMasteryState
    events: tuple[MasteryEvent, ...]


def initial_mastery(user_id: int, skill_id: str, *, difficulty: int = 1) -> SkillMasteryState:
    if difficulty not in range(1, 6):
        raise ValueError("difficulty must be within 1..5")
    return SkillMasteryState(user_id, skill_id, current_difficulty=difficulty)


def criteria_from_skill(skill: Skill) -> LearningCriteria:
    """Use the reviewed card's objective threshold; never invent it with an LLM."""
    return LearningCriteria(minimum_successes=skill.minimum_successes)


def _event(kind: MasteryEventType, signal: LearningSignal, old: MasteryStatus, new: MasteryStatus) -> MasteryEvent:
    return MasteryEvent(kind, signal.experiment_id, old, new, signal.context_domain)


def apply_learning_signal(
    state: SkillMasteryState, signal: LearningSignal, criteria: LearningCriteria,
) -> LearningUpdate:
    """Advance only from observable attempts; failure never erases prior mastery counts."""
    if signal.experiment_id <= 0 or not signal.context_domain.strip():
        raise ValueError("A learning signal requires experiment evidence and context")
    old_status = state.status
    status: MasteryStatus = state.status
    successes = state.successful_practice_count
    independent_uses = state.independent_use_count
    generalized = list(state.generalized_contexts)
    failed = list(state.failed_contexts)
    scaffolding: ScaffoldingLevel = state.scaffolding_level
    regression_flag = state.regression_flag
    difficulty = state.current_difficulty
    events: list[MasteryEvent] = []

    if signal.regression and state.status == "MASTERED":
        status = "PRACTICING"
        scaffolding = "reduced"
        regression_flag = True
        events.append(_event("regression", signal, old_status, status))
    else:
        if signal.attempted and state.status == "NEW":
            status = "LEARNING"
            events.append(_event("first_use", signal, old_status, status))
        if signal.successful:
            successes += 1
            events.append(_event("success", signal, old_status, status))
            if successes == 1 and status != "MASTERED":
                scaffolding = "reduced"
            if successes >= criteria.minimum_successes and status != "MASTERED":
                if status == "LEARNING":
                    status = "PRACTICING"
                scaffolding = "minimal"
                if difficulty < 5:
                    difficulty += 1
                    events.append(_event("difficulty_up", signal, old_status, status))
        if signal.successful and signal.independent:
            independent_uses += 1
            events.append(_event("independent_use", signal, old_status, status))
            if independent_uses >= criteria.independent_uses_for_generalizing and status in {"LEARNING", "PRACTICING"}:
                status = "GENERALIZING"
                scaffolding = "none"
        if signal.successful and signal.independent and signal.is_new_context:
            if signal.context_domain not in generalized:
                generalized.append(signal.context_domain)
                events.append(_event("transfer", signal, old_status, "GENERALIZING"))
            if (
                len(generalized) >= criteria.transfer_contexts_for_mastery
                and signal.used_without_prompt
            ):
                status = "MASTERED"
                scaffolding = "none"
                regression_flag = False
                events.append(_event("mastered", signal, old_status, status))
        if not signal.successful and signal.failure_reason_code:
            if signal.context_domain not in failed:
                failed.append(signal.context_domain)

    updated = replace(
        state, status=status, current_difficulty=difficulty,
        successful_practice_count=successes, independent_use_count=independent_uses,
        generalized_contexts=tuple(generalized), failed_contexts=tuple(failed),
        scaffolding_level=scaffolding, last_used_at=signal.occurred_at or state.last_used_at,
        regression_flag=regression_flag, version=state.version + 1,
    )
    # Ensure every event reflects the final transition reached by this objective signal.
    normalized = tuple(replace(event, to_status=status) for event in events)
    return LearningUpdate(updated, normalized)


def scaffolding_instruction(state: SkillMasteryState, *, full: str, short: str, prompt: str) -> str:
    return {
        "full": full,
        "reduced": short,
        "minimal": prompt,
        "none": "Примени навык самостоятельно; я только зафиксирую результат.",
    }[state.scaffolding_level]


def regression_message() -> str:
    return (
        "Похоже, сейчас снова нужна небольшая опора. Это не потеря навыка и не наказание — "
        "на время вернём короткую подсказку и проверим навык в следующей похожей ситуации."
    )
