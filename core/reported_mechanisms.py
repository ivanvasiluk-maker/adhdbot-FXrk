"""Conservative evidence for conditional skills, including explicit denials."""
import re

SIGNALS = {
    'self_criticism_to_instruction': r'самокрит\w*|руга(?:ю\w*|ть)|стыд\w*|self_attack',
    'restart_after_slip': r'отвлек\w*|отвлека\w*|переключ\w*|срыв\w*|выпал\w*|attention_drift|phone',
}


def signal_status(text, skill_id):
    """True: reported; False: explicitly denied; None: no relevant evidence."""
    denied = False
    for match in re.finditer(r'\b(?:'+SIGNALS[skill_id]+r')\b', str(text or '').lower()):
        before = str(text or '').lower()[:match.start()].rsplit('.', 1)[-1]
        after = str(text or '').lower()[match.end():]
        negated = bool(
            re.search(r'\b(?:нет|без|никакой|не|не чувствую|не испытываю|не было)\s+(?:(?:у меня|себя|никакой|никакого|больше)\s+)?$', before)
            or re.match(r'\s+(?:(?:больше|сейчас|у меня)\s+)?(?:нет\b|не\s+(?:было|была|был|остал\w*|меша\w*)\b|прошл\w*\b)', after)
        )
        if re.search(r'\bне (?:могу|получается)\s+не\s+$', before):
            negated = False
        if not negated:
            return True
        denied = True
    return False if denied else None


def unsupported_skills(previous, latest):
    result = []
    for skill_id in SIGNALS:
        current = signal_status(latest, skill_id)
        # A current explicit denial supersedes an older positive category.
        if current is not True and (current is False or signal_status(previous, skill_id) is not True):
            result.append(skill_id)
    return result
