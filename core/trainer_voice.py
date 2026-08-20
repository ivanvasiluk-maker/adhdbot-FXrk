"""Deterministic presentation layer for trainer personas.

The renderer receives immutable, already-decided facts.  It never classifies an
experiment, ranks a skill, or updates learning state.
"""

from __future__ import annotations

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
        "STRONG_SUCCESS": (
            "Есть результат: после шага ты продолжил задачу. Этот вход оставляем для повторной проверки. Один раз — ещё не закономерность.",
            "Факт: микро-шаг привёл к продолжению задачи. Проверим этот вход позже. Пока без окончательных выводов.",
        ),
        "WEAK_SUCCESS": (
            "Шаг сделал. Дальше остановился. Старт упростили, удержание — нет. Следующий тест будет на удержание.",
            "Начало получилось, продолжения не было. Теперь проверяем STAY, а не ещё один старт.",
        ),
        "EXECUTED_ONLY": (
            "Инструкцию выполнил, но к целевой задаче это не вернуло. Навык не подтверждён. Идём другим путём.",
            "Действие сделано. Нужного продолжения нет. В рабочие этот способ не записываем.",
        ),
        "FAILED": ("Не пошло. Не давим тем же способом. Уменьшаем шаг.", "Шаг не выполнен. Не повторяем в лоб. Меняем вход."),
        "UNKNOWN": ("Данных мало. Результат пока неизвестен. Не додумываем.", "Эффект неясен. Оставляем без оценки."),
    },
    "marsha": {
        "STRONG_SUCCESS": (
            "Похоже, этот маленький вход дал хороший сигнал: после него ты продолжил задачу. Не нужно превращать его в новое обязательство — позже просто проверим ещё раз.",
            "После микрошага получилось остаться с целевой задачей. Запомним это как положительный сигнал, но не будем торопиться с окончательным выводом.",
        ),
        "WEAK_SUCCESS": (
            "Начать получилось, но дальше ты остановился. Это не обнуляет попытку: вход стал чуть легче, а оставаться в задаче всё ещё трудно. Следующий шаг лучше подобрать именно под удержание.",
            "Микро-шаг удалось сделать, а продолжить задачу — нет. Не будем делать из этого провал; теперь можно бережно проверить именно удержание.",
        ),
        "EXECUTED_ONLY": (
            "Ты сделал эксперимент, но он не вернул тебя к нужной задаче. Так бывает — не каждый навык подходит. Не будем считать его рабочим и попробуем другой.",
            "Действие выполнить удалось, но нужного эффекта не было. Не станем спорить с этим результатом — просто выберем другой вариант.",
        ),
        "FAILED": (
            "Похоже, этот шаг сейчас оказался слишком дорогим. Не будем заставлять себя повторять то же самое. Давай уменьшим требование.",
            "Сейчас выполнить этот вариант не получилось. Это данные, а не оценка тебя; следующий вход сделаем меньше.",
        ),
        "UNKNOWN": ("Пока недостаточно данных, чтобы понять эффект. Оставим результат открытым и не будем ничего приписывать.",),
    },
    "beck": {
        "STRONG_SUCCESS": (
            "В этой попытке есть положительный сигнал. После микрошага ты не только начал, но и продолжил целевую задачу. Это отличает результат от простого выполнения инструкции. Для вывода нужна повторная проверка.",
            "Данные этой попытки показывают продолжение целевой задачи после микрошага. Поэтому результат классифицирован как STRONG_SUCCESS, а не просто исполнение. По одной попытке закономерность устанавливать рано.",
        ),
        "WEAK_SUCCESS": (
            "Здесь важно разделить запуск и удержание. Микро-шаг выполнить удалось и стало немного легче, но продолжения задачи не произошло. Вмешательство могло снизить барьер START, тогда как STAY остаётся рабочей проблемой.",
            "Мы получили эффект для запуска, но не для удержания. Исполнение шага и продолжение задачи — разные показатели. Следующий эксперимент логичнее направить на STAY.",
        ),
        "EXECUTED_ONLY": (
            "Эксперимент выполнен технически, но функционального эффекта нет. Исполнение инструкции и продолжение целевой задачи — разные показатели. Поэтому навык остаётся неподтверждённым.",
            "Данные показывают execution без подтверждённого effect: действие сделано, нужного продолжения нет. Это не позволяет отнести навык к рабочим.",
        ),
        "FAILED": (
            "Размер или механизм вмешательства сейчас, возможно, не совпал с проблемой. Повторение того же шага даст мало новой информации. Лучше изменить гипотезу или уменьшить действие.",
            "Микроэксперимент не был выполнен. Это не подтверждает причинную гипотезу. Следующий тест должен изменить вход или его размер.",
        ),
        "UNKNOWN": ("Данных недостаточно, чтобы отделить исполнение от эффекта. Классификация остаётся UNKNOWN; причинный вывод делать рано.",),
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
        options = _RESULT_TEMPLATES[trainer][result]
        text, template_id = _pick(options, f"{trainer}:{content.message_type}:{result}", recent_template_ids)
        return RenderedVoiceMessage(text, template_id, content.result, content.target_function)
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
            "skinny": ("Стоп. Самобичевание ничего не решает. Что было фактически: не начал, отвлёкся или остановился после старта?",),
            "marsha": ("Похоже, сейчас много самокритики. Отделим оценку себя от факта: ты не начал, отвлёкся или начал и остановился?",),
            "beck": ("В этой фразе есть оценка себя, а для анализа нужен факт поведения. Ты не начал, переключился после старта или остановился уже во время задачи?",),
        }[trainer]
    else:
        core = content.core_message or content.next_action
        options = {
            "skinny": (core,),
            "marsha": (f"Похоже, сейчас важно следующее: {core}",),
            "beck": (f"Рабочая формулировка по текущим данным: {core}",),
        }[trainer]
    text, template_id = _pick(options, f"{trainer}:{content.message_type}", recent_template_ids)
    return RenderedVoiceMessage(text, template_id, content.result, content.target_function)


def day_summary_content(*, start_skill_name: str, stay_skill_name: str) -> VoiceContent:
    return VoiceContent(
        "summary", target_function="STAY",
        facts={"start_result": "STRONG_SUCCESS", "stay_result": "EXECUTED_ONLY",
               "start_skill_name": start_skill_name, "stay_skill_name": stay_skill_name},
        core_message=(f"«{start_skill_name}» дал продолжение задачи для START; "
                      f"«{stay_skill_name}» не подтвердил эффект для STAY."),
        next_action="Сместить следующий эксперимент с START на STAY.",
    )
