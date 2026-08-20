"""Personal, non-diagnostic conclusions built from observable user evidence.

This module complements the learning engine: it does not classify experiment
results, select skills, or modify START/STAY/RETURN.  Its state is JSON-safe so
it can be stored inside the existing user profile.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence
from uuid import uuid4

from core.learning_engine import ExperimentResult

HypothesisStatus = Literal[
    "STRONG_HYPOTHESIS", "MODERATE_HYPOTHESIS", "WEAK_HYPOTHESIS", "UNKNOWN", "EVIDENCE_AGAINST",
]
PredictionStatus = Literal["UNTESTED", "SUPPORTED", "NOT_SUPPORTED", "INCONCLUSIVE"]

HYPOTHESIS_LABELS = {
    "fear_of_evaluation": "Страх оценки",
    "perfectionistic_standard": "Высокое требование к качеству",
    "overload": "Перегруз",
    "uncertainty": "Неопределённость",
    "attention_switching": "Переключение внимания",
    "low_energy": "Низкая энергия",
    "low_motivation": "Недостаток мотивации",
    "self_criticism": "Самокритика",
    "task_ambiguity": "Неясность задачи",
    "avoidance_of_emotion": "Выход из неприятного напряжения",
}

STATUS_UI = {
    "STRONG_HYPOTHESIS": "🟢 Основная",
    "MODERATE_HYPOTHESIS": "🟡 Возможная",
    "WEAK_HYPOTHESIS": "🟡 Возможная",
    "UNKNOWN": "⚪ Пока неизвестно",
    "EVIDENCE_AGAINST": "🔴 Данные скорее против",
}


@dataclass(frozen=True)
class HypothesisState:
    hypothesis_id: str
    label: str
    status: HypothesisStatus = "UNKNOWN"
    evidence_for: tuple[str, ...] = ()
    evidence_against: tuple[str, ...] = ()
    still_unknown: tuple[str, ...] = ()
    untested: bool = True
    supported_tests: int = 0
    unsupported_tests: int = 0
    last_updated: str = ""

    def __post_init__(self) -> None:
        if self.hypothesis_id not in HYPOTHESIS_LABELS:
            raise ValueError(f"Unknown hypothesis: {self.hypothesis_id}")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class PredictionState:
    prediction_id: str
    hypothesis_id: str
    prediction: str
    observable_outcome: str
    status: PredictionStatus = "UNTESTED"
    created_at: str = ""
    tested_at: str | None = None
    result: str | None = None

    def __post_init__(self) -> None:
        if self.hypothesis_id not in HYPOTHESIS_LABELS:
            raise ValueError(f"Unknown hypothesis: {self.hypothesis_id}")
        if not self.prediction.strip() or not self.observable_outcome.strip():
            raise ValueError("A prediction requires a testable observable outcome")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WorkingModelState:
    situation: str
    blockage_point: str
    typical_chain: tuple[str, ...]
    hypotheses: tuple[HypothesisState, ...]
    predictions: tuple[PredictionState, ...] = ()
    experiment_history: tuple[Mapping[str, str], ...] = ()
    next_experiment: str = ""
    updated_at: str = ""

    @property
    def primary(self) -> HypothesisState:
        return sorted(self.hypotheses, key=_rank, reverse=True)[0]

    def as_dict(self) -> dict:
        return asdict(self)


def model_from_dict(value: Mapping[str, Any]) -> WorkingModelState:
    """Restore a persisted model while validating every nested taxonomy value."""
    hypotheses = tuple(HypothesisState(
        hypothesis_id=str(item["hypothesis_id"]), label=str(item["label"]),
        status=item.get("status", "UNKNOWN"),
        evidence_for=tuple(item.get("evidence_for") or ()),
        evidence_against=tuple(item.get("evidence_against") or ()),
        still_unknown=tuple(item.get("still_unknown") or ()),
        untested=bool(item.get("untested", True)),
        supported_tests=int(item.get("supported_tests") or 0),
        unsupported_tests=int(item.get("unsupported_tests") or 0),
        last_updated=str(item.get("last_updated") or ""),
    ) for item in value.get("hypotheses") or ())
    predictions = tuple(PredictionState(
        prediction_id=str(item["prediction_id"]), hypothesis_id=str(item["hypothesis_id"]),
        prediction=str(item["prediction"]), observable_outcome=str(item["observable_outcome"]),
        status=item.get("status", "UNTESTED"), created_at=str(item.get("created_at") or ""),
        tested_at=item.get("tested_at"), result=item.get("result"),
    ) for item in value.get("predictions") or ())
    if not hypotheses:
        raise ValueError("A working model requires competing hypotheses")
    return WorkingModelState(
        situation=str(value.get("situation") or "текущая ситуация"),
        blockage_point=str(value.get("blockage_point") or "точка стопора уточняется"),
        typical_chain=tuple(value.get("typical_chain") or ()), hypotheses=hypotheses,
        predictions=predictions, experiment_history=tuple(value.get("experiment_history") or ()),
        next_experiment=str(value.get("next_experiment") or ""), updated_at=str(value.get("updated_at") or ""),
    )


def model_from_analysis(*, situation: str, blockage_point: str, pattern: str,
                        evidence: Sequence[str], next_experiment: str) -> WorkingModelState:
    """Convert the existing diagnosis output into a competing, testable model."""
    pattern = str(pattern or "").lower()
    if any(token in pattern for token in ("perfection", "оцен", "ошиб", "visibility")):
        ids = ("fear_of_evaluation", "overload", "attention_switching", "low_motivation")
    elif any(token in pattern for token in ("overload", "перегруз", "energy")):
        ids = ("overload", "task_ambiguity", "low_energy", "attention_switching")
    elif any(token in pattern for token in ("attention", "отвлеч", "reward")):
        ids = ("attention_switching", "overload", "avoidance_of_emotion", "low_energy")
    elif any(token in pattern for token in ("self_critic", "самокрит", "shame")):
        ids = ("self_criticism", "fear_of_evaluation", "avoidance_of_emotion", "overload")
    else:
        ids = ("task_ambiguity", "overload", "uncertainty", "attention_switching")
    facts = tuple(_clean(evidence))
    hypotheses = tuple(new_hypothesis(
        hypothesis_id, "STRONG_HYPOTHESIS" if index == 0 else
        "MODERATE_HYPOTHESIS" if index == 1 else "UNKNOWN",
        evidence=facts if index == 0 else (),
        unknown=("следующий эксперимент поможет различить эту гипотезу",),
    ) for index, hypothesis_id in enumerate(ids))
    primary = hypotheses[0]
    prediction = create_prediction(
        primary.hypothesis_id,
        f"Если рабочая гипотеза «{primary.label}» верна, выбранный микроэксперимент должен облегчить продолжение конкретной задачи.",
        "После микроэксперимента пользователь продолжит исходную задачу, а не только выполнит инструкцию.",
    )
    return WorkingModelState(
        _safe(situation), _safe(blockage_point), facts[:5], hypotheses, (prediction,),
        next_experiment=_safe(next_experiment), updated_at=_now(),
    )


def new_hypothesis(hypothesis_id: str, status: HypothesisStatus, *,
                   evidence: Sequence[str] = (), unknown: Sequence[str] = ()) -> HypothesisState:
    return HypothesisState(hypothesis_id, HYPOTHESIS_LABELS[hypothesis_id], status,
                           tuple(_clean(evidence)), (), tuple(_clean(unknown)), True,
                           last_updated=_now())


def create_prediction(hypothesis_id: str, prediction: str, observable_outcome: str) -> PredictionState:
    return PredictionState(str(uuid4()), hypothesis_id, prediction.strip(), observable_outcome.strip(),
                           created_at=_now())


def test_prediction(prediction: PredictionState, result: ExperimentResult, *, detail: str) -> PredictionState:
    status: PredictionStatus = {
        "STRONG_SUCCESS": "SUPPORTED", "WEAK_SUCCESS": "INCONCLUSIVE",
        "EXECUTED_ONLY": "NOT_SUPPORTED", "FAILED": "INCONCLUSIVE", "UNKNOWN": "INCONCLUSIVE",
    }[result]
    return replace(prediction, status=status, tested_at=_now(), result=_safe(detail))


def apply_experiment(hypothesis: HypothesisState, prediction: PredictionState,
                     result: ExperimentResult, *, experiment_name: str) -> HypothesisState:
    """Update only when a performed test produced an observable result.

    Execution alone and failure are deliberately inconclusive. Two performed,
    unsupported predictions reduce the hypothesis and allow a competitor to win.
    """
    if prediction.hypothesis_id != hypothesis.hypothesis_id:
        return hypothesis
    for_evidence, against = list(hypothesis.evidence_for), list(hypothesis.evidence_against)
    supported, unsupported = hypothesis.supported_tests, hypothesis.unsupported_tests
    if result == "STRONG_SUCCESS":
        for_evidence.append(f"После эксперимента «{_safe(experiment_name)}» наблюдаемый результат совпал с прогнозом: {_safe(prediction.observable_outcome)}.")
        supported += 1
    elif result == "EXECUTED_ONLY":
        against.append(f"В эксперименте «{_safe(experiment_name)}» действие выполнено, но прогнозируемое продолжение задачи не наблюдалось.")
        unsupported += 1
    # FAILED/UNKNOWN/WEAK_SUCCESS cannot establish that the mechanism is absent.
    status = hypothesis.status
    if supported and status in {"UNKNOWN", "WEAK_HYPOTHESIS", "MODERATE_HYPOTHESIS"}:
        status = "STRONG_HYPOTHESIS"
    if unsupported >= 2:
        status = "EVIDENCE_AGAINST"
    return replace(hypothesis, status=status, evidence_for=tuple(_clean(for_evidence)),
                   evidence_against=tuple(_clean(against)), untested=False,
                   supported_tests=supported, unsupported_tests=unsupported, last_updated=_now())


def update_model(model: WorkingModelState, prediction_id: str, result: ExperimentResult,
                 *, experiment_name: str, result_detail: str, next_experiment: str = "") -> WorkingModelState:
    predictions, hypotheses = [], list(model.hypotheses)
    for prediction in model.predictions:
        if prediction.prediction_id != prediction_id:
            predictions.append(prediction)
            continue
        predictions.append(test_prediction(prediction, result, detail=result_detail))
        hypotheses = [apply_experiment(item, prediction, result, experiment_name=experiment_name)
                      for item in hypotheses]
    history = model.experiment_history + ({"experiment": _safe(experiment_name),
                                            "result": result, "detail": _safe(result_detail)},)
    return replace(model, hypotheses=tuple(hypotheses), predictions=tuple(predictions),
                   experiment_history=history, next_experiment=_safe(next_experiment), updated_at=_now())


def update_next_untested_prediction(model: WorkingModelState, result: ExperimentResult, *,
                                    experiment_name: str, result_detail: str,
                                    next_experiment: str = "") -> WorkingModelState:
    """Apply feedback only to a real pending prediction; otherwise preserve the map."""
    prediction = next((item for item in model.predictions if item.status == "UNTESTED"), None)
    if prediction is None:
        return model
    return update_model(model, prediction.prediction_id, result, experiment_name=experiment_name,
                        result_detail=result_detail, next_experiment=next_experiment)


def render_short_conclusion(model: WorkingModelState) -> str:
    relevant = sorted(model.hypotheses, key=_rank, reverse=True)[:5]
    lines = ["📌 Что я пока вижу", "", _safe(model.blockage_point), "",
             "Сейчас вижу несколько возможных механизмов:"]
    for item in relevant:
        suffix = " — основная рабочая гипотеза." if item is model.primary else " — остаётся возможным."
        if item.status == "UNKNOWN": suffix = " — пока недостаточно данных."
        if item.status == "EVIDENCE_AGAINST": suffix = " — данные пока скорее против."
        lines.append(f"{STATUS_UI[item.status]}: {item.label}{suffix}")
    lines += ["", f"Сначала проверим: {model.next_experiment or model.primary.label}.",
              "Это пока не вывод — следующий эксперимент поможет различить рабочие гипотезы."]
    return "\n".join(lines)


def render_evidence(model: WorkingModelState) -> str:
    primary = model.primary
    evidence = primary.evidence_for or ("пока есть только описание конкретной ситуации",)
    return (f"📚 Почему сейчас рассматриваю «{primary.label}»:\n\n" +
            "\n".join(f"— {_safe(item)}" for item in evidence) +
            "\n\nЭто данные из твоего описания и проверок, а не общая лекция и не диагноз.")


def render_full_working_model(model: WorkingModelState, *, trainer_intro: str = "") -> str:
    primary, alternatives = model.primary, [h for h in sorted(model.hypotheses, key=_rank, reverse=True) if h != model.primary][:4]
    tested = model.experiment_history[-1] if model.experiment_history else None
    prediction = next((p for p in reversed(model.predictions) if p.status == "UNTESTED"), None)
    parts = ["🧭 Твоя рабочая модель", trainer_intro or "Не диагноз — рабочая схема.",
             f"\nИсходная ситуация:\n{_safe(model.situation)}",
             f"\nГде начинается стопор:\n{_safe(model.blockage_point)}",
             "\nТипичная цепочка:\n" + "\n→ ".join(_clean(model.typical_chain)),
             f"\nОсновная рабочая гипотеза:\n🟢 {primary.label}."]
    if primary.evidence_for:
        parts.append("\nЧто говорит в её пользу:\n" + "\n".join(f"— {x}" for x in primary.evidence_for))
    parts.append("\nАльтернативные гипотезы:\n" + "\n".join(
        f"{STATUS_UI[h.status]}: {h.label}. " + ("Пока недостаточно данных." if h.untested else "Данные обновлены экспериментом.")
        for h in alternatives))
    if tested:
        parts += [f"\nЧто проверили:\n{tested['experiment']}", f"\nЧто произошло:\n{tested['detail']}",
                  "\nПредварительный вывод:\nЭто первый поведенческий сигнал. Одна попытка не устанавливает механизм."]
    unknown = list(primary.still_unknown) or ["повторится ли наблюдаемый эффект в другой попытке"]
    parts.append("\nЧто пока неизвестно:\n" + "\n".join(f"— {x}" for x in unknown))
    if prediction:
        parts += [f"\n🔮 Если наша модель верна\n{prediction.prediction}",
                  f"Наблюдаемый результат: {prediction.observable_outcome}"]
    parts.append(f"\nСледующий эксперимент:\n{model.next_experiment or 'пока не выбран'}")
    return "\n".join(parts)


def _rank(item: HypothesisState) -> tuple[int, int, int]:
    level = {"STRONG_HYPOTHESIS": 4, "MODERATE_HYPOTHESIS": 3, "WEAK_HYPOTHESIS": 2,
             "UNKNOWN": 1, "EVIDENCE_AGAINST": 0}[item.status]
    return level, item.supported_tests - item.unsupported_tests, len(item.evidence_for)


def _clean(items: Sequence[str]) -> list[str]:
    return [value for item in items if (value := _safe(item))]


def _safe(value: str) -> str:
    return " ".join(str(value or "").split())[:500]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
