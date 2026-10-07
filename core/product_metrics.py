"""Auditable cohort measures. Unknown observations remain unknown, never zero."""
from collections import defaultdict
from datetime import date
from core.autonomy import dated_rows
from core.behavior_review import reported
from core.personal_skill_collection import successful, CONTEXTS


def measure(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'rate': round(numerator / denominator, 4) if denominator else None}


def product_metrics(users, rows, today, *, model_assessments=(), memory_assessments=()):
    """Explicit cohort, local dates, user/action uniqueness; no text or identity exported.

    Model/memory assessments are explicit yes/no user judgements with unique IDs,
    not predicted accuracy or claims inferred from an exercise outcome.
    """
    cohort = set(users)
    grouped = defaultdict(list)
    for uid in cohort:
        grouped[uid] = dated_rows([r for r in rows if r.get('user_id') == uid], today)
    active = {r['user_id'] for r in rows if r.get('user_id') in cohort
              and r.get('skill_id') and (r.get('shown_at') or reported(r)) and valid_assessment_date(r.get('calendar_date'), today)}
    adopted, independent, transfer, returning = set(), set(), set(), set()
    reported_users = {uid for uid, rr in grouped.items() if rr}
    continued = assessed = 0
    for uid, rr in grouped.items():
        skills = defaultdict(list)
        days = {r['calendar_date'] for r in rr}
        if len(days) >= 2: returning.add(uid)
        for r in rr:
            if successful(r): skills[r['skill_id']].append(r)
            if r.get('completed') == 1 or r.get('partial') == 1:
                adopted.add(uid)
                if r.get('independent') == 1: independent.add(uid)
            if r.get('continued') in (0, 1):
                assessed += 1
                continued += int(r.get('continued') == 1 and successful(r))
        if any(len({r['context_domain'] for r in applications
                    if r.get('context_domain') in CONTEXTS and r['context_domain'] != 'other'}) >= 2
               for applications in skills.values()): transfer.add(uid)
    def judged(records):
        unique = {(r['user_id'], r['assessment_id']): r for r in records if r.get('assessment_id')
                  and r.get('user_id') in cohort and type(r.get('confirmed')) is bool
                  and valid_assessment_date(r.get('calendar_date'), today)}
        return measure(sum(r['confirmed'] is True for r in unique.values()), len(unique))
    return {'activation': measure(len(active), len(cohort)),
            'skill_adoption': measure(len(adopted), len(reported_users)),
            'independent_use': measure(len(independent), len(reported_users)),
            'generalisation': measure(len(transfer), len(reported_users)),
            'behaviour_change': measure(continued, assessed),
            'model_accuracy': judged(model_assessments), 'memory_value': judged(memory_assessments),
            'retention': measure(len(returning), len(reported_users))}


def text_reduction(before, after):
    """Paired same-scenario typed-character samples; voice and button labels excluded."""
    if set(before) != set(after) or not before:
        raise ValueError('Identical nonempty scenario samples are required')
    if any(type(v) is not int or v < 0 for v in (*before.values(), *after.values())):
        raise ValueError('Character counts must be nonnegative integers')
    baseline = sum(before.values())
    return round(1 - sum(after.values()) / baseline, 4) if baseline else None


def valid_assessment_date(value, today):
    try:
        return date.fromisoformat(value) <= date.fromisoformat(today)
    except (TypeError, ValueError):
        return False
