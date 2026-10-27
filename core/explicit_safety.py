"""High-confidence intent phrases shared by the global safety boundary.

This supplements the existing triage; it is not a clinical risk classifier.
"""
import re

_INTENT = re.compile(
    r"\b(?:убью\s+себя|покончу\s+с\s+собой|"
    r"(?:хочу|собираюсь|намерен[а]?)\s+(?:сейчас\s+)?(?:повеситься|застрелиться)|"
    r"i\s+(?:want\s+to|am\s+going\s+to|will)\s+(?:kill\s+myself|end\s+my\s+life)|"
    r"хачу\s+(?:памерці|забіць\s+сябе))\b",
    re.IGNORECASE,
)


def explicit_safety_intent(text: str) -> bool:
    return bool(_INTENT.search(text or ""))


def safety_text(text: str) -> str:
    """Mask narrow ordinary/explicitly resolved statements, never whole messages.

    Additional current intent elsewhere in the message remains visible.
    Ambiguous 'есть план' and uncertain denials still enter triage.
    """
    low = (text or "").lower().replace("ё", "е")
    low = re.sub(r"\bесть план\s+(?:закончить|написать|подготовить|сдать)\s+(?:отчет|письмо|презентацию|работу)\b", "обычная задача", low)
    low = re.sub(r"^\s*(?:я\s+)?больше не думаю о суициде\b(?=\s*(?:$|[.,!;]))", "сообщенное улучшение", low)
    return low
