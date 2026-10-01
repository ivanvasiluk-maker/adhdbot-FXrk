"""Deterministic presentation layer for trainer personas.

The renderer receives immutable, already-decided facts.  It never classifies an
experiment, ranks a skill, or updates learning state.
"""

from __future__ import annotations

from core.dialogue_ux import plain_text

from dataclasses import dataclass, field
from typing import Literal, Mapping, Sequence

from core.learning_engine import ExperimentResult, TargetFunction

Trainer = Literal["skinny", "marsha", "beck"]
MessageType = Literal[
    "hypothesis", "skill_instruction", "experiment_result", "failure", "stuck",
    "morning", "evening", "return", "summary", "offer_transition",
]


@dataclass(frozen=True)
class VoiceContent:
    """Decision-layer output: facts which the voice layer may not rewrite."""

    message_type: MessageType
    result: ExperimentResult | None = None
    target_function: TargetFunction | None = None
    skill_name: str = ""
    facts: Mapping[str, object] = field(default_factory=dict)
    core_message: str = ""
    next_action: str = ""


@dataclass(frozen=True)
class RenderedVoiceMessage:
    text: str
    template_id: str
    # Echoing these values makes fact-preservation auditable and easy to test.
    result: ExperimentResult | None
    target_function: TargetFunction | None


def experiment_result_content(
    *, result: ExperimentResult, target_function: TargetFunction, skill_name: str,
    completed: bool, effect: str, after_action: str,
) -> VoiceContent:
    messages = {
        "STRONG_SUCCESS": ("После микрошага пользователь продолжил целевую задачу.", "Повторить позже для проверки."),
        "WEAK_SUCCESS": ("Запуск стал легче, но пользователь не продолжил задачу.", "Проверить удержание в задаче."),
        "EXECUTED_ONLY": ("Инструкция выполнена, но продолжение целевой задачи не подтверждено.", "Выбрать другой тест."),
        "FAILED": ("Микроэксперимент не выполнен.", "Изменить вход или уменьшить шаг."),
        "UNKNOWN": ("Данных недостаточно для оценки эффекта.", "Оставить результат неопределённым."),
    }
    core, action = messages[result]
    return VoiceContent(
        "experiment_result", result, target_function, skill_name,
        {"completed": completed, "effect": effect, "after_action": after_action}, core, action,
    )


_RESULT_TEMPLATES: dict[Trainer, dict[ExperimentResult, tuple[str, ...]]] = {
    "skinny": {
        "STRONG_SUCCESS": ("После шага получилось продолжить дело. Запомним этот способ.", "Дело продолжилось. Можно повторить этот способ в другой день."),
        "WEAK_SUCCESS": ("Первый шаг получился, продолжение — нет. Посмотрим, что помешало.",),
        "EXECUTED_ONLY": ("Шаг выполнен. Продолжение дела не подтверждено.",),
        "FAILED": ("Шаг не получился. Что помешало?", "Повторять то же самое не нужно. Что остановило?"),
        "UNKNOWN": ("Пока неясно, как прошла попытка. Что получилось?",),
    },
    "marsha": {
        "STRONG_SUCCESS": ("После шага удалось продолжить дело. Сохраним этот результат — позже он может пригодиться.", "Продолжить получилось. Можно остановиться здесь и вернуться, когда понадобится помощь."),
        "WEAK_SUCCESS": ("Начать получилось, а продолжить пока нет. Первый шаг всё равно остаётся сделанным.",),
        "EXECUTED_ONLY": ("Действие выполнено. Продолжение пока не подтверждено — оставим результат без лишних выводов.",),
        "FAILED": ("Сейчас не получилось. Не будем настаивать на том же способе. Что помешало?",),
        "UNKNOWN": ("Пока не хватает ответа о результате. Можно рассказать своими словами.",),
    },
    "beck": {
        "STRONG_SUCCESS": ("После первого шага получилось продолжить дело. Это результат одной попытки; позже проверим, повторится ли он.", "В этой попытке после шага дело продолжилось. Пока одного случая мало, чтобы считать способ подходящим всегда."),
        "WEAK_SUCCESS": ("Первый шаг выполнен, продолжение не получилось. Следующий способ подберём для продолжения дела.",),
        "EXECUTED_ONLY": ("Исполнение шага есть, но нужный эффект пока не подтверждён. Это разные результаты.",),
        "FAILED": ("Действие не получилось выполнить. Причину пока не знаем. Что помешало?",),
        "UNKNOWN": ("Нет достаточных данных о результате. Что произошло после попытки?",),
    },
}


def _pick(options: tuple[str, ...], prefix: str, recent_template_ids: Sequence[str]) -> tuple[str, str]:
    blocked = set(recent_template_ids[-5:])
    for index, text in enumerate(options):
        template_id = f"{prefix}:{index}"
        if template_id not in blocked:
            return text, template_id
    # More than five identical events can exhaust a small template set. Rotate
    # deterministically instead of silently changing any facts.
    index = sum(1 for item in recent_template_ids[-5:] if item.startswith(prefix)) % len(options)
    return options[index], f"{prefix}:{index}"


def render_message(
    trainer: Trainer, content: VoiceContent, *, recent_template_ids: Sequence[str] = (),
) -> RenderedVoiceMessage:
    """Render style only; return the original decision fields unchanged."""
    if trainer not in _RESULT_TEMPLATES:
        raise ValueError(f"Unknown trainer: {trainer}")
    if content.message_type in {"experiment_result", "failure"}:
        result = content.result or "UNKNOWN"
        # Subjective attribution and observable task outcome are separate
        # variables.  "Не помогло" + "Продолжил задачу" must never be rewritten
        # as "к задаче не вернуло"; that contradiction was visible in the live
        # product and destroyed trust in the map.
        if (
            result == "EXECUTED_ONLY"
            and content.facts.get("after_action") == "continued_target_task"
            and content.facts.get("effect") == "did_not_help"
        ):
            options = {
                "skinny": (
                    "Ты продолжил задачу, но не считаешь, что помог именно этот навык. Разделяем факты: задача продолжилась, эффект навыка не подтверждён.",
                ),
                "marsha": (
                    "После шага ты продолжил задачу, но сам навык не почувствовал полезным. Сохраним оба факта и не будем приписывать навыку эффект без твоего подтверждения.",
                ),
                "beck": (
                    "Наблюдаемое продолжение задачи есть, но субъективная полезность навыка не подтверждена. Поэтому не связываем продолжение с вмешательством причинно.",
                ),
            }[trainer]
            text, template_id = _pick(options, f"{trainer}:{content.message_type}:continued_without_attribution", recent_template_ids)
            return RenderedVoiceMessage(plain_text(text), template_id, content.result, content.target_function)
        options = _RESULT_TEMPLATES[trainer][result]
        text, template_id = _pick(options, f"{trainer}:{content.message_type}:{result}", recent_template_ids)
        return RenderedVoiceMessage(plain_text(text), template_id, content.result, content.target_function)
    return _render_non_result(trainer, content, recent_template_ids)


def _render_non_result(trainer: Trainer, content: VoiceContent,
                       recent_template_ids: Sequence[str]) -> RenderedVoiceMessage:
    if content.message_type == "summary" and content.facts.get("start_result") == "STRONG_SUCCESS":
        start = str(content.facts.get("start_skill_name") or "навык запуска")
        stay = str(content.facts.get("stay_skill_name") or "навык удержания")
        options = {
            "skinny": (f"Сегодня «{start}» дал продолжение задачи. «{stay}» удержание не подтвердил. Старт уже получается. Теперь тренируем STAY.",),
            "marsha": (f"Сегодня через «{start}» получилось продолжить задачу. При этом «{stay}» пока не удержал тебя в ней. Завтра не будем снова учить старту — лучше потренируем STAY.",),
            "beck": (f"Сегодня данные разделяют две функции. «{start}» дал положительный сигнал для START: задача продолжилась. «{stay}» не подтвердил эффект для STAY. Рабочая гипотеза на завтра — смещение барьера к удержанию.",),
        }[trainer]
    elif content.message_type == "skill_instruction":
        instruction = str(content.facts.get("instruction") or content.core_message)
        options = {
            "skinny": (f"{content.skill_name}.\n{instruction}\nНе усложняй. Готово — отмечай.",),
            "marsha": (f"Сейчас не нужно делать всё хорошо.\n{instruction}\nНа этом уже можно остановиться.",),
            "beck": (f"Проверим рабочую гипотезу небольшим действием.\n{instruction}\nПосле него отдельно оценим выполнение и эффект.",),
        }[trainer]
    elif content.message_type == "stuck":
        options = {
            "skinny": ("На каком месте возникла трудность: начать, продолжить или вернуться после перерыва?",),
            "marsha": ("Что сейчас труднее: начать, продолжить или вернуться после перерыва?",),
            "beck": ("Уточним, где нужна помощь: начать дело, продолжить или вернуться после отвлечения?",),
        }[trainer]
    else:
        core = content.core_message or content.next_action
        options = {
            "skinny": (core,),
            "marsha": (f"{core}",),
            "beck": (f"{core}",),
        }[trainer]
    text, template_id = _pick(options, f"{trainer}:{content.message_type}", recent_template_ids)
    return RenderedVoiceMessage(plain_text(text), template_id, content.result, content.target_function)


def day_summary_content(*, start_skill_name: str, stay_skill_name: str) -> VoiceContent:
    return VoiceContent(
        "summary", target_function="STAY",
        facts={"start_result": "STRONG_SUCCESS", "stay_result": "EXECUTED_ONLY",
               "start_skill_name": start_skill_name, "stay_skill_name": stay_skill_name},
        core_message=(f"«{start_skill_name}» дал продолжение задачи для START; "
                      f"«{stay_skill_name}» не подтвердил эффект для STAY."),
        next_action="Сместить следующий эксперимент с START на STAY.",
    )

