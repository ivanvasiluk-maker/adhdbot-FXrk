"""User-facing closure and next-return continuity copy."""

from __future__ import annotations


def render_session_closure(anchor: str) -> str:
    anchor = " ".join(str(anchor or "").split()) or "сегодняшняя попытка дала данные для следующего шага"
    return (
        "На сегодня основная тренировка закончена.\n\n"
        f"Главное, что мы выяснили: {anchor}\n\n"
        "Можешь спокойно остановиться здесь. А если хочется продолжить — я никуда не исчез."
    )


def render_return_continuity(anchor: str) -> str:
    anchor = " ".join(str(anchor or "").split())
    if not anchor:
        return "Ситуация сегодня похожа на прошлую или начнём с нового контекста?"
    return f"В прошлый раз мы заметили: {anchor} Проверим тот же принцип или ситуация другая?"
