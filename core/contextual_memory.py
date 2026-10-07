"""Evidence-backed similar situations and explicit hypothesis vetoes; no transcripts."""
from collections import defaultdict

from core.attempt_evidence import ensure_schema
from core.personal_skill_collection import successful


def similar(rows, *, mechanism, target, domain, available, aliases=None):
    """Count only distinct successful applications with all three known coordinates."""
    if not mechanism or not target or domain in {'', 'other', 'general', None}:
        return None
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        identity = row.get('action_id')
        if not identity or identity in seen:
            continue
        seen.add(identity)
        sid = (aliases or {}).get(row.get('skill_id'), row.get('skill_id'))
        if sid in available and row.get('mechanism') == mechanism and row.get('target_function') == target and row.get('context_domain') == domain:
            groups[sid].append(row)
    candidates = []
    for sid, entries in groups.items():
        last = max(entries, key=lambda r: r.get('reported_at') or r.get('shown_at') or '')
        if last.get('helpfulness') == 'worse' or last.get('result') == 'not_completed' or (last.get('helpfulness') == 'not_helped' and last.get('continued') != 1):
            continue
        good = [r for r in entries if successful(r)]
        if good:
            candidates.append(dict(skill_id=sid, count=len(good), mechanism=mechanism, target=target, domain=domain))
    return max(candidates, key=lambda r: (r['count'], r['skill_id']), default=None)


async def load_similar(db, user_id, **coordinates):
    await ensure_schema(db)
    cur = await db.execute('SELECT * FROM attempt_evidence WHERE user_id=? ORDER BY shown_at,action_id', (user_id,))
    columns = [c[0] for c in cur.description]
    return similar([dict(zip(columns, row)) for row in await cur.fetchall()], **coordinates)


def veto(profile, keys, *, reason):
    """A rejection removes the old leader immediately, before further clarification."""
    result = dict(profile or {})
    rejected = dict(result.get('rejected_hypotheses') or {})
    for key in keys:
        if key:
            rejected[key] = {'hypothesis_rejected': True, 'reason': reason}
    result['rejected_hypotheses'] = rejected
    learning = dict(result.get('learning_model') or {})
    scores = dict(learning.get('hypothesis_scores') or result.get('hypothesis_scores') or {})
    for key in rejected:
        scores[key] = 0.0
    learning.update(hypothesis_scores=scores, rejected_hypotheses=list(rejected), primary_hypothesis=None)
    result.update(learning_model=learning, hypothesis_scores=scores, primary_hypothesis=None,
                  main_hypothesis='', main_pattern='', avoidance_pattern='', specific_pattern='', avoidance_behavior='', useful_signal='', hypothesis_rejected=True)
    model = dict(result.get('personal_working_model') or {})
    model.update(confidence='needs_recheck', hypothesis_rejected=True)
    result['personal_working_model'] = model
    conclusion = dict(result.get('conclusion_model') or {})
    hypotheses = [dict(item) for item in conclusion.get('hypotheses', [])]
    primary = max(hypotheses, key=lambda item: {'STRONG_HYPOTHESIS': 4, 'MODERATE_HYPOTHESIS': 3, 'WEAK_HYPOTHESIS': 2, 'UNKNOWN': 1}.get(item.get('status'), 0), default=None)
    if primary is not None:
        primary.update(status='EVIDENCE_AGAINST', evidence_against=[*primary.get('evidence_against', []), 'Вы отвергли этот вывод.'])
        conclusion['hypotheses'] = hypotheses
        conclusion['hypothesis_rejected'] = True
        result['conclusion_model'] = conclusion
    return result
