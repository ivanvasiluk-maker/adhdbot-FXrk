"""Concrete post-action reflection and one-session memory anchor."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReflectionContext:
    situation: str
    barrier: str
    skill_title: str
    tested_action: str
    completed: bool
    partial: bool
    helpfulness: str
    continued: bool | None
    previous_successes: int = 0
    known_pattern: str = ""


@dataclass(frozen=True)
class PostActionReflection:
    reaction: str
    interpretation: str
    personal_pattern: str
    tested_principle: str
    memory_anchor: str

    def render(self) -> str:
        return (
            f"{self.reaction}\n\n"
            f"Сегодня заметили: {self.interpretation}\n\n"
            f"Запомнить: {self.memory_anchor}"
        )


BARRIER_TEXT = {
    "too_hard": "порог входа оставался слишком высоким",
    "no_energy": "для шага не хватило доступной энергии",
    "anxiety": "на входе усилилось напряжение",
    "unclear_instruction": "первое действие осталось неясным",
    "distracted": "внимание перехватила среда",
    "other": "помеха оказалась не той, которую мы предполагали",
    "unknown": "первый вход пока не совпал с реальным барьером",
}


def build_post_action_reflection(context: ReflectionContext) -> PostActionReflection:
    situation = _short(context.situation, "текущей задачи", 90)
    action = _short(context.tested_action, context.skill_title or "первого действия", 100)
    skill = _short(context.skill_title, "короткий вход", 70)
    barrier = BARRIER_TEXT.get(context.barrier, _short(context.barrier, "барьер нужно уточнить", 90))
    successful = context.completed or context.partial

    if successful:
        reaction = f"Получилось выполнить действие: {action}."
        if context.helpfulness == "worse":
            interpretation = "После шага стало хуже. Выполнение действия не означает, что способ помог."
            principle = f"проверяли «{skill}», состояние ухудшилось"
            anchor = "Этот способ пока не повторяем. Можно остановиться или выбрать другой вариант."
        elif context.helpfulness in {"helped", "some"}:
            interpretation = f"По вашему ответу, способ «{skill}» помог в этой попытке."
            principle = f"{skill} — {action}"
            anchor = f"Когда снова возникнет «{situation}», начни с действия «{action}», а не со всей задачи."
        else:
            interpretation = "Действие выполнено, но по вашему ответу заметного облегчения нет."
            principle = f"проверяли «{skill}», полезность пока не подтверждена"
            anchor = "Сохраним результат попытки. В следующий раз можно выбрать другой способ."
        pattern = (f"В ситуации «{situation}» после шага стало хуже." if context.helpfulness == "worse" else
                   _short(context.known_pattern, f"в ситуации «{situation}» движение появилось после одного проверяемого действия", 180))
    else:
        reaction = f"Действие «{action}» пока не получилось. Можно остановиться или выбрать другой способ."
        interpretation = f"По вашему ответу: {barrier}. Важно не повторить то же самое, а подобрать другой способ."
        pattern = _short(context.known_pattern, f"в ситуации «{situation}» текущий вход не обошёл барьер: {barrier}", 180)
        principle = f"проверяли «{skill}», результат — нужен другой или более ясный вход"
        anchor = "Сохраним результат попытки. В следующий раз можно выбрать другой способ."
    return PostActionReflection(
        reaction, interpretation, pattern, _short(principle, principle, 180), _short(anchor, anchor, 180),
    )


def _short(value: str, fallback: str, limit: int) -> str:
    clean = " ".join(str(value or "").split()).strip(" .") or fallback
    return clean if len(clean) <= limit else clean[:limit - 1].rstrip() + "…"

