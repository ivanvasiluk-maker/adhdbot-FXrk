"""Separate optional situations from the completed daily training."""
import json
import uuid
from datetime import datetime, timezone


def parsed(value, fallback):
    if isinstance(value, type(fallback)):
        return value
    try:
        result = json.loads(value or '')
        return result if isinstance(result, type(fallback)) else fallback
    except (ValueError, TypeError):
        return fallback


def begin_case(user, profile=None):
    profile = profile or {}
    old = parsed(user.get('current_case_json'), {})
    analysis = parsed(user.get('analysis_json'), {})
    history = list(parsed(user.get('case_history_json'), []))
    if old or analysis:
        history.append({**old, 'analysis': analysis,
                        'historical_hypothesis': profile.get('primary_hypothesis'),
                        'historical_model': profile.get('conclusion_model') or profile.get('personal_working_model')})
    user['case_history_json'] = json.dumps(history[-20:], ensure_ascii=False)
    case = {'case_id': 'case_' + uuid.uuid4().hex, 'opened_at': datetime.now(timezone.utc).isoformat(),
            'status': 'analysis', 'current_case_hypothesis': None, 'daily_core_completed': True}
    user['current_case_json'] = json.dumps(case, ensure_ascii=False)
    user['closed_day_additional_active'] = 1
    # Previous evidence stays in history. Current reasoning starts empty.
    for key in ('analysis_json', 'pending_feedback_json', 'pending_plan_change', 'pending_skill_id',
                'current_skill', 'current_action_id', 'current_action_context', 'last_explanation_context',
                'current_task_id', 'current_task_title', 'today_target', 'current_next_physical_step'):
        user[key] = None
    user['bucket'] = 'mixed'
    user['analysis_action_transition_shown'] = 0
    return case


def sync_case_analysis(user):
    case = parsed(user.get('current_case_json'), {})
    if not case or not user.get('closed_day_additional_active'):
        return
    analysis = parsed(user.get('analysis_json'), {})
    case['analysis_id'] = analysis.get('analysis_id')
    ready = bool(analysis.get('analysis_result'))
    case['current_case_hypothesis'] = (analysis.get('specific_pattern') or analysis.get('hypothesis')) if ready else None
    case['recommended_skill'] = analysis.get('selected_skill') if ready else None
    user['current_case_json'] = json.dumps(case, ensure_ascii=False)
