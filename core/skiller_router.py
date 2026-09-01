"""State-aware Day 1 interaction router.

This module deliberately has no Telegram dependency.  Telegram adapters pass an
action id (never a button caption) to :func:`route_callback`, and pass text or a
voice transcript to :func:`route_user_input`.  Keeping that boundary explicit
prevents callback captions from accidentally becoming diagnostic stories.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, MutableMapping


class DialogState(str, Enum):
    ONBOARDING = "ONBOARDING"
    DAY1_INTAKE = "DAY1_INTAKE"
    DAY1_CLARIFY = "DAY1_CLARIFY"
    DAY1_SUMMARY = "DAY1_SUMMARY"
    EXPERIMENT_ACTIVE = "EXPERIMENT_ACTIVE"
    EXPERIMENT_FEEDBACK = "EXPERIMENT_FEEDBACK"
    DAY_OPEN = "DAY_OPEN"
    DAY_CLOSED = "DAY_CLOSED"
    CRISIS_FLOW = "CRISIS_FLOW"
    NEW_CASE_INTAKE = "NEW_CASE_INTAKE"
    CORRECTION_INPUT = "CORRECTION_INPUT"
    OFFER = "OFFER"
    FULL_MODE = "FULL_MODE"


ACTIONS = {
    "day.finish", "diagnosis.full_report", "diagnosis.correct",
    "experiment.start", "experiment.next", "experiment.extra",
    "experiment.done", "experiment.result.promising", "experiment.result.partial",
    "experiment.result.no_effect", "experiment.result.negative",
    "map.today_insight", "map.full", "case.new", "resources.show",
    "offer.free", "offer.subscription", "offer.group", "offer.consultation",
    "offer.continue", "offer.later", "navigation.back", "crisis.procrastination",
    "experiment.extra.done",
}

CLARIFICATION_ANSWERS = {
    "clarify.entry.overload": ("entry_barrier", "overload"),
    "clarify.entry.fear": ("entry_barrier", "fear_of_evaluation"),
    "clarify.entry.unclear": ("entry_barrier", "unclear_start"),
    "clarify.entry.dislike": ("entry_barrier", "task_aversion"),
    "clarify.entry.distraction": ("entry_barrier", "distraction"),
    "clarify.overload.yes": ("overload_counterfactual", "yes"),
    "clarify.overload.somewhat": ("overload_counterfactual", "somewhat"),
    "clarify.overload.no": ("overload_counterfactual", "no"),
    "clarify.fear.reaction": ("fear_type", "reaction"),
    "clarify.fear.perfection": ("fear_type", "perfectionism"),
    "clarify.fear.shame": ("fear_type", "delay_shame"),
    "clarify.fear.unclear": ("fear_type", "unclear_message"),
}

def new_session(state: DialogState = DialogState.DAY1_INTAKE) -> dict[str, Any]:
    return {
        "state": state.value, "case_facts": [], "hypotheses": [], "structured_answers": [],
        "clarification_count": 0, "confidence": 0.25, "short_report": "", "full_report": "",
        "experiment_count": 0, "feedback_questions": 0, "processed_callbacks": [],
        "post_close_experiment_used": False, "post_close_skills": [],
        "skill_map": {"primary_pattern": "уточняется", "secondary_patterns": [],
                      "functional_bottleneck": "START", "successful_skills": [],
                      "partial_skills": [], "failed_skills": [], "observations": [],
                      "next_hypothesis": "уточнить точку стопора", "confidence": 0.25},
        "telemetry": [], "last_non_offer_state": state.value,
    }


def _state(s: MutableMapping[str, Any]) -> DialogState:
    try:
        return DialogState(s.get("state", DialogState.DAY1_INTAKE.value))
    except ValueError:
        return DialogState.DAY_OPEN


def _response(text: str, buttons: list[tuple[str, str]] | None = None, **extra: Any) -> dict[str, Any]:
    return {"text": text, "buttons": buttons or [], **extra}


def _question(session: MutableMapping[str, Any]) -> dict[str, Any]:
    answers = {a["question_id"]: a["answer"] for a in session["structured_answers"]}
    if not answers:
        return _response("Что здесь тяжелее всего?", [
            ("😵 Слишком много всего одновременно", "clarify.entry.overload"),
            ("😬 Боюсь ошибки / реакции", "clarify.entry.fear"),
            ("🌀 Не понимаю, с чего начать", "clarify.entry.unclear"),
            ("😑 Не хочу именно эту задачу", "clarify.entry.dislike"),
            ("📱 Постоянно переключаюсь", "clarify.entry.distraction"),
            ("✍️ Другое / скажу своими словами", "clarify.other"),
        ])
    barrier = answers.get("entry_barrier")
    if barrier == "overload" and "overload_counterfactual" not in answers:
        return _response("Если бы других задач сегодня почти не было, эта задача стала бы заметно легче?", [
            ("✅ Да", "clarify.overload.yes"), ("🤷 Немного", "clarify.overload.somewhat"),
            ("❌ Нет", "clarify.overload.no"), ("✍️ Другое / скажу своими словами", "clarify.other")])
    if barrier == "fear_of_evaluation" and "fear_type" not in answers:
        return _response("Что сильнее?", [("😬 Боюсь реакции", "clarify.fear.reaction"),
            ("🎯 Хочу сделать идеально", "clarify.fear.perfection"), ("🙈 Стыдно из-за задержки", "clarify.fear.shame"),
            ("🌀 Не знаю, что написать", "clarify.fear.unclear"), ("✍️ Другое / скажу своими словами", "clarify.other")])
    return _response("В какой точке чаще ломается выполнение?", [("🚪 До старта", "clarify.function.start"),
        ("🧷 После старта", "clarify.function.stay"), ("↩️ При возвращении", "clarify.function.return"),
        ("✍️ Другое / скажу своими словами", "clarify.other")])


def _reports(session: MutableMapping[str, Any]) -> None:
    fact = str((session.get("case_facts") or ["эта конкретная задача"])[-1])
    answers = {a["question_id"]: a["answer"] for a in session["structured_answers"]}
    primary = answers.get("entry_barrier", "перегруз и уход в быстрые альтернативы").replace("_", " ")
    bottleneck = answers.get("functional_bottleneck", "START").upper()
    session["hypotheses"] = [primary, "быстрое облегчение через переключение"]
    session["skill_map"].update(primary_pattern=primary, functional_bottleneck=bottleneck,
        secondary_patterns=["быстрое облегчение через переключение"], confidence=session["confidence"])
    short = ("📌 Твоя рабочая карта\n\nПохоже, проблема сейчас не в лени.\n\n"
        f"Сейчас складывается цепочка:\n{fact}\n↓\nнапряжение или перегруз\n↓\nизбегание / переключение\n↓\n"
        "краткое облегчение\n↓\nзадача становится тяжелее\n\n"
        f"Основная гипотеза:\n{primary}\n\nЧто уже хорошо:\nты замечаешь петлю и можешь описать конкретную ситуацию.\n\n"
        f"Где именно ломается выполнение:\n{bottleneck}\n\nПоэтому первым проверим:\nоткрыть задачу и сделать один видимый микрошаг.")
    full = ("📖 Подробный разбор\n\n1. Что происходит\n"
        f"Ты описал(а): {fact}. Контакт с этой ситуацией запускает напряжение, после чего проще переключиться.\n\n"
        f"2. Рабочий механизм\n{fact} → напряжение/перегруз → избегание → краткое облегчение → растущая цена задержки.\n\n"
        f"3. Что выглядит главным\n{primary}. Уверенность: {round(float(session['confidence']) * 100)}%.\n\n"
        "4. Что выглядит вторичным\nБыстрое переключение может поддерживать петлю, даже если не было исходной причиной.\n\n"
        "5. Что НЕ похоже на главную проблему\nТы способен(на) замечать и описывать задачу, поэтому полная потеря мотивации выглядит слабее.\n\n"
        f"6. Что у тебя сохранено\nОсознание конкретной ситуации сохранено; трудность сейчас сосредоточена в точке {bottleneck}.\n\n"
        "7. Что будем проверять\nОдин микрошаг покажет, снижается ли цена входа без требования закончить всё.\n\n"
        "8. Ограничение\nЭто рабочая модель, а не диагноз. Она будет обновляться по результатам экспериментов.")
    session["short_report"], session["full_report"] = short, full


def route_user_input(session: MutableMapping[str, Any], content: str, *, kind: str = "text") -> dict[str, Any]:
    """Route genuine user text/voice. Callback labels must never call this."""
    state = _state(session)
    content = content.strip()
    if state == DialogState.CORRECTION_INPUT:
        session.setdefault("corrections", []).append({"kind": kind, "text": content})
        session.setdefault("case_facts", []).append(f"Поправка пользователя: {content}")
        session["confidence"] = min(0.95, float(session.get("confidence", .25)) + .1)
        _reports(session); session["state"] = DialogState.DAY1_SUMMARY.value
        return _response("Спасибо. Обновил рабочую карту.\n\n" + session["short_report"], _summary_buttons())
    if state in {DialogState.DAY1_INTAKE, DialogState.NEW_CASE_INTAKE}:
        session.update(case_facts=[content], structured_answers=[], clarification_count=0,
                       confidence=.35, state=DialogState.DAY1_CLARIFY.value)
        return _question(session)
    if state == DialogState.DAY1_CLARIFY:
        session["structured_answers"].append({"question_id": "free_text_clarification", "answer": content, "kind": kind})
        session["clarification_count"] += 1; session["confidence"] += .15
        return _advance_clarification(session)
    if state == DialogState.CRISIS_FLOW:
        return _response("Понял. Сначала уменьшим нагрузку: назови один шаг на 30 секунд. Если в сообщении есть риск для жизни, включится отдельная safety-поддержка.")
    return _response("Записал это как наблюдение к текущей карте.")


def _summary_buttons() -> list[tuple[str, str]]:
    return [("🚀 Проверить навык", "experiment.start"), ("📖 Подробное заключение", "diagnosis.full_report"),
            ("✏️ Исправить вывод", "diagnosis.correct")]


def _advance_clarification(session: MutableMapping[str, Any]) -> dict[str, Any]:
    count = int(session["clarification_count"])
    if float(session["confidence"]) >= .65 or count >= 4:
        _reports(session); session["state"] = DialogState.DAY1_SUMMARY.value
        return _response(session["short_report"], _summary_buttons())
    return _question(session)


def _classify(session: MutableMapping[str, Any], classification: str) -> dict[str, Any]:
    skill = str(session.get("active_skill") or "Открыть без таймера")
    mapping = session["skill_map"]
    messages = {
        "PROMISING": "Есть хороший первый сигнал: после микрошага ты самостоятельно продолжил задачу. Пока отмечаю этот навык как вероятно полезный. Проверим ещё раз позже.",
        "PARTIAL": "Похоже, навык помог запуститься, но не помог удержаться. Значит START стал легче, а STAY пока остаётся проблемой.",
        "NO_EFFECT": "Этот вариант не дал заметного эффекта. Не будем гонять его снова. Следующий тест должен проверять другой механизм.",
        "NEGATIVE_EFFECT": "Этот шаг усилил избегание. Остановим его и в следующий раз проверим более мягкий механизм.",
    }
    target = {"PROMISING": "successful_skills", "PARTIAL": "partial_skills"}.get(classification, "failed_skills")
    evidence = {"PROMISING": "после шага продолжил задачу", "PARTIAL": "стало легче, но остановился",
                "NO_EFFECT": "заметного эффекта не было", "NEGATIVE_EFFECT": "избегание усилилось"}[classification]
    existing = next((x for x in mapping[target] if x["skill"] == skill), None)
    if existing: existing["trials"] += 1
    else: mapping[target].append({"skill": skill, "evidence": evidence, "confidence": .65, "trials": 1})
    mapping["observations"].append(evidence); session["last_classification"] = classification
    session["state"] = DialogState.DAY_OPEN.value
    return _response(messages[classification], [("💪 Сделать следующий шаг", "experiment.next"), ("🌙 Завершить", "day.finish")])


def route_callback(session: MutableMapping[str, Any], action: str, *, callback_id: str = "", user_id: int | None = None,
                   screen_id: str = "") -> dict[str, Any]:
    """Route an action id idempotently; this function never invokes text analysis."""
    before = _state(session)
    duplicate = bool(callback_id and callback_id in session.setdefault("processed_callbacks", []))
    if duplicate:
        result = _response("Действие уже учтено.", duplicate=True)
    else:
        if callback_id: session["processed_callbacks"].append(callback_id)
        result = _dispatch(session, action)
        duplicate = bool(result.get("duplicate", False))
    after = _state(session)
    session.setdefault("telemetry", []).append({"user_id": user_id, "callback_action": action,
        "state_before": before.value, "state_after": after.value, "screen_id": screen_id,
        "handled_by": "skiller_callback_router", "duplicate": duplicate,
        "callback_fell_into_text_router": False, "timestamp": datetime.now(timezone.utc).isoformat()})
    return result


def _dispatch(session: MutableMapping[str, Any], action: str) -> dict[str, Any]:
    state = _state(session)
    if action in CLARIFICATION_ANSWERS or action.startswith("clarify.function."):
        if state != DialogState.DAY1_CLARIFY:
            return _response("Этот ответ уже учтён. Показываю актуальный шаг.", _summary_buttons() if session.get("short_report") else [])
        question, answer = CLARIFICATION_ANSWERS.get(action, ("functional_bottleneck", action.rsplit(".", 1)[-1]))
        session["structured_answers"].append({"question_id": question, "answer": answer})
        session["clarification_count"] += 1; session["confidence"] = min(.95, float(session["confidence"]) + .2)
        return _advance_clarification(session)
    if action == "clarify.other":
        return _response("Расскажи своими словами — текстом или голосом.")
    if action == "day.finish":
        if state == DialogState.DAY_CLOSED:
            return _response("День уже закрыт. Карта сохранена.", [("🧠 Что я сегодня понял", "map.today_insight")], duplicate=True)
        session["state"] = DialogState.DAY_CLOSED.value; session.pop("active_experiment", None)
        sm = session["skill_map"]; best = (sm["successful_skills"] or sm["partial_skills"] or [{"skill": "пока уточняется"}])[0]["skill"]
        return _response(f"🌙 День закрыт.\n\nСегодня мы заметили:\n— чаще всего мешало: {sm['primary_pattern']}\n— лучше всего сработало: {best}\n— пока нужно проверить: {sm['next_hypothesis']}\n\nГлавный вывод:\nкарта стала точнее благодаря реальному действию.\n\nЗавтра начнём не с нуля — карта сохранена.",
            [("🧠 Что я сегодня понял", "map.today_insight"), ("🧭 Моя карта", "map.full"),
             ("🎯 Разобрать новую ситуацию", "case.new"), ("⚡ Один необязательный шаг", "experiment.extra")])
    if action == "diagnosis.full_report":
        if not session.get("full_report"): _reports(session)
        return _response(session["full_report"], _summary_buttons())
    if action == "diagnosis.correct":
        session["state"] = DialogState.CORRECTION_INPUT.value
        return _response("Напиши или скажи одной короткой фразой, что в выводе нужно исправить.")
    if action in {"experiment.start", "experiment.next"}:
        if state in {DialogState.EXPERIMENT_ACTIVE, DialogState.EXPERIMENT_FEEDBACK}:
            return _response("Текущий эксперимент уже открыт — продолжим его без дубликата.",
                [("✅ Сделал", "experiment.done"), ("🌙 Завершить", "day.finish")], duplicate=True)
        if state == DialogState.DAY_CLOSED:
            return _response("День закрыт. Если хочется, доступен один необязательный шаг.",
                [("⚡ Один необязательный шаг", "experiment.extra")])
        if int(session.get("experiment_count", 0)) >= 2:
            return _response("На сегодня достаточно: два основных эксперимента уже проведены.")
        session["experiment_count"] += 1; session["active_skill"] = "Открыть без таймера"
        session["state"] = DialogState.EXPERIMENT_ACTIVE.value
        return _response("🚀 Эксперимент: открой задачу без таймера и сделай один видимый микрошаг.", [("✅ Сделал", "experiment.done"), ("🌙 Завершить", "day.finish")])
    if action == "experiment.done":
        if state == DialogState.EXPERIMENT_FEEDBACK:
            return _response("Что произошло после шага?", [("🚀 Продолжил задачу", "experiment.result.promising"),
                ("🙂 Стало легче, но остановился", "experiment.result.partial"),
                ("😐 Почти ничего не изменилось", "experiment.result.no_effect"),
                ("😣 Стало хуже / сильнее избегаю", "experiment.result.negative")], duplicate=True)
        if state != DialogState.EXPERIMENT_ACTIVE:
            return _response("Этот эксперимент уже закрыт. Показываю актуальный шаг.", duplicate=True)
        session["state"] = DialogState.EXPERIMENT_FEEDBACK.value; session["feedback_questions"] = 1
        return _response("Что произошло после шага?", [("🚀 Продолжил задачу", "experiment.result.promising"),
            ("🙂 Стало легче, но остановился", "experiment.result.partial"), ("😐 Почти ничего не изменилось", "experiment.result.no_effect"),
            ("😣 Стало хуже / сильнее избегаю", "experiment.result.negative")])
    if action.startswith("experiment.result."):
        if state != DialogState.EXPERIMENT_FEEDBACK:
            return _response("Этот результат уже учтён в карте.", duplicate=True)
        classification = {"promising": "PROMISING", "partial": "PARTIAL", "no_effect": "NO_EFFECT", "negative": "NEGATIVE_EFFECT"}.get(action.rsplit(".", 1)[-1])
        return _classify(session, classification) if classification else _response("Записал результат.")
    if action == "experiment.extra":
        if state != DialogState.DAY_CLOSED or session.get("post_close_experiment_used"):
            return _response("Дополнительный шаг уже использован. День остаётся закрытым.", [("🧠 Посмотреть вывод", "map.today_insight"), ("🌙 На сегодня всё", "day.finish")])
        session["post_close_experiment_used"] = True; session["post_close_skills"].append("Записать видимый следующий шаг")
        return _response("Необязательно: запиши один видимый следующий шаг на завтра. После выполнения день останется закрытым.", [("✅ Готово", "experiment.extra.done")])
    if action == "experiment.extra.done":
        session["state"] = DialogState.DAY_CLOSED.value
        return _response("Готово. Дополнительный шаг засчитан. День остаётся закрытым.", [("🧠 Посмотреть вывод", "map.today_insight"), ("🌙 На сегодня всё", "day.finish")])
    if action == "case.new":
        session["state"] = DialogState.NEW_CASE_INTAKE.value
        return _response("Расскажи новую конкретную ситуацию текстом или голосом.")
    if action == "crisis.procrastination":
        session["state"] = DialogState.CRISIS_FLOW.value
        return _response("Что происходит прямо сейчас? Можно ответить текстом, голосом или выбрать состояние.")
    if action.startswith("offer."):
        if state != DialogState.OFFER:
            session["state_before_offer"] = state.value
            session["last_non_offer_state"] = state.value
        if action in {"offer.continue", "offer.later"}:
            session["state"] = session.pop(
                "state_before_offer", session.get("last_non_offer_state", DialogState.DAY_OPEN.value)
            )
            return _response("Возвращаю к тренировке.")
        session["state"] = DialogState.OFFER.value
        offer_text = {
            "offer.free": "Бесплатная тренировка остаётся доступной. Можно вернуться к текущему шагу.",
            "offer.subscription": "Подписка открывает полный режим и продолжение персональной тренировки.",
            "offer.group": "Группа КПТ — формат совместной практики навыков с ведущим.",
            "offer.consultation": "Консультация — индивидуальный разбор карты со специалистом.",
        }.get(action, "Выбери подходящий вариант или вернись к тренировке.")
        return _response(offer_text, [("Продолжить тренировку", "offer.continue"),
                                     ("Выбрать позже", "offer.later")])
    if action == "navigation.back":
        if state == DialogState.OFFER:
            return _response("Выбери вариант или вернись к тренировке.", [
                ("🟢 Продолжить бесплатно", "offer.free"), ("🔵 Подписка", "offer.subscription"),
                ("🟠 Группа КПТ", "offer.group"), ("🔴 Консультация", "offer.consultation"),
                ("Продолжить тренировку", "offer.continue")])
        return _response("Возвращаю к актуальному шагу.", _summary_buttons() if session.get("short_report") else [])
    if action == "resources.show":
        return _response(
            "📚 Проверенный материал, не новый эксперимент: бесплатный КПТ-практикум Put Off Procrastinating от Centre for Clinical Interventions. "
            "Начни со схемы Vicious Cycle of Procrastination; читать всё сразу не нужно.\n\n"
            "https://www.cci.health.wa.gov.au/resources/looking-after-yourself/procrastination"
        )
    if action == "map.today_insight":
        return _response(session.get("short_report") or "Сегодня мы уточнили рабочую петлю; карта сохранена.")
    if action == "map.full":
        if not session.get("full_report"):
            _reports(session)
        return _response(session["full_report"])
    # A stale/legacy action is converted to its safe current equivalent, never text.
    if action in {"legacy.short_skill", "want_short_skill"}:
        return _dispatch(session, "experiment.start")
    return _response("Показываю актуальный доступный шаг.", _summary_buttons() if session.get("short_report") else [])
