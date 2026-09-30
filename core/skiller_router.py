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

from core.product_config import FREE_BETA_ACCESS
from core.learning_engine import correction_intent
from core.dialogue_ux import valid_transcript, is_clarification, explain_previous, concrete_step, plain_text, repeated


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
    "experiment.extra.done", "experiment.not_started",
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
    facts = session.get("case_facts") or []
    answers = {a["question_id"]: a["answer"] for a in session.get("structured_answers", [])}
    labels = {"overload": "слишком много дел", "fear_of_evaluation": "страх ошибки или реакции",
              "unclear_start": "неясно, с чего начать", "task_aversion": "не хочется делать эту задачу",
              "distraction": "частые переключения"}
    primary = labels.get(answers.get("entry_barrier"), "пока нужно уточнить, что мешает")
    bottleneck = str(answers.get("functional_bottleneck") or "UNKNOWN").upper()
    stages = {"START": "начать", "STAY": "продолжить после начала", "RETURN": "вернуться после перерыва", "UNKNOWN": "пока не уточнили"}
    session["hypotheses"] = [primary] if answers.get("entry_barrier") else []
    session["skill_map"].update(primary_pattern=primary, functional_bottleneck=bottleneck,
                               secondary_patterns=[], confidence=session["confidence"])
    fact_text = "\n".join(f"— «{fact}" + "»" for fact in facts[-3:]) or "Пока нет описания ситуации."
    short = (f"Вы описали:\n{fact_text}\n\n"
             f"По вашим ответам мешает: {primary}.\n"
             f"Труднее всего: {stages.get(bottleneck, stages['UNKNOWN'])}.\n\n"
             "Это предварительный вывод. Попробуем одно действие?")
    full = (f"Подробный разбор\n\nЧто вы сообщили\n{fact_text}\n\n"
            f"Ваши ответы\nМешает: {primary}. Труднее всего: {stages.get(bottleneck, stages['UNKNOWN'])}.\n\n"
            "Что пока неизвестно\nПочему это происходит и какой способ поможет, ещё нужно проверить.\n\n"
            "Что дальше\nВыберем одно действие для вашей задачи и посмотрим на результат.\n\n"
            "Ограничение\nЭто не диагноз. Вы можете исправить любой вывод.")
    session["short_report"], session["full_report"] = short, full


def route_user_input(session: MutableMapping[str, Any], content: str, *, kind: str = "text") -> dict[str, Any]:
    """Route genuine user text/voice. Callback labels must never call this."""
    state = _state(session)
    content = content.strip()
    if kind == "voice" and not valid_transcript(content):
        return _response("Кажется, голосовое не распозналось. Можете записать ещё раз или написать текстом.")
    if is_clarification(content):
        return _response(explain_previous(session.get("last_instruction", ""), str((session.get("case_facts") or [""])[0]), content))
    if session.get("awaiting_barrier"):
        session["awaiting_barrier"] = False
        session.setdefault("structured_answers", []).append({"question_id": "outcome_barrier", "answer": content, "role": "self_report", "kind": kind})
        return _response("Записано: «" + content + "». Можно выбрать другой способ или закончить на сегодня.", [("Попробовать другой способ", "experiment.next"), ("Закончить", "day.finish")])
    if session.pop("awaiting_target", False):
        session.setdefault("case_facts", []).append(content)
        session["target_task"] = content
        session["explicit_action"] = content
        return _dispatch(session, "experiment.start")
    if state in {DialogState.EXPERIMENT_ACTIVE, DialogState.EXPERIMENT_FEEDBACK}:
        low = content.casefold().replace("ё", "е")
        if "не стало хуже" not in low and any(x in low for x in ("стало хуже", "хуже после")):
            return _classify(session, "NEGATIVE_EFFECT")
        if any(x in low for x in ("не начал", "не удалось начать", "не получилось начать", "не помог")):
            return _classify(session, "NO_EFFECT")
        if any(x in low for x in ("не продолжил", "не закончил")):
            return _response("Получилось сделать первый шаг или пока не начать? Можно ответить своими словами.")
        if any(x in low for x in ("сделал шаг", "только шаг", "остановил", "остановилась")):
            return _classify(session, "PARTIAL")
        if any(x in low for x in ("продолжил", "продолжила", "закончил", "закончила")):
            return _classify(session, "PROMISING")
        return _response("Что получилось после попытки: продолжить дело, сделать только первый шаг или пока не начать? Можно ответить своими словами.")
    if state == DialogState.CORRECTION_INPUT:
        intent = correction_intent(content)
        if intent == "confirm":
            session["conclusion_confirmed"] = True
            session["state"] = DialogState.DAY1_SUMMARY.value
            return _response("Отлично, текущий вывод подтверждён. Ничего в карте не меняю.", _summary_buttons())
        if intent in {"reject", "unclear"}:
            return _response("Что именно стоит изменить? Одной короткой фразой.")
        session.setdefault("corrections", []).append({"kind": kind, "text": content})
        session.setdefault("case_facts", []).append(f"Поправка пользователя: {content}")
        session["confidence"] = .25
        session["structured_answers"] = []
        _reports(session); session["state"] = DialogState.DAY1_SUMMARY.value
        return _response("Спасибо. Обновил рабочую карту.\n\n" + session["short_report"], _summary_buttons())
    if state in {DialogState.DAY1_INTAKE, DialogState.NEW_CASE_INTAKE}:
        session.update(case_facts=[content], structured_answers=[], clarification_count=0,
                       confidence=.35, state=DialogState.DAY1_CLARIFY.value)
        return _question(session)
    if state == DialogState.DAY1_CLARIFY:
        session["structured_answers"].append({"question_id": "free_text_clarification", "answer": content, "kind": kind, "role": "self_report"})
        session.setdefault("case_facts", []).append(content)
        session["clarification_count"] += 1; session["confidence"] += .15
        return _advance_clarification(session)
    if state == DialogState.CRISIS_FLOW:
        return _response("Что сейчас мешает сильнее всего? Можно ответить одной фразой.")
    return _response("Что сейчас не получается? Можно описать новую ситуацию.")


def _summary_buttons() -> list[tuple[str, str]]:
    return [("🚀 Проверить навык", "experiment.start"), ("📖 Подробное заключение", "diagnosis.full_report"),
            ("✏️ Исправить вывод", "diagnosis.correct")]


def _advance_clarification(session: MutableMapping[str, Any]) -> dict[str, Any]:
    count = int(session["clarification_count"])
    if float(session["confidence"]) >= .65 or count >= 2:
        _reports(session); session["state"] = DialogState.DAY1_SUMMARY.value
        return _response(session["short_report"], _summary_buttons())
    return _question(session)


def _classify(session: MutableMapping[str, Any], classification: str) -> dict[str, Any]:
    skill = str(session.get("active_skill") or "Открыть без таймера")
    mapping = session["skill_map"]
    messages = {
        "PROMISING": "После шага получилось продолжить дело. Сохраним этот результат. Остановиться сейчас тоже можно.",
        "PARTIAL": "Первый шаг получился, продолжение — пока нет. Это два разных результата. Что помешало продолжить?",
        "NO_EFFECT": "Этот способ пока не помог. Что помешало попробовать или продолжить?",
        "NEGATIVE_EFFECT": "После попытки стало хуже. Остановим упражнение. Какая поддержка сейчас нужна?",
    }
    target = {"PROMISING": "successful_skills", "PARTIAL": "partial_skills"}.get(classification, "failed_skills")
    evidence = {"PROMISING": "после шага продолжил задачу", "PARTIAL": "стало легче, но остановился",
                "NO_EFFECT": "заметного эффекта не было", "NEGATIVE_EFFECT": "после попытки стало хуже"}[classification]
    existing = next((x for x in mapping[target] if x["skill"] == skill), None)
    if existing: existing["trials"] += 1
    else: mapping[target].append({"skill": skill, "evidence": evidence, "confidence": .65, "trials": 1})
    mapping["observations"].append(evidence); session["last_classification"] = classification
    session["state"] = DialogState.DAY_OPEN.value
    session["awaiting_barrier"] = classification != "PROMISING"
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
    if result.get("text"):
        session["last_instruction"] = result["text"]
    return result


def _dispatch(session: MutableMapping[str, Any], action: str) -> dict[str, Any]:
    state = _state(session)
    if action in CLARIFICATION_ANSWERS or action.startswith("clarify.function."):
        if state != DialogState.DAY1_CLARIFY:
            return _response("Этот ответ уже учтён. Показываю актуальный шаг.", _summary_buttons() if session.get("short_report") else [])
        question, answer = CLARIFICATION_ANSWERS.get(action, ("functional_bottleneck", action.rsplit(".", 1)[-1]))
        session["structured_answers"].append({"question_id": question, "answer": answer, "role": "self_report"})
        session["clarification_count"] += 1; session["confidence"] = min(.95, float(session["confidence"]) + .2)
        return _advance_clarification(session)
    if action == "clarify.other":
        return _response("Расскажи своими словами — текстом или голосом.")
    if action == "day.finish":
        if state == DialogState.DAY_CLOSED:
            return _response("День уже закрыт. Карта сохранена.", [("🧠 Что я сегодня понял", "map.today_insight")], duplicate=True)
        session["state"] = DialogState.DAY_CLOSED.value; session.pop("active_experiment", None)
        sm = session["skill_map"]; best = (sm["successful_skills"] or sm["partial_skills"] or [{"skill": "пока уточняется"}])[0]["skill"]
        return _response(f"🌙 День закрыт.\n\nСегодня мы заметили:\n— чаще всего мешало: {sm['primary_pattern']}\n— лучше всего сработало: {best}\n— пока нужно проверить: {sm['next_hypothesis']}\n\nГлавный вывод:\nсохраняем только то, что вы сообщили или отметили после попытки.\n\nЗавтра начнём не с нуля — карта сохранена.",
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
        task = str(session.get("target_task") or (session.get("case_facts") or [""])[0])
        if not task:
            session["awaiting_target"] = True
            return _response("Какое одно действие нужно для вашей задачи?")
        session["awaiting_barrier"] = False
        session["experiment_count"] += 1
        instruction = ("Попробуйте только это действие: «" + str(session.pop("explicit_action")) + "». Можно остановиться после него.") if session.get("explicit_action") else concrete_step(task, alternative=session["experiment_count"] > 1)
        if instruction.endswith("?") or instruction.startswith("Какое одно"):
            session["experiment_count"] -= 1
            session["awaiting_target"] = True
            return _response(instruction)
        recent = session.setdefault("recent_instructions", [])
        if repeated(instruction, recent):
            session["experiment_count"] -= 1
            session["awaiting_barrier"] = True
            session["state"] = DialogState.DAY_OPEN.value
            return _response("Этот способ мы уже пробовали. Что в нём не подошло?")
        session["recent_instructions"] = (recent + [instruction])[-5:]
        session["active_skill"] = instruction
        session["last_instruction"] = instruction
        session["state"] = DialogState.EXPERIMENT_ACTIVE.value
        return _response(instruction + "\n\nЧто получилось?", [
            ("✅ Получилось сделать", "experiment.done"),
            ("Пока не получилось начать", "experiment.not_started"),
            ("🌙 Завершить", "day.finish")])
    if action == "experiment.not_started":
        if state != DialogState.EXPERIMENT_ACTIVE:
            return _response("Этот шаг уже закрыт. Можно описать новую ситуацию.", duplicate=True)
        return _classify(session, "NO_EFFECT")

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
        if FREE_BETA_ACCESS:
            if state == DialogState.OFFER:
                session["state"] = session.pop(
                    "state_before_offer", session.get("last_non_offer_state", DialogState.DAY_OPEN.value)
                )
            return _response(
                "🟢 Сейчас открытый beta-тест: все функции доступны бесплатно. Оплата не нужна.",
                [("Продолжить тренировку", "offer.continue")],
            )
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
            if FREE_BETA_ACCESS:
                session["state"] = session.pop(
                    "state_before_offer", session.get("last_non_offer_state", DialogState.DAY_OPEN.value)
                )
                return _response(
                    "🟢 Полный режим открыт бесплатно на время beta-теста.",
                    [("Продолжить тренировку", "offer.continue")],
                )
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

