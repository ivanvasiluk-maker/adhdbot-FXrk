"""Return and ability summaries grounded in distinct reported applications."""
from datetime import date
from core.behavior_review import distinct, reported
from core.personal_skill_collection import successful, CONTEXTS


def dated_rows(rows, today):
    limit = date.fromisoformat(today)
    result = []
    for row in distinct(rows):
        try:
            day = date.fromisoformat(row.get('calendar_date') or '')
        except (ValueError, TypeError):
            continue
        if day <= limit and reported(row):
            result.append(row)
    return result


def return_recap(rows, today, label):
    previous = [r for r in dated_rows(rows, today) if r['calendar_date'] < today]
    if not previous:
        return 'Можно начать с одного небольшого шага. Пропущенные дни отрабатывать не нужно.'
    last = max(previous, key=lambda r: (r['calendar_date'], r.get('reported_at') or '', r['action_id']))
    name = label(last['skill_id'])
    if last.get('helpfulness') == 'worse':
        fact = f'После «{name}» стало хуже. Этот способ пока не повторяем.'
    elif last.get('continued') == 1:
        fact = f'После «{name}» дело продолжилось.'
    elif last.get('helpfulness') in {'helped', 'some'}:
        fact = f'После «{name}» стало легче. Продолжение дела не подтверждено.'
    elif last.get('completed') == 0 and last.get('partial') != 1:
        fact = f'«{name}» тогда не получилось выполнить.'
    elif last.get('partial') == 1:
        fact = f'«{name}» получилось выполнить частично.'
    else:
        fact = f'Для «{name}» эффект пока не уточнён.'
    return f'В прошлый раз: {fact}\nПропущенные дни отрабатывать не нужно.'


def ability_progress(rows, today, label):
    entries = dated_rows(rows, today)
    starts = [r for r in entries if r.get('independent') == 1 and r.get('target_function') == 'START'
              and r.get('continued') == 1 and successful(r)]
    lines = []
    if starts:
        lines.append(f'Без подсказки бота получилось начать и продолжить дело: {len(starts)} раз.')
    groups = {}
    for row in entries:
        if successful(row) and row.get('context_domain') in CONTEXTS and row['context_domain'] != 'other':
            groups.setdefault(row['skill_id'], set()).add(row['context_domain'])
    for sid, domains in sorted(groups.items()):
        if len(domains) >= 2:
            lines.append(f'«{label(sid)}» помог в разных ситуациях: ' + ', '.join(CONTEXTS[d].lower() for d in sorted(domains)) + '.')
            break
    return '\n'.join(lines)


def small_challenge(rows, today, sid, label, disabled=()):
    entries = [r for r in dated_rows(rows, today) if r.get('skill_id') == sid]
    if not entries or sid in disabled:
        return ''
    latest = max(entries, key=lambda r: (r['calendar_date'], r.get('reported_at') or '', r['action_id']))
    if (latest.get('helpfulness') == 'worse' or latest.get('completed') == 0
            or (latest.get('helpfulness') == 'not_helped' and latest.get('continued') != 1)
            or not any(successful(r) for r in entries)):
        return ''
    return (f'Необязательная проверка: в следующий раз, когда возникнет похожая трудность, '
            f'попробуйте сами вспомнить «{label(sid)}» до подсказки бота и выбрать одно небольшое действие. '
            'Можно пропустить. Отчёт не обязателен; результат можно записать в «Мои рабочие навыки», если захотите.')
