"""Reported feedback and six-link chains; missing facts stay missing."""
import re

CHAIN_LABELS = {
    'situation': 'Ситуация', 'thoughts': 'Автоматические мысли',
    'feelings': 'Эмоции и ощущения', 'behavior': 'Поведение',
    'immediate': 'Краткосрочный результат', 'delayed': 'Отсроченный результат',
}

def feedback_kind(text):
    low = ' '.join((text or '').lower().replace('ё','е').split()).strip(' .!?')
    patterns = (
        ('worse', r'стало (?:еще )?хуже|усилил(?:ась|ось|ся)|хуже после'),
        ('self_criticism', r'самокритик\w*.*(?:остал|не останов|не прош|не уменьш|не исчез)'),
        ('boring', r'скучно|надоел.*(?:навык|упражнен)|неинтересно.*(?:делать|навык)'),
        ('hard', r'слишком (?:сложно|трудно)|тяжело (?:делать|выполнять) (?:навык|упражнен)'),
        ('different', r'хочу (?:другой|другое|иной) (?:способ|навык|упражнение)|навык не подходит'),
        ('not_helped', r'не помог\w*|не сработал\w*|без изменений'),
        ('not_done', r'не получилось(?: сделать| выполнить)?$|не смог(?:ла)? (?:сделать|выполнить)'),
    )
    return next((kind for kind, pattern in patterns if re.search(pattern,low)),None)


def reported_chain(text, *, avoidance=''):
    """Use explicit sentences and quotes; never infer relief from avoidance."""
    text = ' '.join((text or '').split())
    sentences = [s.strip(' .') for s in re.split(r'[.!?]\s+|\n',text) if s.strip(' .')]
    def find(pattern):
        return next((s[:260] for s in sentences if re.search(pattern,s,re.I)),None)
    quotes = re.findall(r'[«“"]([^»”"]{3,180})[»”"]',text)
    thought = [q for q in quotes if re.search(r'плох|некомпет|решит|ошиб|долж|смогу|подума',q,re.I)]
    if not thought:
        explicit_thought = find(r'\b(?:думаю|мысль|кажется|боюсь,? что)\b')
        thought = [explicit_thought] if explicit_thought else []
    self_assessment = next((item for item in thought if re.search(r'ленив\w*|безвольн\w*',item,re.I)),None)
    if self_assessment:
        thought = [item for item in thought if item != self_assessment]
    situation = find(r'открыва\w*.*(?:презентац|документ|файл|ноутбук|компьютер)|(?:до сдачи|дедлайн|срок)|(?:нужно|надо|хочу|собираюсь|пытаюсь)\s+(?:(?:сегодня|завтра)\s+)?(?:сделать|написать|подготовить|заниматься)')
    if situation:
        situation = re.split(r',?\s+(?:а\s+)?вместо (?:этого|задачи|работы)\b',situation,maxsplit=1,flags=re.I)[0].strip(' ,')
    deadline = find(r'до сдачи|дедлайн|срок')
    if situation and deadline and deadline != situation:
        situation = (situation + '. ' + deadline)[:320]
    intensity = re.search(r'\b(?:тревог\w*|напряж\w*|страх\w*|интенсивность)\s*[:—=-]?\s*(?:10|[0-9])\s*(?:/\s*10|из 10)', text, re.I)
    emotion_roots = {'тревога':'тревог|тревож','напряжение':'напряж','страх':'страх','стыд':'стыд','злость':'злость|злюсь','усталость':'устал','апатия':'апати','паника':'паник'}
    feelings = [word for word,root in emotion_roots.items() if re.search(r'\b(?:'+root+r')\w*', text, re.I) and not re.search(r'\b(?:не|нет|без)\s+(?:'+root+r')\w*', text, re.I)]
    behavior = find(r'закрыва\w*.*(?:документ|файл)|переключа\w*.*(?:YouTube|ютуб|новост|телефон)|(?:вместо|ухожу|смотрю|читаю|завис\w*).*(?:YouTube|ютуб|новост|лент|телефон|Telegram)|иду делать кофе|вместо (?:задачи|работы|этого).+')
    if behavior:
        alternative = re.search(r'вместо (?:задачи|работы|этого)\s+(.+)',behavior,re.I)
        if alternative:
            behavior = re.split(r',\s*(?:у меня|чувствую)|\s+и не могу',alternative.group(1),maxsplit=1,flags=re.I)[0].strip(' ,')
    immediate = find(r'(?:после|сразу|временно|становится).*(?:легче|облегчен|тревог\w*.*снижа|спокойнее)')
    delayed = find(r'самокритик|не выполнен|не сделан|не продвин|ничего не.*(?:сдел|готов)|(?:потом|позже|из-за срок).*(?:тревог|напряж|врем)')
    # A reported later panic is not evidence of anxiety before avoidance.
    later_feelings = ', '.join(feelings) if feelings == ['паника'] and delayed and re.search(r'паник',delayed,re.I) else None
    if later_feelings:
        feelings = []
    resources = find(r'вечер\w*.*(?:делаю|сделал\w*|успева\w*)')
    tried = find(r'пробовал\w*.*(?:ежедневник|Pomodoro|Notion|соцсет)')
    context = find(r'работаю из дома|недел\w*.*(?:развал|откладыва)')
    return {'situation':situation,'thoughts':'; '.join(thought) or None,
            'feelings':', '.join(feelings) or None,'behavior':behavior,
            'immediate':immediate,'delayed':delayed,
            'context':context,'resources':resources,'tried':tried,'self_assessment':self_assessment,'later_feelings':later_feelings,
            'source':'user_report', 'intensity':intensity.group(0) if intensity else None, 'missing':[key for key,value in {
                'situation':situation,'thoughts':thought,'feelings':feelings,'behavior':behavior,'immediate':immediate,'delayed':delayed}.items() if not value]}


def render_chain(chain):
    lines=['Поведенческая цепочка']
    for key,label in CHAIN_LABELS.items():
        lines.append(f'{label}: {chain.get(key) or "ещё не уточнено"}')
    if chain.get('feelings'):
        lines.append('Интенсивность переживаний: '+(chain.get('intensity') or 'ещё не уточнена.'))
    missing=chain.get('missing') or []
    questions={'situation':'В какой момент и с каким конкретным делом это происходит?',
               'thoughts':'Какая мысль появляется, когда собираешься начать дело?',
               'feelings':'Что ты ощущаешь в этот момент?',
               'behavior':'Что ты делаешь вместо задачи?',
               'immediate':'Что меняется сразу после того, как откладываешь дело?',
               'delayed':'Что происходит с делом и состоянием позже?'}
    if missing:
        lines.append('Уточним одно звено: '+questions[missing[0]])
    lines.append('Рабочая гипотеза: '+ ('избегание может закрепляться временным облегчением; проверим это действием, не считая причиной по умолчанию.' if chain.get('behavior') and chain.get('immediate') else 'поддерживающий механизм ещё уточняем; неизвестные последствия не достраиваем.'))
    return '\n'.join(lines)


def adaptation_question(kind, trainer):
    question={
        'boring':'Мешает скука самого упражнения или тревога перед исходной задачей?',
        'self_criticism':'Самокритика мешает продолжать задачу или она есть, но дело уже идёт?',
        'hard':'Что сейчас труднее: объём шага или переживания перед задачей?',
        'not_helped':'Шаг получилось выполнить? А что именно осталось без изменений?',
        'not_done':'Что помешало выполнить шаг: он слишком большой или мешают переживания?',
        'different':'Что не подходит в этом способе? Можно выбрать другой вход без нового разбора.',
    }.get(kind,'Что изменилось после попытки?')
    opening={'marsha':'Поняла. Если этот способ не подошёл, не нужно заставлять себя. Сохраняем твою задачу и меняем подход.',
             'skinny':'Этот вход не сработал. Задача остаётся прежней. Уточним препятствие.',
             'beck':'Это обратная связь о проверке навыка. Отделим выполнение, состояние и продвижение задачи; затем изменим эксперимент.'}
    return opening.get(trainer,opening['marsha'])+'\n\n'+question
