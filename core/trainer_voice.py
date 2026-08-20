"""Deterministic presentation layer for trainer personas.

This module receives already-decided facts.  It may vary wording, never outcomes,
mechanisms, skill instructions, or next-action policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

Trainer = Literal["skinny", "marsha", "beck"]
MessageType = Literal[
    "hypothesis", "skill_instruction", "experiment_result", "failure", "stuck",
    "morning", "evening", "return", "summary", "offer_transition",
]


@dataclass(frozen=True)
class VoiceContent:
    message_type: MessageType
    result: str = "UNKNOWN"
    target_function: str = "START"
    skill_name: str = ""
    facts: Mapping[str, Any] = field(default_factory=dict)
    core_message: str = ""
    next_action: str = ""
    core_instruction: str = ""


@dataclass(frozen=True)
class RenderedVoice:
    text: str
    variant_id: str
    result: str
    facts: Mapping[str, Any]


RESULT_TEMPLATES: dict[str, dict[str, tuple[str, ...]]] = {
    "skinny": {
        "STRONG_SUCCESS": (
            "Есть результат: после шага ты продолжил целевую задачу. Этот вход оставляем кандидатом. Один раз — ещё не закономерность.",
            "Факт: микрошаг перешёл в работу над задачей. Сохраняем этот вход для повторной проверки.",
        ),
        "WEAK_SUCCESS": (
            "Шаг сделал. Дальше остановился. Старт упростили, удержание — нет. Следующий тест будет на удержание.",
            "Начать получилось, остаться в задаче — нет. Теперь проверяем STAY.",
        ),
        "EXECUTED_ONLY": (
            "Инструкцию выполнил, но к нужной задаче это не вернуло. Навык не подтверждён. Идём другим путём.",
            "Действие сделано. Продолжения задачи нет. Не засчитываем результат как рабочий.",
        ),
        "FAILED": ("Не пошло. Не давим тем же способом. Уменьшаем шаг.", "Шаг не выполнен. Не повторяем давление. Берём другой вход."),
        "UNKNOWN": ("Данных пока мало. Вывод не делаем. Нужна ещё одна проверка.",),
    },
    "marsha": {
        "STRONG_SUCCESS": (
            "Похоже, этот маленький вход действительно дал хороший сигнал: после него ты продолжил целевую задачу. Запомним наблюдение без нового обязательства и позже проверим ещё раз.",
            "После микрошага получилось остаться с нужной задачей. Это положительный факт, но одной попытки пока недостаточно для устойчивого вывода.",
        ),
        "WEAK_SUCCESS": (
            "Начать получилось, но дальше ты остановился. Это не обнуляет попытку: вход стал чуть легче, а оставаться в задаче всё ещё трудно. Следующий шаг лучше подобрать именно под удержание.",
            "Микрошаг состоялся, а продолжения не было. Не будем делать из этого провал — теперь точнее проверим удержание в задаче.",
        ),
        "EXECUTED_ONLY": (
            "Ты выполнил эксперимент, но эффекта для нужной задачи не было. Не будем спорить с этим результатом или считать навык рабочим. Просто выберем другой вариант.",
            "Действие получилось выполнить, однако продолжения целевой задачи не произошло. Так бывает; оставим навык неподтверждённым и попробуем другой.",
        ),
        "FAILED": (
            "Похоже, этот шаг сейчас оказался слишком дорогим. Это данные, а не оценка тебя. Не будем заставлять себя повторять то же самое — уменьшим требование.",
            "Сейчас действие не получилось выполнить. Не добавляем к этому самокритику; следующий вход сделаем меньше или иначе.",
        ),
        "UNKNOWN": ("Эксперимент состоялся, но данных для вывода пока мало. Оставим результат открытым и вернёмся к нему позже.",),
    },
    "beck": {
        "STRONG_SUCCESS": (
            "В этой попытке есть положительный сигнал. После микрошага ты не только начал, но и продолжил целевую задачу. Это отличает результат от простого выполнения инструкции. Для вывода нужна повторная проверка.",
            "Данные одной попытки показывают продолжение целевой задачи после микрошага. Функциональный эффект здесь есть, но воспроизводимость ещё не установлена.",
        ),
        "WEAK_SUCCESS": (
            "Здесь важно разделить запуск и удержание. Микрошаг выполнить удалось, но продолжения задачи не произошло. Вмешательство могло снизить барьер START, тогда как проблема STAY остаётся. Следующий эксперимент логичнее направить туда.",
            "Мы получили выполнение без устойчивого продолжения. Это положительный сигнал для запуска, но не для удержания внимания. Рабочая гипотеза теперь касается STAY.",
        ),
        "EXECUTED_ONLY": (
            "Эксперимент выполнен технически, но функционального эффекта нет. Действие произошло без продолжения целевой задачи. Здесь важно различить исполнение инструкции и её результат. Интервенция остаётся неподтверждённой.",
            "Данные показывают выполнение действия, но не изменение поведения в нужной задаче. Поэтому исполнение нельзя приравнять к эффекту. Запишем отрицательный результат без причинного вывода.",
        ),
        "FAILED": (
            "Рабочая гипотеза или размер вмешательства сейчас не совпали с барьером. Повторение того же шага даст мало новой информации. Лучше изменить гипотезу или уменьшить действие.",
            "Действие не было выполнено. Это не позволяет оценить функциональный эффект навыка, но указывает, что текущий вход слишком дорог или неточен. Следующий тест должен отличаться.",
        ),
        "UNKNOWN": ("Сейчас недостаточно наблюдений для классификации эффекта. Отделим отсутствие данных от отрицательного результата и не будем делать причинный вывод.",),
    },
}

STUCK_TEXT = {
    "skinny": "Стоп. Самобичевание сейчас ничего не решает. Что произошло фактически: не начал, отвлёкся или бросил после старта?",
    "marsha": "Похоже, сейчас включилась сильная самокритика. Для тренировки полезнее отделить оценку себя от события. Ты не начал, отвлёкся или начал и потом остановился?",
    "beck": "Сейчас в этой фразе есть оценка себя, но для анализа нужен факт поведения. Что произошло перед остановкой: ты не начал, переключился после старта или столкнулся с тревогой во время задачи?",
}


def _trainer(value: str) -> Trainer:
    return value if value in {"skinny", "marsha", "beck"} else "marsha"  # type: ignore[return-value]


def _choose_variant(options: Sequence[str], prefix: str, recent: Sequence[str]) -> tuple[str, str]:
    leads = ("", "По фактам: ", "Короткий вывод: ", "В этой попытке: ", "Записываю: ", "Текущий результат: ")
    expanded = tuple(
        ((lead + text[0].lower() + text[1:]) if lead and text else text)
        for text in options for lead in leads
    )
    for index, text in enumerate(expanded):
        variant_id = f"{prefix}:{index}"
        if variant_id not in recent[-5:]:
            return text, variant_id
    index = len(recent) % len(expanded)
    return expanded[index], f"{prefix}:{index}"


def render_message(
    trainer: str, content: VoiceContent, *, recent_variant_ids: Sequence[str] = (),
) -> RenderedVoice:
    """Render immutable facts in a persona voice without re-deciding them."""
    persona = _trainer(trainer)
    if content.message_type in {"experiment_result", "failure"}:
        options = RESULT_TEMPLATES[persona].get(content.result, RESULT_TEMPLATES[persona]["UNKNOWN"])
    elif content.message_type == "stuck":
        options = (STUCK_TEXT[persona],)
    elif content.message_type == "skill_instruction":
        instruction = content.core_instruction or str(content.facts.get("instruction") or "")
        if persona == "skinny":
            options = (f"{content.skill_name}. {instruction} Не исправляй. Готово — отмечай.",)
        elif persona == "beck":
            options = (f"Проверим рабочую гипотезу небольшим действием. {instruction} Пока не редактируй результат; затем отдельно оценим эффект.",)
        else:
            options = (f"Сейчас не нужно делать хорошо. {instruction} Это только вход в задачу, и на нём уже можно остановиться.",)
    elif content.message_type == "summary" and content.target_function == "STAY":
        strong = str(content.facts.get("strong_skill") or "первый навык")
        weak = str(content.facts.get("unconfirmed_skill") or "второй навык")
        options = {
            "skinny": (f"Сегодня: {strong} дал продолжение задачи; {weak} не удержал её. Старт уже получается. Теперь тренируем удержание.",),
            "marsha": (f"Сегодня через «{strong}» получилось продолжить задачу, а «{weak}» пока не удержал внимание. Завтра не будем снова учить старту — лучше потренируем удержание.",),
            "beck": (f"Сегодня данные разделяют две функции. «{strong}» дал положительный сигнал для START, а «{weak}» не подтвердил STAY. Рабочая гипотеза смещается к удержанию внимания.",),
        }[persona]
    else:
        core = content.core_message or content.next_action or "Продолжим по фактам текущего шага."
        frames = {
            "skinny": {
                "hypothesis": "Рабочая версия. {core} Проверяем действием.",
                "morning": "План на сейчас. {core}", "evening": "Итог дня. {core}",
                "return": "Возвращаемся. {core}", "offer_transition": "Следующий вариант. {core}",
            },
            "marsha": {
                "hypothesis": "Похоже, здесь стоит проверить одну версию. {core}",
                "morning": "Начнём без лишнего давления. {core}", "evening": "Посмотрим на сегодняшний опыт без оценки. {core}",
                "return": "Можно вернуться без наказания за паузу. {core}", "offer_transition": "Если захочешь продолжить, вот доступный следующий формат. {core}",
            },
            "beck": {
                "hypothesis": "Рабочая гипотеза, а не установленный факт: {core}",
                "morning": "Определим проверяемую цель на сегодня. {core}", "evening": "Суммируем только наблюдаемые данные. {core}",
                "return": "Разделим факт паузы и следующий проверяемый шаг. {core}", "offer_transition": "Следующий формат меняет объём поддержки, но не факты экспериментов. {core}",
            },
        }
        template = frames[persona].get(content.message_type, "{core}")
        options = (template.format(core=core),)
    text, variant_id = _choose_variant(options, f"{persona}:{content.message_type}:{content.result}", recent_variant_ids)
    return RenderedVoice(text, variant_id, content.result, dict(content.facts))


def generative_renderer_contract(trainer: str, content: VoiceContent) -> str:
    """Safe prompt contract for a future generative renderer."""
    return (
        "FACTS THAT MUST NOT CHANGE\n"
        f"result={content.result}; target_function={content.target_function}; facts={dict(content.facts)}\n"
        "Never invent emotion, causality, improvement, or skill success.\n"
        f"STYLE INSTRUCTIONS\ntrainer={_trainer(trainer)}; preserve every fact and classification."
    )
