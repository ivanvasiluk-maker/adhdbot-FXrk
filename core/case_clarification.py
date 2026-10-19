"""Ask for missing reported facts before guessing a functional explanation."""
import json
import re
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from core.experiment_dialogue import CHAIN_LABELS
from core.legacy_flow_adapter import set_legacy_stage
from db import save_user

QUESTIONS = {
    'task': 'Какое одно дело или действие хотите разобрать? Что именно хотите изменить?',
    'situation': 'В какой конкретной ситуации это произошло? Назови один эпизод.',
    'behavior': 'Что вы делали в том эпизоде?',
    'immediate': 'Сразу после того как отложили дело и переключились, что изменилось? Стало легче, тяжелее или ничего не изменилось?',
    'thoughts': 'Какая мысль появлялась перед тем, как отложить это дело?',
    'feelings': 'Что чувствовали, когда собирались приступить к делу, ещё до откладывания?',
    'delayed': 'Что происходило с ситуацией и состоянием позже?',
}

def question_text(comp, field):
    if comp.get('request_kind') in {'relationship', 'other'}:
        return {
            'feelings': 'Какие чувства и ощущения были перед вашими действиями в этом эпизоде?',
            'thoughts': 'Что вы подумали перед тем, как поступить так в этом эпизоде?',
            'immediate': 'Что изменилось сразу после ваших действий в этом эпизоде: стало легче, тяжелее или ничего не изменилось?',
        }.get(field, QUESTIONS.get(field, 'Какое звено нужно исправить?'))
    return QUESTIONS.get(field, 'Какое звено нужно исправить?')

def wants_questions(text):
    return bool(re.search(r'задай\w*\s+(?:мне\s+)?вопрос|задайте\s+(?:мне\s+)?вопрос|спроси\s+меня|спросите\s+меня',str(text or ''),re.I))

def known_facts(chain):
    labels={'context':'Контекст',**CHAIN_LABELS,'resources':'Что уже получается','tried':'Что уже пробовали','self_assessment':'Общая оценка себя — не доказанная причина'}
    return '\n'.join(f'— {label}: {chain[key]}' for key,label in labels.items() if chain.get(key))

async def start_questions(m,u,path,*,require_task=False,detailed=False):
    if not detailed:
        await start_brief(m,u,path,require_task=require_task)
        return
    comp=json.loads(u.get('analysis_json') or '{}')
    chain=comp.get('behavioral_chain') or {}
    if chain.get('task') and (require_task or not u.get('today_target')):
        u['today_target']=str(chain['task'])[:140]
        u['current_task_title']=u['today_target']
    require_task=require_task or bool(comp.get('require_case_task'))
    comp['case_mode']='detailed'
    comp['require_case_task']=require_task
    queue=(['task'] if require_task and not chain.get('task') else [])+[key for key in ('situation','behavior','feelings','thoughts','immediate','delayed') if not chain.get(key)]
    if not queue:
        response='Все шесть звеньев уже описаны. Какое из них нужно уточнить?'
        comp['case_question']='correction'
    else:
        comp['case_question']=queue[0]
        facts=known_facts(chain)
        response=('Уже известные факты:\n'+facts+'\n\n' if facts else '')+'Для вывода нужно уточнить один факт:\n'+question_text(comp,queue[0])
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

async def handle_answer(m,u,text,path,*,is_known_button=None,on_complete=None):
    if u.get('stage')!='reported_case_question' or str(text or '').startswith('/'):
        return False
    if str(text or '').lower().strip(' .!?') in {'стоп','не надо','хватит','убери','на этом пока остановиться'}:
        return False
    comp=json.loads(u.get('analysis_json') or '{}')
    if comp.get('case_mode')=='brief':
        return await handle_brief(m,u,text,path,comp,is_known_button,on_complete=on_complete)
    chain=comp.get('behavioral_chain') or {}
    field=comp.get('case_question')
    if text in {'📚 Подробнее','📚 Почему так?'}:
        await m.answer(known_facts(chain)+'\n\n'+question_text(comp,field),reply_markup=ReplyKeyboardRemove())
        return True
    if wants_questions(text) or not str(text or '').strip() or (is_known_button and is_known_button(text)):
        await m.answer(question_text(comp,field),reply_markup=ReplyKeyboardRemove())
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
        response='Ответ сохранён. Следующий факт:\n'+question_text(comp,queue[0])
        markup=ReplyKeyboardRemove()
    else:
        if on_complete:
            await on_complete(m,u,path,comp)
            return True
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

DEEP = '🔎 Хочу разобраться глубже'
OTHER = '✍️ Другое — расскажу'
START = '▶️ Да, давай начнём'
CHOOSE = '🎯 Помоги выбрать дело'
BARRIERS = ('🟠 Скучно', '🌀 Непонятно, с чего начать', '😟 Боюсь сделать плохо', OTHER)
RELATION_BARRIERS = ('🌊 Сильные эмоции', '💬 Не знаю, что сказать', '😟 Боюсь реакции', OTHER)
BRIEF_BUTTONS = set(BARRIERS + RELATION_BARRIERS + (DEEP, START, CHOOSE))


def keyboard(*labels):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x)] for x in labels],resize_keyboard=True)


def barrier_prompt(comp):
    if comp.get('request_kind')=='relationship':
        return 'Что сейчас труднее всего в этой ситуации?', keyboard(*RELATION_BARRIERS)
    return 'Что труднее всего в начале дела? Можно выбрать ответ или рассказать своими словами.', keyboard(*BARRIERS)


async def start_brief(m,u,path,*,require_task=False):
    comp=json.loads(u.get('analysis_json') or '{}')
    chain=comp.get('behavioral_chain') or {}
    if chain.get('task'):
        u['today_target']=str(chain['task'])[:140]
        u['current_task_title']=u['today_target']
    elif require_task:
        # A new report must not inherit the previous episode's work target.
        u['today_target']=''
        u['current_task_title']=''
    comp['case_mode']='brief'
    comp['case_question']='brief_barrier'
    comp['case_question_queue']=[]
    for key in ('short_conclusion','selected_skill','selected_barrier'):
        comp.pop(key,None)
    comp['analysis_result']={}
    comp['specific_pattern']='Причину ещё уточняем'
    comp['hypothesis_status']='needs_clarification'
    comp['hypothesis_rejected']=True
    u['analysis_json']=json.dumps(comp,ensure_ascii=False)
    u['pending_plan_change']=None
    set_legacy_stage(u,'reported_case_question')
    await save_user(u,path)
    prompt,markup=barrier_prompt(comp)
    await m.answer(prompt,reply_markup=markup)


async def brief_ready(m,u,path,comp,*,on_complete=None):
    if on_complete:
        await on_complete(m,u,path,comp)
        return
    comp.pop('case_question',None)
    comp['selected_skill']='visible_next_step'
    u['pending_skill_id']='visible_next_step'
    set_legacy_stage(u,'analysis_clarify_done')
    u['analysis_json']=json.dumps(comp,ensure_ascii=False)
    await save_user(u,path)
    await m.answer('Возьмём это дело и попробуем один небольшой шаг. После попытки проверим, помог ли он начать.',
                   reply_markup=keyboard('💪 Давай действие',DEEP,'На этом пока остановиться'))


async def handle_brief(m,u,text,path,comp,is_known_button,*,on_complete=None):
    field=comp.get('case_question')
    if text==DEEP:
        await start_questions(m,u,path,detailed=True,require_task=not bool(u.get('today_target')))
        return True
    if text in {'📚 Подробнее','📚 Почему так?'}:
        # Facts are available on request without forcing a six-link interview.
        await m.answer(known_facts(comp.get('behavioral_chain') or {}) or 'Пока уточняем запрос.',reply_markup=keyboard(DEEP,'На этом пока остановиться'))
        return True
    if text==OTHER:
        await m.answer('Что именно мешает? Можно одной фразой или голосом.',reply_markup=keyboard(DEEP,'На этом пока остановиться'))
        return True
    if wants_questions(text) or not str(text or '').strip():
        prompt,markup=barrier_prompt(comp)
        await m.answer(prompt,reply_markup=markup)
        return True
    if field=='brief_barrier' and (text in BARRIERS+RELATION_BARRIERS or not (is_known_button and is_known_button(text))):
        # This is a reported difficulty, never a proven cause or a chain result.
        comp['reported_difficulty']=text.strip()[:320]
        comp['case_question']='brief_ready'
        if comp.get('request_kind')=='relationship':
            response='Попробуем один небольшой шаг. Уже знаете, что хотите сделать или сказать?'
        else:
            response='Попробуем начать с двух минут. Уже знаете, какое дело возьмёте?'
        markup=keyboard(START,CHOOSE,DEEP)
        if not u.get('today_target'):
            comp['case_question']='brief_task'
            response='Какое одно дело возьмём на две минуты? Например: открыть конкретное письмо или документ. Можно текстом или голосом.'
            if comp.get('request_kind')=='relationship':
                response='Что конкретно хотите сделать или сказать в этой ситуации? Можно одной фразой или голосом.'
            markup=keyboard(CHOOSE,DEEP)
    elif field=='brief_ready' and text==START:
        if u.get('today_target'):
            await brief_ready(m,u,path,comp,on_complete=on_complete)
            return True
        comp['case_question']='brief_task'
        response='Какое одно дело возьмёте? Например: открыть конкретное письмо или документ. Можно текстом или голосом.'
        markup=keyboard(DEEP,'На этом пока остановиться')
    elif field in {'brief_ready','brief_task'} and text==CHOOSE:
        comp['case_question']='brief_task'
        comp['case_question']='brief_task_options'
        response='Напишите два-три дела, между которыми выбираете, каждое с новой строки или через точку с запятой. Покажу их отдельно — выберем одно для короткого старта.'
        markup=keyboard(DEEP,'На этом пока остановиться')
    elif field=='brief_task_options' and not (is_known_button and is_known_button(text)) and text not in BRIEF_BUTTONS:
        options=[x.strip()[:140] for x in re.split(r'\n|;',text) if x.strip()][:3]
        if len(options)<2:
            comp.setdefault('behavioral_chain',{})['task']=text.strip()[:140]
            u['today_target']=text.strip()[:140]
            u['current_task_title']=u['today_target']
            await brief_ready(m,u,path,comp,on_complete=on_complete)
            return True
        comp['task_options']={f'🎯 {i+1}. {value[:80]}':value for i,value in enumerate(options)}
        comp['case_question']='brief_task_pick'
        response='С какого дела проще начать одним небольшим действием? Выберите его. Остальные пока оставим в стороне.'
        markup=keyboard(*comp['task_options'],DEEP)
    elif field=='brief_task_pick' and text in (comp.get('task_options') or {}):
        target=comp['task_options'][text]
        comp.setdefault('behavioral_chain',{})['task']=target
        u['today_target']=target
        u['current_task_title']=target
        await brief_ready(m,u,path,comp,on_complete=on_complete)
        return True
    elif field in {'brief_ready','brief_task'} and not (is_known_button and is_known_button(text)) and text not in BRIEF_BUTTONS:
        # A free-form answer may name the task directly, without an extra button.
        target=text.strip()[:140]
        comp.setdefault('behavioral_chain',{})['task']=target
        u['today_target']=target
        u['current_task_title']=target
        await brief_ready(m,u,path,comp,on_complete=on_complete)
        return True
    else:
        prompt,markup=barrier_prompt(comp) if field=='brief_barrier' else ('Назовите одно дело или выберите дальнейший шаг.',keyboard(START,CHOOSE,DEEP))
        await m.answer(prompt,reply_markup=markup)
        return True
    u['analysis_json']=json.dumps(comp,ensure_ascii=False)
    await save_user(u,path)
    await m.answer(response,reply_markup=markup)
    return True
