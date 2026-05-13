# ============================================================
# NLP FALLBACK — локальные intent-проверки без GPT
# ============================================================

import re


def _normalize(text: str) -> str:
    """Упростить пользовательский текст для локальных intent checks."""
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_timer_too_hard(text: str) -> bool:
    """Пользователю сложно даже с таймером."""
    low = _normalize(text)
    return bool(
        re.search(r"сложн\w*\s+даже\s+таймер", low)
        or re.search(r"даже\s+таймер\w*\s+сложн", low)
        or re.search(r"таймер\w*\s+.*(не могу|не получается|сложн)", low)
    )


def is_too_hard(text: str) -> bool:
    """Пользователь сообщает, что текущий шаг слишком трудный."""
    low = _normalize(text)
    if is_timer_too_hard(low):
        return True
    return any(
        marker in low
        for marker in (
            "слишком сложно",
            "очень сложно",
            "мне сложно",
            "сложно",
            "не могу",
            "не получается",
            "не выходит",
            "не справляюсь",
            "тяжело",
        )
    )


def is_misunderstood(text: str) -> bool:
    """Пользователь говорит, что бот понял его неверно."""
    low = _normalize(text)
    return any(
        marker in low
        for marker in (
            "ты меня не понял",
            "ты не понял",
            "не понял",
            "не понял меня",
            "меня не понял",
            "не так понял",
            "не то понял",
            "не понимаю",
            "не понимаешь",
        )
    )
