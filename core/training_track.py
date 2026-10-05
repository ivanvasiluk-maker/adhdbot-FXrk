"""Daily practice and optional moment help; compact state in dialogue_context."""
import copy
import json
import re
from difflib import SequenceMatcher

TRACK_FIELDS = ('stage','current_state','state_version','current_skill','current_skill_variant','active_flow','active_attempt','current_action_id','current_screen_id',
    'current_action_context','pending_skill_id','pending_skill_day','pending_feedback_json','analysis_json','bucket',
    'plan_json','day','day_number','day_core_skill_id','day_core_skill_date','day_core_round_count',
    'current_core_skill_id','current_skill_variant_id','current_core_skill_date','current_skill_completed_count',
    'daily_skill_id','daily_skill_name','daily_skill_status','current_task_id','current_task_title','today_target',
    'current_next_physical_step','daily_training_completed','day_closed','today_closed','day_status')
BORED_WORDS = {'скучно','это одно и то же','задолбало','надоело','слишком однообразно'}

def context(user):
    raw=user.get('dialogue_context') or {}
    if isinstance(raw,dict):return copy.deepcopy(raw)
    try:return json.loads(raw)
    except (ValueError,TypeError):return {}

def save_context(user, ctx):user['dialogue_context']=json.dumps(ctx,ensure_ascii=False)

def begin_moment(user, today):
    ctx=context(user)
    if ctx.get('moment_training'):return False
    if user.get('day_core_skill_date') != today or not user.get('day_core_skill_id'):return False
    ctx['moment_training']={'date':today,'fields':{k:copy.deepcopy(user.get(k)) for k in TRACK_FIELDS},
                            'screen':{k:copy.deepcopy(ctx.get(k)) for k in ('text','markup','inline')}}
    save_context(user,ctx);return True

def restore_training(user,today):
    ctx=context(user);saved=ctx.pop('moment_training',None)
    if not saved:return None
    if saved['date']==today:
        user.update(saved['fields']);ctx.update(saved['screen'])
    else:
        # Never resurrect yesterday's pending attempt or lock after rollover.
        saved['screen']={};user['stage']='training_main'
    save_context(user,ctx);return saved

def pressure(recent, replies):
    recent=recent[-5:]
    questions=sum('?' in text for text in recent)
    similar=any(SequenceMatcher(None,a,b).ratio()>=.75 for i,a in enumerate(recent) for b in recent[i+1:] if len(a)>30 and len(b)>30)
    return questions>=3 or similar or sum(replies[-5:])>=2

def record_reply(user,text):
    ctx=context(user);low=text.lower().strip(' .!?')
    brief=len(low.split())<=3 and not text.startswith('/')
    ctx['brief_replies']=(list(ctx.get('brief_replies') or [])+[brief])[-5:]
    save_context(user,ctx)
    return low in BORED_WORDS or (brief and pressure(ctx.get('recent') or [],ctx['brief_replies']))

def next_format(user,forced=None):
    ctx=context(user);history=list(ctx.get('action_formats') or [])
    choices=('short','experiment','real')
    chosen=forced or next((x for x in choices if x not in history[-2:]),'short')
    ctx['action_formats']=(history+[chosen])[-5:];save_context(user,ctx)
    return chosen

def action_instruction(minimum, steps):
    action_verbs = r'^(?:откро|откры|най|напиш|напис|полож|сдел|назов|назва|выбер|выбра|убер|убра|закро|закры|посмотр|постав|попроб|ска|ся|остань|пройд|встан|проч)'
    if re.match(action_verbs, str(minimum).lower()):return minimum
    lines=[re.sub(r'^\s*\d+[.)]\s*','',x).strip() for x in str(steps).splitlines() if x.strip()]
    return lines[0] if lines else 'Открой то, что нужно для дела.'

def render_format(minimum,why,format_name):
    title={'short':'Один короткий подход','experiment':'Проверим этот способ','real':'Кусочек настоящего дела','30seconds':'Попробуй в течение 30 секунд'}.get(format_name,'Один шаг сейчас')
    return f'{title}\n{minimum}\n\n{why}\n\nПосле попытки можно остановиться.'
