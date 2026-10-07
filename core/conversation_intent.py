"""Explicit intent with pending dialogue ownership; no diagnosis or result inference."""
import re

FEEDBACK_STAGES = frozenset({'minimal_feedback_help', 'minimal_feedback_next', 'minimal_feedback_done', 'feedback_partial_text', 'skill_result_feedback', 'skill_done_effect', 'skill_done_effect_text', 'skill_obstacle', 'skill_obstacle_other', 'post_action_reflection', 'recovery_context'})
NAVIGATION_STAGES = frozenset({'training', 'training_main', 'day_core_stop', 'success_menu', 'day_closed_menu', 'closed_day_new_situation', 'request_task', 'intent_choose', 'intent_understand_context', 'intent_understand_result', 'intent_act_context', 'intent_paused'})
STOP_WORDS = frozenset({'стоп', 'stop', 'остановиться', 'хватит', 'не сейчас', 'на этом пока остановиться', 'мне достаточно', 'пока достаточно', 'до свидания', 'до встречи', 'пока', 'на сегодня все', 'спасибо, на сегодня все'})
ACKNOWLEDGE_WORDS = frozenset({'спасибо', 'спасибо большое', 'благодарю', 'понятно', 'понял', 'поняла', 'ясно'})
CONTINUE_WORDS = frozenset({'продолжить', 'продолжим', 'давай дальше'})


def resolve_intent(text, stage='', session=None):
    low = re.sub(r'\s+', ' ', (text or '').lower().replace('ё', 'е')).strip(' .!?')
    session = session or {}
    if stage == 'offer_request_form':
        return 'APPLICATION_DATA'
    # A paused screen owns navigation even while its frozen session awaits feedback.
    if stage == 'intent_paused':
        if low in STOP_WORDS:
            return 'STOP'
        if low in CONTINUE_WORDS:
            return 'CONTINUE'
        if low in ACKNOWLEDGE_WORDS:
            return 'ACKNOWLEDGE'
    if stage in FEEDBACK_STAGES and low in {'стоп', 'stop', 'остановиться', 'хватит'}:
        return 'STOP'
    if stage in FEEDBACK_STAGES or session.get('state') == 'EXPERIMENT_FEEDBACK':
        return 'FEEDBACK_REPLY'
    if session.get('awaiting_target') or session.get('awaiting_barrier') or session.get('state') in {'DAY1_INTAKE', 'DAY1_CLARIFY', 'CORRECTION_INPUT', 'NEW_CASE_INTAKE'}:
        return 'PENDING_REPLY'
    if stage not in NAVIGATION_STAGES:
        return 'PENDING_REPLY'
    if low in STOP_WORDS:
        return 'STOP'
    if stage in {'training', 'training_main', 'day_core_stop', 'success_menu', 'day_closed_menu', 'intent_understand_result', 'intent_paused'} and low in ACKNOWLEDGE_WORDS:
        return 'ACKNOWLEDGE'
    if low in CONTINUE_WORDS:
        return 'CONTINUE'
    if text in {'🧠 Понять, что происходит', 'Хочу понять, что происходит'} or re.match(r'^(?:почему\b|хочу понять\b|помоги понять\b|объясни,? почему\b)', low):
        return 'UNDERSTAND'
    if text in {'⚡ Сделать что-то сейчас', 'Помоги сделать что-то сейчас', '⚡ Давай попробуем'} or re.match(r'^(?:помоги (?:мне )?(?:сейчас )?(?:начать|сделать|вернуться)|(?:мне )?(?:нужно|надо) сейчас (?:начать|сделать))\b', low):
        return 'ACT_NOW'
    if low in {'помоги', 'помоги мне', 'нужна помощь', 'что дальше'}:
        return 'CHOOSE_MODE'
    return 'OTHER'
