"""A reported episode informs a testable skill plan, never a diagnosis."""
import re


def case_plan(comp):
    chain=comp.get('behavioral_chain') or {}
    difficulty=str(comp.get('reported_difficulty') or '')
    before=str(chain.get('feelings') or '')
    thought=str(chain.get('thoughts') or '')
    after=str(chain.get('immediate') or '')
    task=str(chain.get('task') or '')
    # Timing comes from the explicit before/after questions, not an emotion's name.
    relief=bool(re.search(r'облегчен|стало (?:легче|спокойнее)|становится (?:легче|спокойнее)',after,re.I)) and not re.search(r'не (?:стало|становится|почувствовал)|нет облегчения|без облегчения|никакого облегчения|облегчен\w*.{0,12}не (?:было|почувств)',after,re.I)
    low=' '.join((difficulty,before,thought)).lower()
    perfection=bool(re.search(r'сдела\w* плохо|ошиб\w*|идеаль\w*|оцен\w*',low)) and not re.search(r'не боюсь|страха нет',low)
    boredom=bool(re.search(r'скуч\w*|неинтерес\w*',low)) and not re.search(r'не скучно|скуки нет',low)
    if comp.get('request_kind')=='relationship':
        kind='specific_action'
        candidates=['visible_next_step']
        hypothesis='Выберем одно собственное действие в разговоре, не угадывая намерения другого человека.'
        reason='Сделаем следующий шаг конкретным и выполнимым; реакцию другого человека заранее не знаем.'
    elif perfection:
        kind='evaluation_avoidance'
        candidates=['bad_first_step','open_only']
        hypothesis='Возможно, ожидание плохого результата или оценки мешает начать. Проверим возможность сделать черновой шаг без оценки качества.'
        reason='Черновик отделяет начало работы от требования сразу сделать хорошо.'
    elif boredom or relief:
        kind='start_avoidance'
        candidates=['ninety_sec_start','open_only']
        hypothesis=('Возможно, откладывание закрепляется кратким облегчением: напряжение снижается сейчас, а цена появляется позже.' if relief else 'Пока известно, что вход в дело сопровождается скукой. Проверим, станет ли легче начать с очень короткой попытки.')
        reason='Короткая попытка ограничивает обязательство: нужно начать, а не закончить всё дело.'
    else:
        kind='specific_action'
        candidates=['visible_next_step','open_only']
        hypothesis='Причину пока не установили. Проверим более узкую гипотезу: поможет ли конкретный первый шаг начать выбранное дело.'
        reason='Один видимый шаг делает начало понятнее и позволяет проверить результат без большого плана.'
    evidence=[]
    if difficulty:evidence.append('Названная трудность: «'+difficulty[:100]+'».')
    if before:evidence.append('Перед началом: «'+before[:120]+'».')
    if thought:evidence.append('Перед откладыванием — мысль: «'+thought[:120]+'».')
    if after:evidence.append('Сразу после переключения: «'+after[:120]+'».')
    if chain.get('resources'):evidence.append('Что уже получается: «'+str(chain['resources'])[:130]+'».')
    if chain.get('delayed'):evidence.append('Позже: «'+str(chain['delayed'])[:150]+'».')
    return dict(kind=kind,candidates=candidates,hypothesis=hypothesis,reason=reason,
                evidence=evidence,task=task,relief_reported=relief,
                criterion='После попытки отдельно проверим: выполнен ли шаг, началось ли дело и стало ли легче. Если не помогло, уточним препятствие и изменим способ.')


def render_case_plan(plan,skill_name):
    evidence='\n'.join(plan['evidence']) or 'Пока известно только выбранное дело.'
    return ('Что удалось понять\n'+evidence+'\n\nРабочая гипотеза\n'+plan['hypothesis']+
            '\n\nЧто попробуем\n'+skill_name+'. '+plan['reason']+'\nЗадача: «'+plan['task'][:140]+'».\n\n'+plan['criterion'])


def render_case_action(plan, skill_id, skill_name):
    task=plan['task'][:140]
    if skill_id=='ninety_sec_start':
        steps='1. Откройте место, где будете делать эту задачу.\n2. Поставьте таймер на 90 секунд и сделайте самый маленький рабочий шаг.\n3. После сигнала можно остановиться. Заканчивать всё дело сейчас не требуется.'
    elif skill_id=='bad_first_step':
        steps='1. Откройте место задачи.\n2. Сделайте один намеренно черновой фрагмент: предложение, пункт или набросок.\n3. Пока не исправляйте и не отправляйте его.'
    elif skill_id=='open_only':
        steps='Только откройте место задачи: нужный документ, письмо или проект. Продолжать пока не требуется.'
    else:
        steps='Назовите одно физически выполнимое действие для этой задачи. Сделайте только его; остальное пока не требуется.'
    return skill_name+'\nЗадача: «'+task+'».\n\n'+steps+'\n\nПосле попытки отдельно отметим: получилось ли выполнить шаг, продолжилось ли дело и изменилось ли самочувствие.'
