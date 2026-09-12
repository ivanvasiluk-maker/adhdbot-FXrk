"""PATCH-14: objective skill mastery and progressive removal of AI scaffolding."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Literal, Mapping, Sequence

from core.skill_schema import Skill

MasteryStatus = Literal["NEW", "LEARNING", "PRACTICING", "GENERALIZING", "MASTERED"]
ScaffoldingLevel = Literal["full", "reduced", "minimal", "none"]
ExperimentResult = Literal["STRONG_SUCCESS", "WEAK_SUCCESS", "EXECUTED_ONLY", "FAILED", "UNKNOWN"]
TargetFunction = Literal["START", "STAY", "RETURN", "EMOTION_REGULATION"]
SubjectiveEffect = Literal["helped", "a_little", "did_not_help", "unknown"]
AfterAction = Literal["continued_target_task", "stopped_after_step", "did_something_else", "unknown"]
NodeStatus = Literal["green", "yellow", "red"]
Effect = Literal["positive", "none", "negative", "unknown"]
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
    # Added as optional, JSON-safe fields so old profile rows remain readable.
    state_effect: Effect | None = None
    task_effect: Effect | None = None
    returned_after_distraction: bool | None = None
    hypothesis_id: str | None = None
    user_feedback: str | None = None
    explicitly_changed: bool = False

    @property
    def result(self) -> ExperimentResult:
        return classify_experiment_result(
            completed=self.completed,
            subjective_effect=self.subjective_effect,
            after_action=self.after_action,
        )

    @property
    def normalized_state_effect(self) -> Effect:
        if self.state_effect:
            return self.state_effect
        return "positive" if self.subjective_effect in {"helped", "a_little"} else \
            "negative" if self.subjective_effect == "did_not_help" else "unknown"

    @property
    def normalized_task_effect(self) -> Effect:
        if self.task_effect:
            return self.task_effect
        if self.returned_after_distraction is not None:
            return "positive" if self.returned_after_distraction else "none"
        return "positive" if self.after_action == "continued_target_task" else \
            "none" if self.after_action in {"stopped_after_step", "did_something_else"} else "unknown"


@dataclass(frozen=True)
class FunctionalState:
    """One evidence-derived source for map, report and recommendation routing."""

    start: NodeStatus
    stay: NodeStatus
    return_: NodeStatus
    primary_problem: TargetFunction | None

    def status(self, node: TargetFunction) -> NodeStatus:
        return {"START": self.start, "STAY": self.stay, "RETURN": self.return_}.get(node, "yellow")


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
    state = derive_functional_state(history)
    return state.primary_problem or "START"


def _node_status(successes: int, failures: int) -> NodeStatus:
    if successes and failures:
        return "yellow"
    if failures:
        return "red"
    if successes:
        return "green"
    return "yellow"


def derive_functional_state(history: Sequence[ExperimentEvidence]) -> FunctionalState:
    """Derive START/STAY/RETURN solely from observable task outcomes.

    A same-day negative RETURN observation can therefore never be rendered green.
    The sequence is causal: completing a step confirms START, while what happened
    afterwards supplies separate STAY evidence.
    """
    counts = {node: [0, 0] for node in ("START", "STAY", "RETURN")}
    for item in history:
        if item.target_function == "RETURN":
            effect = item.normalized_task_effect
            counts["RETURN"][0 if effect == "positive" else 1] += effect in {"positive", "none", "negative"}
            continue
        if item.completed is True:
            counts["START"][0] += 1
        elif item.completed is False:
            counts["START"][1] += 1
        if item.completed is True and item.normalized_task_effect in {"positive", "none", "negative"}:
            counts["STAY"][0 if item.normalized_task_effect == "positive" else 1] += 1
    statuses = {node: _node_status(*counts[node]) for node in counts}
    # Prefer the earliest confirmed break in the chain; ties use evidence volume.
    red = [node for node in ("START", "STAY", "RETURN") if statuses[node] == "red"]
    primary = max(red, key=lambda node: sum(counts[node])) if red else None
    # Mixed evidence still needs routing: repeated failures at a later link are
    # more useful than retraining an already-green earlier link.
    if primary is None:
        mixed_failures = [node for node in ("START", "STAY", "RETURN")
                          if counts[node][1] >= 2 and statuses[node] == "yellow"]
        primary = max(mixed_failures, key=lambda node: counts[node][1]) if mixed_failures else None
    return FunctionalState(statuses["START"], statuses["STAY"], statuses["RETURN"], primary)  # type: ignore[arg-type]


def skill_cooldown_remaining(history: Sequence[ExperimentEvidence], skill_id: str) -> int:
    """Return how many *other* experiments must happen before this skill may repeat."""
    consecutive_task_failures = 0
    for item in reversed(history):
        if item.skill_id == skill_id and item.completed is True and item.normalized_task_effect in {"none", "negative"}:
            consecutive_task_failures += 1
        elif item.skill_id == skill_id:
            break
    last_index = max((index for index, item in enumerate(history) if item.skill_id == skill_id), default=-1)
    other_since = len(history) - last_index - 1
    if last_index >= 0 and history[last_index].explicitly_changed:
        return max(0, 3 - other_since)
    if consecutive_task_failures >= 2:
        return max(0, 4 - other_since)
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


def update_hypothesis_scores(scores: Mapping[str, float], observations: Sequence[str]) -> dict[str, float]:
    """Small deterministic evidence updater; newer contradictory signals decay old leaders."""
    aliases = {
        "fear_of_failure": ("страх", "ошиб", "оцен"), "unclear_next_step": ("непонят", "неяс", "следующ"),
        "low_energy": ("нет сил", "устал", "энерг"), "overload": ("перегруз", "слишком много"),
        "fast_reward_avoidance": ("скуч", "быстр", "отдач"), "distraction": ("телефон", "youtube", "отвл"),
    }
    result = {key: min(1.0, max(0.0, float(value))) for key, value in scores.items()}
    for observation in observations:
        low = observation.lower()
        matched = {key for key, tokens in aliases.items() if any(token in low for token in tokens)}
        for key in aliases:
            current = result.get(key, 0.0)
            result[key] = min(1.0, current + .25) if key in matched else max(0.0, current - .05)
    return result


def primary_hypothesis(scores: Mapping[str, float]) -> str | None:
    viable = [(float(score), key) for key, score in scores.items() if float(score) > 0]
    return max(viable)[1] if viable else None


def experiment_allowance(main_count: int, voluntary_count: int = 0, *, developer_mode: bool = False) -> str:
    if developer_mode:
        return "main"
    if main_count < 2:
        return "main"
    if voluntary_count < 1:
        return "voluntary"
    return "closed"


def correction_intent(value: str) -> Literal["confirm", "reject", "correct", "unclear"]:
    """Classify conclusion feedback before mutating the persisted user model."""
    text = " ".join(str(value or "").lower().replace("ё", "е").split()).strip(" .!?")
    confirms = {"все ок", "да", "точно", "верно", "в точку", "норм", "согласен", "именно так"}
    rejects = {"нет", "не так", "не попал", "неверно", "все не так", "не то"}
    if text in confirms:
        return "confirm"
    if text in rejects:
        return "reject"
    correction_markers = ("а не", "дело не", "вообще не", "на самом деле", "скорее", "проблема в")
    if len(text) >= 8 and any(marker in text for marker in correction_markers):
        return "correct"
    return "unclear"


def milestone_summary(day: int, history: Sequence[ExperimentEvidence], skill_names: Mapping[str, str] | None = None) -> str | None:
    if day not in {3, 7} or not history:
        return None
    state = derive_functional_state(history)
    names = skill_names or {}
    successful = next((names.get(e.skill_id, e.skill_id) for e in reversed(history) if e.result == "STRONG_SUCCESS"), None)
    unsupported = next((names.get(e.skill_id, e.skill_id) for e in reversed(history) if e.result in {"EXECUTED_ONLY", "FAILED"}), None)
    facts = [f"Главный текущий узел — {state.primary_problem}." if state.primary_problem else "Цепочка пока даёт смешанные данные."]
    if successful:
        facts.append(f"Подтверждённый эффект на задачу дал «{successful}».")
    if unsupported:
        facts.append(f"«{unsupported}» пока не подтвердил эффект на задачу.")
    lines = "\n".join(f"{i}. {fact}" for i, fact in enumerate(facts, 1))
    next_node = state.primary_problem or "START"
    return f"За последние дни мы узнали:\n{lines}\n\nСледующее, что проверяем:\n— {next_node}."


def evidence_from_dict(value: Mapping[str, Any]) -> ExperimentEvidence:
    """Read both new evidence and legacy action-event metadata safely."""
    completed = value.get("completed")
    after_action = str(value.get("after_action") or "unknown")
    target = str(value.get("target_function") or "START").upper()
    return ExperimentEvidence(
        skill_id=str(value.get("skill_id") or "unknown"),
        completed=completed if isinstance(completed, bool) else None,
        subjective_effect=str(value.get("subjective_effect") or "unknown"),  # type: ignore[arg-type]
        after_action=after_action if after_action in {"continued_target_task", "stopped_after_step", "did_something_else", "unknown"} else "unknown",  # type: ignore[arg-type]
        target_function=target if target in {"START", "STAY", "RETURN", "EMOTION_REGULATION"} else "START",  # type: ignore[arg-type]
        state_effect=value.get("state_effect") if value.get("state_effect") in {"positive", "none", "negative", "unknown"} else None,
        task_effect=value.get("task_effect") if value.get("task_effect") in {"positive", "none", "negative", "unknown"} else None,
        returned_after_distraction=value.get("returned_after_distraction") if isinstance(value.get("returned_after_distraction"), bool) else None,
        hypothesis_id=str(value.get("hypothesis_id") or "") or None,
        user_feedback=str(value.get("user_feedback") or "") or None,
        explicitly_changed=bool(value.get("explicitly_changed")),
    )


def learning_history(model: Mapping[str, Any] | None) -> list[ExperimentEvidence]:
    raw = (model or {}).get("experiments") or []
    return [evidence_from_dict(item) for item in raw if isinstance(item, Mapping)]


def update_learning_model(
    model: Mapping[str, Any] | None, evidence: ExperimentEvidence, *, observations: Sequence[str] = (), day: str = "",
) -> dict[str, Any]:
    """Persistable reducer for outcomes, cooldowns, hypotheses and daily state."""
    current = dict(model or {})
    raw_history = [dict(item) for item in current.get("experiments") or [] if isinstance(item, Mapping)]
    item = {
        "skill_id": evidence.skill_id, "completed": evidence.completed,
        "subjective_effect": evidence.subjective_effect or "unknown", "after_action": evidence.after_action or "unknown",
        "target_function": evidence.target_function, "state_effect": evidence.normalized_state_effect,
        "task_effect": evidence.normalized_task_effect, "returned_after_distraction": evidence.returned_after_distraction,
        "hypothesis_id": evidence.hypothesis_id, "user_feedback": evidence.user_feedback,
        "explicitly_changed": evidence.explicitly_changed,
    }
    raw_history.append(item)
    raw_history = raw_history[-100:]
    history = [evidence_from_dict(row) for row in raw_history]
    state = derive_functional_state(history)
    sequence = int(current.get("experiment_sequence") or 0) + 1
    skill_states = {key: dict(value) for key, value in (current.get("skill_states") or {}).items()
                    if isinstance(value, Mapping)}
    stats = skill_states.setdefault(evidence.skill_id, {})
    stats["attempts"] = int(stats.get("attempts") or 0) + 1
    stats["successful_state_effects"] = int(stats.get("successful_state_effects") or 0) + (evidence.normalized_state_effect == "positive")
    stats["successful_task_effects"] = int(stats.get("successful_task_effects") or 0) + (evidence.normalized_task_effect == "positive")
    if evidence.completed is True and evidence.normalized_task_effect in {"none", "negative"}:
        stats["consecutive_failures"] = int(stats.get("consecutive_failures") or 0) + 1
    else:
        stats["consecutive_failures"] = 0
    if int(stats["consecutive_failures"]) >= 2:
        stats["cooldown_until"] = sequence + 4
    if evidence.explicitly_changed:
        stats["cooldown_until"] = max(int(stats.get("cooldown_until") or 0), sequence + 3)
    scores = update_hypothesis_scores(current.get("hypothesis_scores") or {}, observations)
    daily_states = {key: value for key, value in (current.get("daily_states") or {}).items()}
    if day:
        daily_states[day] = {
            "START": state.start, "STAY": state.stay, "RETURN": state.return_,
            "primary_problem": state.primary_problem,
            "experiment_count": sum(1 for row in raw_history if row.get("day") == day) + 1,
        }
        item["day"] = day
    return {
        **current, "version": 1, "experiments": raw_history, "experiment_sequence": sequence,
        "skill_states": skill_states, "hypothesis_scores": scores,
        "primary_hypothesis": primary_hypothesis(scores), "daily_states": daily_states,
        "functional_state": {"START": state.start, "STAY": state.stay, "RETURN": state.return_,
                             "primary_problem": state.primary_problem},
    }


def skill_blocked_in_model(model: Mapping[str, Any] | None, skill_id: str) -> bool:
    current = model or {}
    state = (current.get("skill_states") or {}).get(skill_id) or {}
    return int(state.get("cooldown_until") or 0) > int(current.get("experiment_sequence") or 0)


def progressive_node_insight(node: str, failures: int) -> str:
    if failures >= 3:
        return f"У нас уже достаточно данных считать {node} главным текущим узлом."
    if failures == 2:
        return f"Это повторяется второй раз: {node} остаётся слабым звеном."
    return f"Первый сигнал: сейчас трудность возникает в {node}."


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
