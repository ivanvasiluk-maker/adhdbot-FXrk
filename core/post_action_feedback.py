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
            f"{self.reaction}\n\n{self.interpretation}\n\n"
            f"Сегодня заметили: {self.personal_pattern}\n\n"
            f"Сработало / проверяли: {self.tested_principle}\n\n"
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
        reaction = f"Получилось: ты сделал конкретный вход — {action}. Заканчивать всю задачу для этого не понадобилось."
        if context.helpfulness in {"helped", "some"}:
            interpretation = f"В этой попытке сработал не общий призыв собраться, а навык «{skill}»: он снизил стоимость первого контакта с задачей."
        else:
            interpretation = "Действие состоялось, но заметного облегчения пока нет. Записываем запуск отдельно от субъективного эффекта."
        pattern = _short(context.known_pattern, f"в ситуации «{situation}» движение появилось после одного проверяемого действия", 180)
        principle = f"{skill} — {action}"
        anchor = f"Когда снова возникнет «{situation}», начни с действия «{action}», а не со всей задачи."
    else:
        reaction = f"Этот вход не сработал: действие «{action}» не началось. Это результат проверки, а не оценка тебя."
        interpretation = f"Похоже, одного уменьшения шага было недостаточно: {barrier}. Следующий заход должен изменить причину или способ входа, а не повторить то же самое."
        pattern = _short(context.known_pattern, f"в ситуации «{situation}» текущий вход не обошёл барьер: {barrier}", 180)
        principle = f"проверяли «{skill}», результат — нужен другой или более ясный вход"
        anchor = f"Если «{action}» не запускается, сначала уточни барьер: {barrier}."
    return PostActionReflection(
        reaction, interpretation, pattern, _short(principle, principle, 180), _short(anchor, anchor, 180),
    )


def _short(value: str, fallback: str, limit: int) -> str:
    clean = " ".join(str(value or "").split()).strip(" .") or fallback
    return clean if len(clean) <= limit else clean[:limit - 1].rstrip() + "…"
