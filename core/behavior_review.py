"""User-confirmed chains and seven local calendar days of distinct attempt facts."""
from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib

from core.attempt_evidence import ensure_schema
from core.personal_skill_collection import successful, CONTEXTS

CHAIN_FIELDS = ('trigger', 'thought', 'behavior', 'immediate', 'later')
CHAIN_LABELS = ('Что произошло', 'Что почувствовали или подумали', 'Что сделали', 'Что получили сразу', 'Что было потом')
POINTS = {'before': ('Перед началом', 'Заранее выбрать одну небольшую часть дела.'),
          'feeling': ('При мысли или чувстве', 'Заметить мысль или чувство и проверить, какой небольшой шаг доступен.'),
          'action': ('В момент действия', 'Заметить остановку и проверить один короткий возврат к делу.')}


async def load_rows(db, user_id, *, aliases=None):
    await ensure_schema(db)
    cur = await db.execute('SELECT * FROM attempt_evidence WHERE user_id=? ORDER BY shown_at,action_id', (user_id,))
    columns = [c[0] for c in cur.description]
    rows = [dict(zip(columns, row)) for row in await cur.fetchall()]
    for row in rows:
        row['skill_id'] = (aliases or {}).get(row['skill_id'], row['skill_id'])
    return rows


def distinct(rows):
    return list({row['action_id']: row for row in rows if row.get('action_id')}.values())


def reported(row):
    return row.get('completed') is not None or row.get('partial') == 1 or row.get('helpfulness') == 'worse' or row.get('source') == 'independent_report'


def period_rows(rows, today):
    last = date.fromisoformat(today); first = last - timedelta(days=6)
    result = []
    for row in distinct(rows):
        try:
            day = date.fromisoformat(row.get('calendar_date') or '')
        except (ValueError, TypeError):
            continue
        if first <= day <= last and reported(row):
            result.append(row)
    return result


def recurrence(rows, *, today, rejected=()):
    groups = defaultdict(list)
    for row in period_rows(rows, today):
        domain, mechanism = row.get('context_domain'), row.get('mechanism')
        if domain not in CONTEXTS or domain == 'other' or not mechanism or mechanism in rejected:
            continue
        # Both are explicit observations, not deductions from relief or a view.
        behavior = 'not_done' if row.get('completed') == 0 and row.get('partial') != 1 else 'not_continued' if row.get('continued') == 0 else ''
        if behavior and row.get('helpfulness') != 'worse':
            groups[(domain, mechanism, behavior)].append(row)
    candidates = [(key, items) for key, items in groups.items() if len(items) >= 3 and len({r['calendar_date'] for r in items}) >= 2]
    if not candidates:
        return None
    (domain, mechanism, behavior), entries = max(candidates, key=lambda item: (len(item[1]), item[0]))
    refs = sorted(r['action_id'] for r in entries)
    signature = hashlib.sha256('|'.join((domain,mechanism,behavior)).encode()).hexdigest()[:16]
    relief = sum(r.get('helpfulness') in {'helped','some'} for r in entries)
    return dict(signature=signature, source_refs=refs, domain=domain, mechanism=mechanism,
                count=len(entries), trigger='', thought='',
                behavior='Шаг не удалось выполнить.' if behavior == 'not_done' else 'После шага дело не продолжилось.',
                immediate=f'Отметок «стало легче»: {relief}.' if relief else '', later='',
                confirmed=False, created_date=today)


def render_chain(chain):
    lines = [f"Схема повторяющейся ситуации · {CONTEXTS.get(chain.get('domain'), 'контекст пока не уточнён')}"]
    if chain.get('count'):
        lines.append(f"Основание: {chain['count']} отдельных попыток за 7 дней к {chain.get('created_date') or 'дате разбора'}.")
    lines.extend(f"{label}: {chain.get(key) or 'пока неизвестно'}" for key,label in zip(CHAIN_FIELDS,CHAIN_LABELS))
    lines.append('Это предварительная схема, а не объяснение причины. Можно исправить один пункт или отказаться.')
    if chain.get('break_at') in POINTS:
        lines.append('Следующая проверка: '+POINTS[chain['break_at']][1])
    return '\n\n'.join(lines)


def week_facts(rows, today):
    entries = period_rows(rows,today)
    good = [row for row in entries if successful(row)]
    skills = Counter(row['skill_id'] for row in good)
    failures = Counter(row.get('target_function') for row in entries if (row.get('completed') == 0 and row.get('partial') != 1) or row.get('continued') == 0)
    return dict(attempts=len(entries), worse=sum(r.get('helpfulness') == 'worse' for r in entries), completed=sum(r.get('completed') == 1 for r in entries),
                partial=sum(r.get('partial') == 1 for r in entries), relief=sum(r.get('helpfulness') in {'helped','some'} for r in entries),
                continued=sum(r.get('continued') == 1 for r in entries),
                execution_unknown=sum(r.get('completed') is None and r.get('partial') != 1 for r in entries),
                continuation_unknown=sum(r.get('continued') is None for r in entries),
                helpful=skills, checks=failures,
                refs={r['action_id'] for r in entries})


def render_week(rows, today, titles, *, chains=(), rejected=()):
    facts=week_facts(rows,today)
    first=(date.fromisoformat(today)-timedelta(days=6)).isoformat()
    lines=[f"Итоги недели · {first} — {today}",
           f"Попыток с ответом: {facts['attempts']}. Полностью: {facts['completed']}; частично: {facts['partial']}.",
           f"Стало легче: {facts['relief']}. Дело продолжилось: {facts['continued']}.",
           f"Выполнение неизвестно: {facts['execution_unknown']}; продолжение неизвестно: {facts['continuation_unknown']}."]
    helped=[f"«{titles[sid]}» — полезных применений: {n}" for sid,n in facts['helpful'].most_common(2) if sid in titles]
    lines.append('Что помогало\n'+('; '.join(helped) if helped else 'Пока нет подтверждённого полезного способа.'))
    confirmed=[c for c in chains if c.get('confirmed') and set(c.get('source_refs') or []) & facts['refs'] and c.get('mechanism') not in rejected]
    if confirmed:
        chain=confirmed[-1]
        lines.append('Сценарий, который вы подтвердили\n'+(chain.get('behavior') or 'Подробности пока не уточнены.'))
    else:
        lines.append('Повторяющийся сценарий пока не подтверждён вами. Не достраиваем его по догадке.')
    selected=next((c for c in reversed(confirmed) if c.get('break_at') in POINTS),None)
    check=POINTS[selected['break_at']][1] if selected else {'START':'Проверить один небольшой вход в конкретное дело.', 'STAY':'Проверить, что поможет продолжить после первого шага.', 'RETURN':'Проверить один короткий возврат после отвлечения.'}.get(facts['checks'].most_common(1)[0][0] if facts['checks'] else '', 'После следующей попытки отметить выполнение и продолжение дела отдельно.')
    if facts['worse']:
        check='Уточнить, что стало хуже. Этот способ пока не повторять.'
    lines.append('Что проверим дальше\n'+check)
    lines.append('Это ваши отметки за 7 дней. Они не доказывают устойчивого изменения или освоения навыка.')
    return '\n\n'.join(lines)


DAILY_SKILL_ALIASES = {
    "task_naming": "name_task_one_word",
    "open_only": "open_without_timer",
    "phone_far_3min": "phone_away_3_min",
    "bad_first_step": "bad_draft",
    "body_before_task": "body_first",
    "one_breath": "body_first",
    "minimum_contact": "body_first",
    "visible_next_step": "one_visible_step",
    "choose_one": "one_visible_step",
    "task_cut": "one_visible_step",
}

