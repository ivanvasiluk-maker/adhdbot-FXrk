"""PATCH-14: objective skill mastery and progressive removal of AI scaffolding."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Literal, Mapping, Sequence

from core.skill_schema import Skill

MasteryStatus = Literal["NEW", "LEARNING", "PRACTICING", "GENERALIZING", "MASTERED"]
ScaffoldingLevel = Literal["full", "reduced", "minimal", "none"]
ExperimentResult = Literal["STRONG_SUCCESS", "WEAK_SUCCESS", "EXECUTED_ONLY", "FAILED", "UNKNOWN"]
TargetFunction = Literal["START", "STAY", "RETURN", "EMOTION_REGULATION"]
SubjectiveEffect = Literal["helped", "a_little", "did_not_help", "unknown"]
AfterAction = Literal["continued_target_task", "stopped_after_step", "did_something_else", "unknown"]
MasteryEventType = Literal[
    "first_use", "success", "independent_use", "difficulty_up", "transfer", "mastered", "regression",
]


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


@dataclass(frozen=True)
class ExperimentEvidence:
    """The minimum evidence used for learning and recommendation decisions."""

    skill_id: str
    completed: bool | None
    subjective_effect: SubjectiveEffect | None = None
    after_action: AfterAction | None = None
    target_function: TargetFunction = "START"

    @property
    def result(self) -> ExperimentResult:
        return classify_experiment_result(
            completed=self.completed,
            subjective_effect=self.subjective_effect,
            after_action=self.after_action,
        )


@dataclass(frozen=True)
class SkillEffectiveness:
    skill_id: str
    attempts: int = 0
    strong_successes: int = 0
    weak_successes: int = 0
    executed_only: int = 0
    failures: int = 0
    unknown: int = 0


def classify_experiment_result(
    *, completed: bool | None, subjective_effect: str | None = None, after_action: str | None = None,
) -> ExperimentResult:
    """Classify evidence conservatively: execution alone is never success."""
    if completed is False:
        return "FAILED"
    if completed is not True:
        return "UNKNOWN"
    if after_action in {None, "unknown"}:
        return "UNKNOWN"
    positive_effect = subjective_effect in {"helped", "a_little"}
    if after_action == "continued_target_task" and positive_effect:
        return "STRONG_SUCCESS"
    if after_action == "stopped_after_step" and positive_effect:
        return "WEAK_SUCCESS"
    if after_action == "did_something_else" or subjective_effect == "did_not_help":
        return "EXECUTED_ONLY"
    # Completion without positive evidence of target-task continuation is not success.
    return "EXECUTED_ONLY"


def experiment_feedback(result: ExperimentResult, *, subjective_effect: str | None = None,
                        after_action: str | None = None) -> str:
    if result == "STRONG_SUCCESS":
        return ("Похоже, этот вход сработал: после микрошага ты продолжил задачу.\n"
                "Запишем это как положительный сигнал, но проверим ещё раз позже.")
    if result == "WEAK_SUCCESS":
        return ("Сам микро-шаг дал некоторый эффект, но дальше ты остановился.\n"
                "Значит, запуск стал легче, но удержание в задаче пока остаётся отдельной проблемой.")
    if result == "FAILED":
        return "Этот вариант сейчас не зашёл.\nНе будем давить тем же способом — попробуем другой вход."
    if result == "UNKNOWN":
        return ("Эксперимент выполнен, но пока мало данных, чтобы понять эффект.\n"
                "Оставим результат неопределённым.")
    if subjective_effect == "did_not_help":
        return "Действие выполнено, но заметного эффекта не было.\nНе будем объявлять этот навык рабочим."
    if after_action == "did_something_else":
        return ("Начать действие получилось, но после него ты переключился на другую задачу.\n"
                "Значит, проблема сейчас может быть не только во входе, но и в удержании внимания.")
    return ("Эксперимент выполнен, но пока нет признака, что он помог продолжить нужную задачу.\n"
            "Не считаем навык рабочим — просто сохраняем результат.")


def skill_effectiveness(history: Iterable[ExperimentEvidence], skill_id: str) -> SkillEffectiveness:
    counts = {key: 0 for key in ("strong_successes", "weak_successes", "executed_only", "failures", "unknown")}
    attempts = 0
    for evidence in history:
        if evidence.skill_id != skill_id:
            continue
        attempts += 1
        key = {
            "STRONG_SUCCESS": "strong_successes", "WEAK_SUCCESS": "weak_successes",
            "EXECUTED_ONLY": "executed_only", "FAILED": "failures", "UNKNOWN": "unknown",
        }[evidence.result]
        counts[key] += 1
    return SkillEffectiveness(skill_id=skill_id, attempts=attempts, **counts)


def recommended_target_function(history: Sequence[ExperimentEvidence], *, wants_to_return: bool = False) -> TargetFunction:
    if wants_to_return:
        return "RETURN"
    start_successes = sum(item.target_function == "START" and item.result == "STRONG_SUCCESS" for item in history)
    lost_after_start = sum(
        item.target_function == "START" and item.completed is True
        and item.after_action in {"stopped_after_step", "did_something_else"}
        for item in history
    )
    return "STAY" if start_successes >= 1 and lost_after_start >= 2 else "START"


def skill_cooldown_remaining(history: Sequence[ExperimentEvidence], skill_id: str) -> int:
    """Return how many *other* experiments must happen before this skill may repeat."""
    for offset, item in enumerate(reversed(history)):
        if item.skill_id != skill_id:
            continue
        required = 5 if item.result == "FAILED" or item.subjective_effect == "did_not_help" else 3
        if item.result == "STRONG_SUCCESS":
            required = 3
        return max(0, required - offset)
    return 0


def choose_next_skill(
    available_skills: Mapping[str, TargetFunction], history: Sequence[ExperimentEvidence],
    *, wants_to_return: bool = False,
) -> str:
    """Choose by target function, recent-use window, and result-dependent cooldown."""
    if not available_skills:
        raise LookupError("No available skills")
    target = recommended_target_function(history, wants_to_return=wants_to_return)
    recent = {item.skill_id for item in history[-3:]}
    eligible = [sid for sid, function in available_skills.items()
                if function == target and sid not in recent and skill_cooldown_remaining(history, sid) == 0]
    if not eligible:
        eligible = [sid for sid in available_skills
                    if sid not in recent and skill_cooldown_remaining(history, sid) == 0]
    if not eligible:  # Exhausted libraries may repeat, but never the immediately previous skill when alternatives exist.
        previous = history[-1].skill_id if history else None
        eligible = [sid for sid in available_skills if sid != previous] or list(available_skills)
    return eligible[0]


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
