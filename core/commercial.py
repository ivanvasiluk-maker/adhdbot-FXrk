"""Conservative automatic invitations grounded in reported usefulness."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from core.autonomy import dated_rows
from core.personal_skill_collection import successful


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def commercial_blocked(user, profile, *, now=None):
    now = now or datetime.now(timezone.utc)
    stage = str(user.get('stage') or '')
    feedback = profile.get('last_skill_feedback') or {}
    review = profile.get('last_day_review') or {}
    if (user.get('crisis_mode') in (1, '1') or 'crisis' in stage or 'safety' in stage
            or stage in {'worse_followup', 'intent_paused', 'offer_request_form'}
            or (isinstance(feedback, dict) and (feedback.get('helpfulness') == 'worse'
                or (feedback.get('completed') in (False, 0) and feedback.get('partial') not in (True, 1))
                or (feedback.get('helpfulness') == 'not_helped' and feedback.get('continued_after_skill') not in (True, 1))
                or str(feedback.get('skill_id') or '').upper() in {'STOP', 'DBT_STOP'}))
            or (isinstance(review, dict) and review.get('state') in {'напряжённо', 'почти не было сил'})):
        return True
    for raw in (profile.get('offer_suppressed_until'), user.get('offer_suppressed_until')):
        until = timestamp(raw)
        if until and until > now:
            return True
    declined = timestamp(profile.get('offer_declined_at'))
    if declined and now - declined < timedelta(days=7):
        return True
    try:
        zone = ZoneInfo(str(user.get('timezone') or 'Europe/Vilnius'))
    except (ValueError, KeyError):
        zone = timezone.utc
    today = now.astimezone(zone).date()
    return any(shown and shown.astimezone(zone).date() >= today for shown in
               (timestamp(profile.get('offer_seen_at')), timestamp(user.get('last_offer_shown_at'))))


def offer_evidence(rows, today):
    rows = dated_rows(rows, today)
    latest = max(rows, key=lambda r: (r['calendar_date'], r.get('reported_at') or '', r['action_id']), default={})
    return {'useful_applications': sum(successful(r) for r in rows),
            'latest_worse': latest.get('helpfulness') == 'worse',
            'latest_failed': (latest.get('completed') == 0 and latest.get('partial') != 1)
                or (latest.get('helpfulness') == 'not_helped' and latest.get('continued') != 1)}


def support_ceiling(rows, today, chains):
    """A confirmed recurrence after executed trials; never infer failed character."""
    entries = {r['action_id']: r for r in dated_rows(rows, today)}
    for chain in chains:
        if not isinstance(chain, dict) or not chain.get('confirmed'):
            continue
        trials = [entries[ref] for ref in set(chain.get('source_refs') or []) if ref in entries
                  and entries[ref].get('completed') == 1 and entries[ref].get('continued') == 0
                  and entries[ref].get('helpfulness') != 'worse']
        if len(trials) >= 3 and len({r['calendar_date'] for r in trials}) >= 2:
            return (f'В подтверждённом вами сценарии после {len(trials)} выполненных проб дело не продолжилось. '
                    'Можно продолжать проверять способы здесь или обсудить эту точку с Иваном. '
                    'Это не означает, что самостоятельная работа вам недоступна.')
    return ''
