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
        barrier_type = _canonical_barrier(barrier_type)
        for item in candidates:
            if barrier_type and item.barrier_type == barrier_type:
                return item
            if skill_id and skill_id in item.skill_tags:
                return item
        return next((item for item in candidates if item.barrier_type == "general"), None)


def _canonical_barrier(value: str) -> str:
    """Map user-facing and legacy barrier labels to reviewed content topics."""
    low = " ".join(str(value or "").lower().replace("_", " ").split())
    if any(token in low for token in ("страш", "ошиб", "стыд", "оцен", "перфек")):
        return "fear_of_error"
    if any(token in low for token in ("телефон", "youtube", "отвлеч", "залип", "быстр")):
        return "distraction"
    if any(token in low for token in ("слишком больш", "перегруз", "много задач", "too hard")):
        return "too_hard"
    if any(token in low for token in ("самокрит", "сжира", "приговор")):
        return "self_criticism"
    if any(token in low for token in ("нет сил", "устал", "низк ресурс", "low energy")):
        return "low_energy"
    return low


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
    ContentItem(
        "cci_perfectionism", "perfectionism", "fear_of_error", ("bad_draft", "bad_first_step"),
        "ru", "workbook", "Как ослабить перфекционистскую проверку", "https://www.cci.health.wa.gov.au/resources/looking-after-yourself/perfectionism",
        "Centre for Clinical Interventions, WA Health", "10–15 мин", True,
        "Открой бесплатный КПТ-практикум Overcoming Perfectionism и начни с модуля 5 — Reducing My Perfectionist Behaviour. Там предлагаются эксперименты с «достаточно хорошим» результатом вместо бесконечной переделки.",
    ),
    ContentItem(
        "cci_procrastination", "procrastination", "distraction", ("phone_away_3_min", "one_tab_focus", "restart_after_slip"),
        "ru", "workbook", "Как устроен цикл прокрастинации", "https://www.cci.health.wa.gov.au/resources/looking-after-yourself/procrastination",
        "Centre for Clinical Interventions, WA Health", "10–15 мин", True,
        "Открой бесплатный КПТ-практикум Put Off Procrastinating. Для начала достаточно схемы Vicious Cycle of Procrastination и одного упражнения из Practical Strategies to Stop Procrastination.",
    ),
    ContentItem(
        "cci_procrastination_general", "procrastination", "general", (),
        "ru", "workbook", "Проверенный практикум по прокрастинации", "https://www.cci.health.wa.gov.au/resources/looking-after-yourself/procrastination",
        "Centre for Clinical Interventions, WA Health", "10–15 мин", True,
        "Это бесплатный КПТ-практикум. Начни с раздела, который ближе к сегодняшней ситуации; читать весь материал сразу не нужно.",
    ),
))


def render_content_suggestion(item: ContentItem | None, *, reason: str = "") -> str:
    if item is None:
        return "📚 Материал по твоей ситуации\n\nСейчас лучше закрепить сегодняшний навык одной повторной попыткой, чем добавлять ещё чтение."
    why = reason or "он связан с сегодняшней проверкой"
    text = (
        "📚 Материал по твоей ситуации\n\n"
        f"«{item.title}» — {item.duration}.\n"
        f"Почему именно это: {why}."
    )
    if item.body:
        text = f"{text}\n\n{item.body}"
    if item.url and not item.url.startswith("internal://"):
        text = f"{text}\n\nОткрыть материал: {item.url}"
    return text
