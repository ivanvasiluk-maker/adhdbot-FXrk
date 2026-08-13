"""Reviewed optional content; never synthesize links at runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ContentItem:
    content_id: str
    topic: str
    barrier_type: str
    skill_tags: tuple[str, ...]
    language: str
    content_type: str
    title: str
    url: str
    source: str
    duration: str
    reviewed: bool
    body: str = ""


class ContentRegistry:
    def __init__(self, items: Iterable[ContentItem] = ()) -> None:
        self._items = {item.content_id: item for item in items if item.reviewed}

    def select(self, *, barrier_type: str = "", skill_id: str = "", language: str = "ru") -> ContentItem | None:
        candidates = [item for item in self._items.values() if item.language == language]
        candidates.sort(key=lambda item: item.content_id)
        for item in candidates:
            if barrier_type and item.barrier_type == barrier_type:
                return item
            if skill_id and skill_id in item.skill_tags:
                return item
        return None


# Small, repository-owned contour. These are short reviewed notes, not generated
# links; adding external content still requires an explicit content review.
CONTENT_REGISTRY = ContentRegistry((
    ContentItem(
        "start_threshold", "task_start", "too_hard", ("open_only", "visible_next_step"),
        "ru", "note", "Как уменьшить именно вход в задачу", "internal://start-threshold",
        "SKILLER editorial", "2 мин", True,
        "Выбери не маленькую задачу целиком, а первый наблюдаемый контакт: открыть файл, найти строку или написать одну сырую фразу. После контакта можно остановиться.",
    ),
    ContentItem(
        "unclear_first_action", "task_clarity", "unclear_instruction", ("task_naming", "one_visible_step"),
        "ru", "note", "Как сделать первый шаг наблюдаемым", "internal://unclear-first-action",
        "SKILLER editorial", "2 мин", True,
        "Фраза «поработать над задачей» не проверяема. Хороший первый шаг отвечает на вопрос: что именно увидит камера в ближайшие две минуты?",
    ),
    ContentItem(
        "anxiety_before_contact", "approach", "anxiety", ("bad_first_step", "open_without_timer"),
        "ru", "note", "Вход в неприятный контакт без требования закончить", "internal://anxiety-contact",
        "SKILLER editorial", "2 мин", True,
        "Не требуй от себя завершить разговор или письмо. Подготовь только первый контакт: открыть чат, записать первую фразу и отдельно решить, готов ли ты продолжать.",
    ),
))


def render_content_suggestion(item: ContentItem | None, *, reason: str = "") -> str:
    if item is None:
        return "Сейчас у меня нет подходящего проверенного материала. Не буду придумывать ссылку."
    why = reason or "он связан с сегодняшней проверкой"
    text = f"Если хочется разобраться подробнее: «{item.title}» ({item.duration}). Предлагаю материал, потому что {why}."
    if item.body:
        return f"{text}\n\n{item.body}"
    return f"{text}\n{item.url}"
