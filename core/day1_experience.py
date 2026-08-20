"""Day-1 value layer built on top of the existing Learning Engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


FIRST_EXPERIMENT_BY_MECHANISM = {
    "fear_of_evaluation": ("bad_draft", "bad_first_step"),
    "evaluation_avoidance": ("bad_draft", "bad_first_step"),
    "perfectionism": ("bad_draft", "bad_first_step"),
    "perfectionism_error_fear": ("bad_draft", "bad_first_step"),
    "overload": ("one_visible_step", "visible_next_step"),
    "overwhelm": ("one_visible_step", "visible_next_step"),
    "distraction": ("phone_away_3_min", "phone_far_3min", "one_tab_focus"),
    "attention_drift": ("phone_away_3_min", "phone_far_3min", "one_tab_focus"),
    "uncertainty": ("one_visible_step", "visible_next_step", "task_naming"),
    "unclear_next_action": ("one_visible_step", "visible_next_step", "task_naming"),
    "low_energy": ("body_first", "body_before_task", "open_without_timer"),
    "low_activation": ("body_first", "body_before_task", "open_without_timer"),
    "self_criticism": ("self_criticism_to_instruction", "bad_draft", "bad_first_step"),
}

DAY1_ANALYTICS_EVENTS = (
    "day1_diagnosis_completed", "first_experiment_started", "first_experiment_completed",
    "first_experiment_result", "continued_target_task", "day1_map_viewed",
    "day1_finished", "returned_day2",
)


@dataclass(frozen=True)
class Day1Map:
    task: str
    break_point: str
    trigger: str
    alternative_behavior: str
    hypothesis: str
    intervention: str
    result: str
    confidence: str
    next_question: str


def _clean(text: str, limit: int = 180) -> str:
    return " ".join(str(text or "").split())[:limit]


def _signals(user_input: str) -> dict[str, bool]:
    low = user_input.lower()
    return {
        "evaluation": any(x in low for x in ("оцен", "руковод", "некомпет", "стыд", "баналь", "ошиб")),
        "preparation": any(x in low for x in ("информац", "подготов", "структур", "чита", "изуч", "собира")),
        "deadline": any(x in low for x in ("дедлайн", "срок", "ноч", "последн")),
        "switching": any(x in low for x in ("переключ", "телефон", "youtube", "соцсет")),
        "overload": any(x in low for x in ("слишком много", "огром", "не знаю с чего", "куча")),
    }


def build_day1_insight(user_input: str, selected_mechanism: str, inferred_mechanism: str) -> str:
    """Build a grounded 3–5 sentence hypothesis using only observable input signals."""
    text = _clean(user_input, 600)
    signals = _signals(text)
    mechanism = selected_mechanism or inferred_mechanism
    sentences: list[str] = []
    if signals["deadline"]:
        sentences.append("Похоже, проблема не в способности работать вообще: в сообщении есть опыт работы под давлением срока.")
    else:
        sentences.append("Похоже, стопор относится к этой конкретной задаче, а не доказывает общую неспособность начинать.")
    if signals["evaluation"] or mechanism in {"fear_of_evaluation", "evaluation_avoidance", "perfectionism", "perfectionism_error_fear"}:
        sentences.append("Стопор появляется там, где нужно создать первую версию и результат могут оценивать.")
    elif signals["overload"] or mechanism in {"overload", "overwhelm"}:
        sentences.append("Стопор появляется, когда задача видится сразу целиком и следующий физический шаг теряется.")
    else:
        sentences.append("Пока точнее всего видно затруднение в момент перехода от намерения к конкретному действию.")
    if signals["preparation"]:
        sentences.append("Вместо создания результата появляется подготовка: поиск информации или структуры.")
        sentences.append("Рабочая гипотеза — дополнительная подготовка может откладывать момент, когда появится первая оцениваемая версия.")
    elif signals["switching"]:
        sentences.append("Вместо целевого действия внимание переключается на более доступное занятие.")
    sentences.append("Это пока гипотеза — проверим её действием.")
    return " ".join(sentences[:5])


def select_first_experiment(mechanism: str, available_skill_ids: Sequence[str]) -> str:
    available = set(available_skill_ids)
    for skill_id in FIRST_EXPERIMENT_BY_MECHANISM.get(mechanism, ("one_visible_step", "visible_next_step", "open_only")):
        if skill_id in available:
            return skill_id
    if not available_skill_ids:
        raise LookupError("No skills available for first experiment")
    return available_skill_ids[0]


def personalize_skill_instruction(skill: Mapping[str, Any], task_context: str, *, distress: int = 40) -> str:
    """Keep the skill mechanism but make its instruction produce visible task progress."""
    sid = str(skill.get("skill_id") or skill.get("id") or "")
    task = _clean(task_context, 140) or "задачу"
    low = task.lower()
    short = distress >= 70
    duration = "30 секунд" if short else "3 минуты"
    if "презент" in low or "слайд" in low:
        if sid in {"bad_draft", "bad_first_step"}:
            return f"Открой презентацию. За {duration} сделай один намеренно банальный первый слайд. Не ищи информацию и не исправляй его."
        return "Открой презентацию и выбери один конкретный слайд, который создашь первым."
    if "письм" in low or "клиент" in low:
        if sid in {"bad_draft", "bad_first_step"}:
            return f"Открой письмо клиенту и за {duration} напиши первую несовершенную версию обращения. Пока не отправляй."
        return "Открой письмо и назови одно следующее действие: написать обращение, уточнить факт или отправить."
    if "документ" in low:
        return "Возьми один первый документ и назови, куда он должен попасть. Остальную стопку пока не разбирай."
    if sid in {"bad_draft", "bad_first_step"}:
        return f"Открой «{task}» и за {duration} создай один намеренно несовершенный фрагмент результата. Не редактируй его до сигнала."
    if sid in {"phone_away_3_min", "phone_far_3min", "one_tab_focus"}:
        return f"Убери телефон вне руки, открой только «{task}» и оставайся с одним конкретным действием {duration}."
    if sid in {"body_first", "body_before_task"}:
        return f"Сделай один короткий телесный запуск, затем сразу открой «{task}» и выполни одно действие за {duration}."
    return f"Открой «{task}» и за {duration} сделай один видимый кусок результата, который можно назвать одним предложением."


def build_day1_result_insight(*, before: str, intervention: str, result: str, mechanism: str) -> str:
    before = _clean(before, 180)
    intervention = _clean(intervention, 180)
    if result == "STRONG_SUCCESS":
        return (f"До эксперимента: {before}. Мы изменили одно: {intervention}. После этого ты продолжил целевую задачу. "
                f"Это первый сигнал в пользу гипотезы «{mechanism}», но пока только одна попытка.")
    if result == "FAILED":
        return (f"До эксперимента: {before}. Проверили: {intervention}. Действие не получилось выполнить, поэтому гипотеза пока не подтверждена. "
                "Мы уже знаем, что этот размер или вход сейчас не подходит.")
    return (f"До эксперимента: {before}. Проверили: {intervention}. Продолжения целевой задачи не зафиксировано. "
            "Навык пока не подтверждён; это сужает выбор следующего теста.")


def build_day1_map(data: Mapping[str, Any]) -> Day1Map:
    result_code = str(data.get("experiment_result") or "UNKNOWN")
    confidence = "🟡 первая подтверждающая попытка" if result_code == "STRONG_SUCCESS" else "⚪ гипотеза пока не подтвердилась"
    result = {
        "STRONG_SUCCESS": "после микрошага ты продолжил целевую задачу",
        "WEAK_SUCCESS": "микрошаг состоялся, но дальше ты остановился",
        "EXECUTED_ONLY": "действие выполнено без продолжения целевой задачи",
        "FAILED": "действие пока не получилось выполнить",
        "UNKNOWN": "данных для вывода пока недостаточно",
    }.get(result_code, "данных для вывода пока недостаточно")
    return Day1Map(
        task=_clean(str(data.get("task") or "текущая задача")),
        break_point=_clean(str(data.get("break_point") or "момент перехода к первому видимому результату")),
        trigger=_clean(str(data.get("trigger") or "требование начать и получить результат")),
        alternative_behavior=_clean(str(data.get("alternative_behavior") or "подготовка или переключение вместо действия")),
        hypothesis=_clean(str(data.get("hypothesis") or "первый барьер связан с входом в задачу")),
        intervention=_clean(str(data.get("intervention") or "один конкретный проверяемый вход")),
        result=result, confidence=confidence,
        next_question="что поможет удержаться в задаче после первоначального входа?",
    )


def render_day1_map(value: Day1Map) -> str:
    return (
        "🧭 Твоя первая карта\n\n"
        f"Задача:\n{value.task}\n\nГде ломается:\n{value.break_point}\n\n"
        f"Что появляется:\n{value.trigger}\n\nЧто делаешь вместо:\n{value.alternative_behavior}\n\n"
        f"Рабочая гипотеза:\n{value.hypothesis}\n\nЧто проверили:\n{value.intervention}\n\n"
        f"Результат:\n{value.result}\n\nУверенность:\n{value.confidence}\n\n"
        f"Следующий вопрос:\n{value.next_question}\n\n"
        "Сегодня уже знаем: ✓ место стопора  ✓ один проверенный механизм  ✓ один протестированный навык\n"
        "Ещё не знаем: ○ что удерживает в задаче  ○ повторится ли эффект  ○ как легче возвращаться"
    )


def day1_tomorrow_teaser(trainer: str, result: str) -> str:
    moved = result == "STRONG_SUCCESS"
    if trainer == "skinny":
        return "Начать смог. Теперь вопрос сложнее: удержишься ли в задаче после старта. Завтра проверим." if moved else "Первый вход проверили. Не угадали — уже данные. Завтра берём другой механизм."
    if trainer == "beck":
        return ("Есть первый сигнал для START. Теперь отдельно проверим, что происходит через 5–15 минут после начала — функцию STAY."
                if moved else "Первая гипотеза не получила поддержки. Завтра проверим альтернативный механизм, не повторяя тот же вход.")
    return ("Сегодня удалось найти вход, после которого ты продолжил задачу. Завтра посмотрим, что помогает не выпадать уже после старта."
            if moved else "Сегодня один вариант не подтвердился. Завтра попробуем другой вход — без давления повторить то же самое.")
