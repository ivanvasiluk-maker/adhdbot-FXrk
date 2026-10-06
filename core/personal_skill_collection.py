"""Personal skill facts derived from distinct reported applications, never views."""
from collections import defaultdict

from core.attempt_evidence import ensure_schema

CONTEXTS = {"work": "Работа", "study": "Учёба", "home": "Дом",
            "relationships": "Общение", "health": "Самочувствие", "other": "Другое"}


def successful(row):
    return (row.get("completed") == 1 or row.get("partial") == 1) and row.get("helpfulness") != "worse" and (
        row.get("continued") == 1 or row.get("helpfulness") in {"helped", "some"})


def aggregate(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["skill_id"]].append(row)
    result = []
    for sid, entries in groups.items():
        reports = [r for r in entries if r.get("completed") is not None or r.get("source") == "independent_report"]
        successes = [r for r in reports if successful(r)]
        independent = [r for r in successes if r.get("independent") == 1]
        contexts = sorted({r["context_domain"] for r in successes if r.get("context_domain") in CONTEXTS and r["context_domain"] != "other"})
        dates = {r["calendar_date"] for r in successes if r.get("calendar_date")}
        status = "Познакомились"
        if reports:
            status = "Попробовал"
        if len(reports) >= 2:
            status = "Повторил"
        if independent:
            status = "Применил самостоятельно"
        if independent and len(contexts) >= 2:
            status = "Применил в разных ситуациях"
        if len(successes) >= 3 and len(independent) >= 2 and len(contexts) >= 2 and len(dates) >= 2:
            status = "Освоил"
        last = max(reports, key=lambda r: r.get("reported_at") or r.get("shown_at") or "", default={})
        result.append({"skill_id": sid, "status": status, "applications": len(reports),
            "successful": len(successes), "completed": sum(r.get("completed") == 1 for r in reports),
            "partial": sum(r.get("partial") == 1 for r in reports),
            "relief": sum(r.get("helpfulness") in {"helped", "some"} for r in reports),
            "continued": sum(r.get("continued") == 1 for r in reports),
            "independent": sum(r.get("independent") == 1 for r in reports),
            "independent_successful": len(independent), "contexts": contexts,
            "last_used": last.get("calendar_date") or "", "latest_worse": last.get("helpfulness") == "worse",
            "latest_unsuccessful": last.get("result") == "not_completed" or (last.get("helpfulness") == "not_helped" and last.get("continued") != 1),
            "working_for": sorted({(r.get("mechanism") or "", r.get("target_function") or "") for r in successes
                if r.get("mechanism") and r.get("target_function")})})
    return sorted(result, key=lambda r: (-r["successful"], -r["applications"], r["skill_id"]))


async def load(db, user_id, *, aliases=None):
    await ensure_schema(db)
    cur = await db.execute("SELECT * FROM attempt_evidence WHERE user_id=? ORDER BY shown_at,action_id", (user_id,))
    columns = [c[0] for c in cur.description]
    rows = [dict(zip(columns, row)) for row in await cur.fetchall()]
    for row in rows:
        row["skill_id"] = (aliases or {}).get(row["skill_id"], row["skill_id"])
    return aggregate(rows)


def familiar(collection, mechanism, target, available, disabled=()):
    candidates = [r for r in collection if r["skill_id"] in available and r["skill_id"] not in disabled
        and not r.get("latest_worse") and not r.get("latest_unsuccessful") and r.get("successful", 0) > 0
        and [mechanism, target] in [list(pair) for pair in r.get("working_for", [])]]
    candidates.sort(key=lambda r: (r.get("status") == "Освоил", r.get("independent_successful", 0), r.get("successful", 0)), reverse=True)
    return candidates[0]["skill_id"] if candidates else None


def describe(item, title):
    contexts = ", ".join(CONTEXTS[c] for c in item["contexts"]) or "пока не уточнено"
    caution = "\nВ последней попытке стало хуже — пока не повторяем." if item["latest_worse"] else ""
    return (f"{title}\n{item['status']}\n"
        f"Применений: {item['applications']}. Полностью: {item['completed']}; частично: {item['partial']}.\n"
        f"Стало легче: {item['relief']}. Помогли продолжить дело: {item['continued']}.\n"
        f"Самостоятельно: {item['independent']}. Где помог: {contexts}.\n"
        f"Последнее применение: {item['last_used'] or 'ещё не отмечено'}.{caution}")
