"""User-facing closure and next-return continuity copy."""

from __future__ import annotations


def render_session_closure(anchor: str) -> str:
    anchor = " ".join(str(anchor or "").split()) or "можно вернуться к практике позже"
    return (
        "На сегодня основная тренировка закончена.\n\n"
        f"Главное, что мы выяснили: {anchor}\n\n"
        "Можно остановиться здесь. Чтобы продолжить, напишите новую ситуацию."
    )


def render_return_continuity(anchor: str) -> str:
    anchor = " ".join(str(anchor or "").split())
    if not anchor:
        return "С чем нужна помощь сегодня?"
    return f"В прошлый раз мы заметили: {anchor} Хотите продолжить с этого или разобрать другую ситуацию?"



def render_pause(trainer: str = "marsha") -> str:
    opening = {
        "skinny": "Остановимся здесь.",
        "marsha": "Можно спокойно остановиться здесь.",
        "beck": "На сегодня остановимся. Результат шага не будем додумывать.",
    }.get(trainer, "Можно спокойно остановиться здесь.")
    return opening + " Текущее место сохранено до конца дня. Если захотите вернуться сегодня, нажмите «Продолжить»."
