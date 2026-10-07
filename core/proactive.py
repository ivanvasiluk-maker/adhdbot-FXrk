"""Explicit, confirmed intentions; reminder delivery never proves task execution."""
import datetime as dt
import re
from reactivation_service import local_now, parse_dt

ELIGIBLE = {'training', 'training_main', 'day_core_stop', 'success_menu', 'post_action_reflection', 'day_menu', 'existing_user_start_menu', 'new_day_skill', 'current_skill', 'waiting_next_day', 'done', 'morning_checkin', 'evening_checkin', 'evening_not_started', 'morning_new_day'}


def protected(user):
    return (user.get('stage') not in ELIGIBLE or str(user.get('safety_mode') or 'none') not in {'none', 'inactive'}
            or bool(user.get('crisis_mode')) or bool(user.get('pending_feedback_json')))


def intention(text, user, now=None):
    match = re.fullmatch(r'(?:напомни(?:те)?(?: мне)?\s+)?(завтра\s+)?(?:в\s*(\d{1,2}):(\d{2})|после обеда)\s*[,—:-]?\s*(?:я\s+)?(?:сделаю\s+)?(.{3,180})', str(text or '').strip(), re.I)
    if not match:
        return None
    tomorrow, hour, minute, action = match.groups()
    hour, minute = (int(hour), int(minute)) if hour is not None else (13, 0)
    if hour > 23 or minute > 59:
        return None
    current = local_now(user, now)
    due = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if tomorrow or due <= current:
        due += dt.timedelta(days=1)
    return dict(planned_action=action, planned_context='завтра' if tomorrow else 'после обеда' if match.group(2) is None else 'в указанное время',
                approx_time=f'{hour:02d}:{minute:02d}', due_at=due.isoformat(), status='draft')


def due(plan, now):
    scheduled = parse_dt(plan.get('due_at'))
    return bool(plan.get('status') == 'confirmed' and scheduled and scheduled <= now < scheduled + dt.timedelta(hours=4))


def contextual_text(plan):
    return f'Вы планировали: «{plan["planned_action"]}» ({plan["approx_time"]}). Получилось начать?'
