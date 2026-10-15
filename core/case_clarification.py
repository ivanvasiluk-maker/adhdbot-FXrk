"""Ask for missing reported facts before guessing a functional explanation."""
import json
import re
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from core.experiment_dialogue import CHAIN_LABELS
from core.legacy_flow_adapter import set_legacy_stage
from db import save_user

QUESTIONS = {
    'task': 'Какое одно рабочее дело было отложено в то утро? Что именно нужно было сделать?',
    'situation': 'В какой конкретной ситуации это произошло? Назови один эпизод.',
    'behavior': 'Что происходило вместо этого дела в том эпизоде?',
    'immediate': 'Что менялось сразу после откладывания дела: в состоянии, мыслях или ощущениях?',
    'thoughts': 'Какая мысль появлялась именно перед откладыванием дела?',
    'feelings': 'Какие чувства и ощущения были в тот момент?',
    'delayed': 'Что происходило с делом и состоянием позже?',
}

def wants_questions(text):
    return bool(re.search(r'задай\w*\s+(?:мне\s+)?вопрос|задайте\s+(?:мне\s+)?вопрос|спроси\s+меня|спросите\s+меня',str(text or ''),re.I))

def known_facts(chain):
    labels={'context':'Контекст',**CHAIN_LABELS,'resources':'Что уже получается','tried':'Что уже пробовали','self_assessment':'Общая оценка себя — не доказанная причина'}
    return '\n'.join(f'— {label}: {chain[key]}' for key,label in labels.items() if chain.get(key))

async def start_questions(m,u,path,*,require_task=False):
    comp=json.loads(u.get('analysis_json') or '{}')
    chain=comp.get('behavioral_chain') or {}
    if chain.get('task') and (require_task or not u.get('today_target')):
        u['today_target']=str(chain['task'])[:140]
        u['current_task_title']=u['today_target']
    require_task=require_task or bool(comp.get('require_case_task'))
    comp['require_case_task']=require_task
    queue=(['task'] if require_task and not chain.get('task') else [])+[key for key in ('situation','behavior','immediate','thoughts','feelings','delayed') if not chain.get(key)]
    if not queue:
        response='Все шесть звеньев уже описаны. Какое из них нужно уточнить?'
        comp['case_question']='correction'
    else:
        comp['case_question']=queue[0]
        facts=known_facts(chain)
        response=('Уже известные факты:\n'+facts+'\n\n' if facts else '')+'Для вывода нужно уточнить один факт:\n'+QUESTIONS[queue[0]]
    comp['case_question_queue']=queue
    # A rejected or incomplete explanation cannot remain an accepted conclusion.
    comp.pop('short_conclusion',None)
    comp.pop('selected_skill',None)
    comp.pop('selected_barrier',None)
    comp['analysis_result']={}
    comp['specific_pattern']='Причину ещё уточняем'
    comp['hypothesis_rejected']=True
    signals=dict(comp.get('analysis_signals') or {})
    signals['facts']=[str(chain[key]) for key in CHAIN_LABELS if chain.get(key)]
    comp['analysis_signals']=signals
    comp['hypothesis_status']='needs_clarification'
    u['analysis_json']=json.dumps(comp,ensure_ascii=False)
    u['pending_plan_change']=None
    set_legacy_stage(u,'reported_case_question')
    await save_user(u,path)
    await m.answer(response,reply_markup=ReplyKeyboardRemove())

async def handle_answer(m,u,text,path,*,is_known_button=None):
    if u.get('stage')!='reported_case_question' or str(text or '').startswith('/'):
        return False
    if str(text or '').lower().strip(' .!?') in {'стоп','не надо','хватит','убери','на этом пока остановиться'}:
        return False
    comp=json.loads(u.get('analysis_json') or '{}')
    chain=comp.get('behavioral_chain') or {}
    field=comp.get('case_question')
    if text in {'📚 Подробнее','📚 Почему так?'}:
        await m.answer(known_facts(chain)+'\n\n'+QUESTIONS.get(field,'Какое звено нужно исправить?'),reply_markup=ReplyKeyboardRemove())
        return True
    if wants_questions(text) or not str(text or '').strip() or (is_known_button and is_known_button(text)):
        await m.answer(QUESTIONS.get(field,'Какое звено нужно исправить?'),reply_markup=ReplyKeyboardRemove())
        return True
    if field=='correction':
        comp['reported_correction']=text[:320]
    else:
        chain[field]=text.strip()[:320]
        if field=='task':
            u['today_target']=text.strip()[:140]
            u['current_task_title']=u['today_target']
    chain['missing']=[key for key in CHAIN_LABELS if not chain.get(key)]
    comp['behavioral_chain']=chain
    queue=list(comp.get('case_question_queue') or [])[1:]
    comp['case_question_queue']=queue
    if queue:
        comp['case_question']=queue[0]
        response='Ответ сохранён. Следующий факт:\n'+QUESTIONS[queue[0]]
        markup=ReplyKeyboardRemove()
    else:
        comp.pop('case_question',None)
        comp['selected_skill']='visible_next_step'
        u['pending_skill_id']='visible_next_step'
        set_legacy_stage(u,'analysis_clarify_done')
        response='Вот факты из ответов:\n'+known_facts(chain)+'\n\nЭто пока описание эпизода, а не доказанная причина. Можно уточнить его или перейти к одному маленькому действию по выбранной задаче.'
        markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='💪 Давай действие')],[KeyboardButton(text='На этом пока остановиться')]],resize_keyboard=True)
    u['analysis_json']=json.dumps(comp,ensure_ascii=False)
    await save_user(u,path)
    await m.answer(response,reply_markup=markup)
    return True
