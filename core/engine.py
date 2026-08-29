"""Pure behavior engine for bot training flows.

Core logic should stay UI-independent for future app migration.
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from skills import SKILLS_DB, get_current_plan, core_skill_id_for_variant, core_skill_title, variants_for_core_skill, adapt_plan_to_profile


Screen = Dict[str, Any]
UserState = Dict[str, Any]

SKILL_CARD_BUTTONS = [
    "✅ Сделал",
    "🟡 Попробовал, но не вышло",
    "↘️ Нужно проще",
    "🌙 На сегодня достаточно",
]
DONE_BUTTONS = ["🔁 Ещё круг", "🌙 На сегодня хватит"]
DAY_STOP_BUTTONS = ["🧭 Моя карта", "📚 Почему это работает", "🌙 До завтра"]
MAX_CORE_ROUNDS_PER_DAY = 4
DAY_CORE_STOP_TEXT = (
    "На сегодня достаточно.\n\n"
    "Сейчас важнее повторение навыка,\n"
    "а не поиск новой техники.\n\n"
    "Новый навык откроется завтра."
)
FAILED_BUTTONS = ["😣 Слишком сложно", "😵 Нет сил", "📱 Залип", "🤔 Не понял"]
DOWNSCALE_BUTTONS = ["✅ Сделал", "😣 Даже это сложно", "🤔 Зачем так мало?"]
PAY_OFFER_BUTTONS = ["7 дней — €20", "Месяц — €40", "Не сейчас"]

DOWNSCALE_PRIMARY_SKILL = "open_only"
DOWNSCALE_FALLBACK_SKILL = "task_naming"

TRAINER_NAMES = {
    "beck": "Бек",
    "skinny": "Скинни",
    "marsha": "Марша",
}
TRAINER_EMOJIS = {
    "beck": "🐈‍🦁",
    "skinny": "🐈‍⬛",
    "marsha": "🐈",
}


def _screen(
    text: str,
    buttons: Optional[List[str]] = None,
    next_state: Optional[str] = None,
    events: Optional[List[Dict[str, Any]]] = None,
    **extra: Any,
) -> Screen:
    payload: Screen = {
        "text": text,
        "buttons": buttons or [],
        "next_state": next_state,
        "events": events or [],
    }
    payload.update(extra)
    return payload


def _event(name: str, stage: str = "training", meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"name": name, "stage": stage, "meta": meta or {}}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp_text(value: Any, limit: int, fallback: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    return text[:limit]


def _trainer_key(user_state: UserState) -> str:
    key = user_state.get("trainer_key") or "marsha"
    return key if key in TRAINER_NAMES else "marsha"


def _trainer_header(user_state: UserState) -> str:
    key = _trainer_key(user_state)
    return f"{TRAINER_EMOJIS[key]} {TRAINER_NAMES[key]}"


def _skill_steps(skill: Dict[str, Any]) -> List[str]:
    raw_steps = skill.get("steps") or skill.get("simple")
    if isinstance(raw_steps, str):
        candidates = raw_steps.split("→") if "→" in raw_steps else raw_steps.split("\n")
    elif isinstance(raw_steps, list):
        candidates = raw_steps
    else:
        how = skill.get("how") or ""
        candidates = how.split("→") if "→" in how else [how]
    steps = [str(item).strip() for item in candidates if str(item or "").strip()]
    return steps or ["Открой место, где лежит задача."]


def _clean_skill_line(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    for prefix in ("Навык дня:", "Навык:", "Сделай:", "Минимум:", "Попробуй:", "Делаешь только это:"):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip()
    return text


def _unique_skill_steps(skill: Dict[str, Any]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for step in _skill_steps(skill):
        clean = _clean_skill_line(step)
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if len(result) >= 3:
            break
    return result or ["Открой место, где лежит задача."]




def _local_date(user_state: UserState) -> str:
    tz_name = str(user_state.get("timezone") or "Europe/Vilnius").strip()
    try:
        return datetime.now(ZoneInfo(tz_name)).date().isoformat()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()


def _admin_ids() -> set[str]:
    return {x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}


def _test_mode_enabled(user_state: UserState) -> bool:
    if os.getenv("TEST_MODE", "").lower() in {"1", "true", "yes", "on", "debug"}:
        return True
    user_id = str(user_state.get("user_id") or "")
    if user_id not in _admin_ids():
        return False
    return _safe_int(user_state.get("is_test_user"), 0) == 1 or _safe_int(user_state.get("fast_forward_enabled"), 0) == 1


def _locked_day_core_skill_id(user_state: UserState) -> Optional[str]:
    if _test_mode_enabled(user_state):
        return None
    skill_id = user_state.get("day_core_skill_id")
    if user_state.get("day_core_skill_date") == _local_date(user_state) and skill_id in SKILLS_DB:
        return str(skill_id)
    return None


def core_round_count_today(user_state: UserState) -> int:
    if user_state.get("day_core_skill_date") != _local_date(user_state):
        return 0
    return max(0, _safe_int(user_state.get("day_core_round_count"), 0))


def build_day_core_updates(user_state: UserState, skill_id: str, reset_rounds: bool = False) -> Dict[str, Any]:
    today = _local_date(user_state)
    same_lock = user_state.get("day_core_skill_date") == today and user_state.get("day_core_skill_id") == skill_id
    same_visible_core = user_state.get("current_core_skill_date") == today and user_state.get("current_core_skill_id")
    visible_core_id = str(user_state.get("current_core_skill_id") or "") if same_visible_core else core_skill_id_for_variant(skill_id)
    return {
        "day_core_skill_id": skill_id,
        "day_core_skill_date": today,
        "day_core_round_count": 0 if reset_rounds or not same_lock else core_round_count_today(user_state),
        "current_core_skill_id": visible_core_id,
        "current_skill_variant_id": skill_id,
        "current_core_skill_date": today,
    }

def _parse_plan_ids(user_state: UserState) -> List[str]:
    plan = user_state.get("plan")
    if isinstance(plan, list):
        return [sid for sid in plan if isinstance(sid, str)]

    raw_plan = user_state.get("plan_json")
    if raw_plan:
        try:
            parsed = json.loads(raw_plan)
            if isinstance(parsed, list):
                return [sid for sid in parsed if isinstance(sid, str)]
        except Exception:
            return []
    return []


def select_skill(user_state: UserState) -> Dict[str, Any]:
    """Select the current skill without mutating the user state."""
    day = max(1, _safe_int(user_state.get("pending_skill_day") or user_state.get("day"), 1))
    locked_skill_id = _locked_day_core_skill_id(user_state)
    pending_skill_id = user_state.get("pending_skill_id")
    if locked_skill_id:
        skill_id = locked_skill_id
    elif pending_skill_id in SKILLS_DB:
        skill_id = pending_skill_id
    else:
        plan = _parse_plan_ids(user_state)
        if not plan:
            try:
                plan = get_current_plan(user_state)
            except Exception:
                plan = []
        safe_plan = adapt_plan_to_profile([sid for sid in plan if sid in SKILLS_DB], user_state)
        if safe_plan:
            idx = max(0, min(len(safe_plan) - 1, day - 1))
            skill_id = safe_plan[idx]
        else:
            skill_id = next(iter(SKILLS_DB.keys()))

    skill = deepcopy(SKILLS_DB.get(skill_id) or next(iter(SKILLS_DB.values())))
    skill.setdefault("skill_id", skill_id)
    return {"skill_id": skill_id, "skill": skill, "day": day}




BEHAVIOR_ENGINE_ENTITIES = (
    "user",
    "situation",
    "task",
    "context",
    "barrier",
    "hypothesis",
    "micro_skill",
    "behavior_experiment",
    "result",
    "feedback",
    "personal_model",
    "next_recommendation",
)

EXPERIMENT_FEEDBACK_OPTIONS = {
    "completion": ["done", "partially_done", "not_done"],
    "difficulty": ["easy", "ok", "too_hard", "unclear", "no_energy", "no_time"],
    "helpfulness": ["helped", "slightly_helped", "not_helped"],
    "next_outcome": ["continued_task", "stopped_after_microstep", "felt_easier", "felt_more_anxious", "new_barrier"],
}

BARRIER_TO_MECHANISM = {
    "task_too_big": "reduce_task_size",
    "unclear_first_step": "clarify_first_step",
    "fear_of_failure": "reduce_quality_threshold",
    "perfectionism": "reduce_quality_threshold",
    "low_energy": "restore_energy",
    "anxiety": "lower_emotional_arousal",
    "distractibility": "remove_distractions",
    "too_many_decisions": "reduce_decisions",
    "self_criticism": "self_validation",
    "slip_recovery": "return_after_slip",
}

SKILL_ID_TO_BARRIER = {
    "open_only": "task_too_big",
    "open_without_timer": "task_too_big",
    "task_naming": "unclear_first_step",
    "visible_next_step": "unclear_first_step",
    "ninety_sec_start": "task_too_big",
    "bad_first_step": "perfectionism",
    "one_tab_focus": "distractibility",
    "phone_far_3min": "distractibility",
    "restart_after_slip": "slip_recovery",
    "check_the_facts_light": "fear_of_failure",
    "self_criticism_to_instruction": "self_criticism",
    "urge_surf_60": "distractibility",
    "body_before_task": "low_energy",
    "minimum_viable_day": "low_energy",
    "body_doubling_plan": "external_support_needed",
    "if_then_plan": "too_many_decisions",
}


def normalize_micro_skill(skill_id: str, skill: Dict[str, Any]) -> Dict[str, Any]:
    """Return a Behavior Engine skill record without mutating the legacy catalog."""
    raw = dict(skill or {})
    barrier = raw.get("barrier") or raw.get("primary_barrier") or SKILL_ID_TO_BARRIER.get(skill_id, raw.get("mechanism", "start_avoidance"))
    mechanism = raw.get("behavioral_mechanism") or raw.get("mechanism") or BARRIER_TO_MECHANISM.get(str(barrier), "micro_start")
    duration = raw.get("duration") or raw.get("duration_seconds") or "30-180s"
    difficulty = raw.get("difficulty_level") or raw.get("difficulty") or (1 if str(duration).startswith(("30", "60")) else 2)
    minimum = raw.get("minimum_action") or raw.get("minimum") or raw.get("micro") or "Сделать один видимый контакт с задачей."
    return {
        "id": skill_id,
        "title": raw.get("title") or raw.get("name") or skill_id,
        "short_description": raw.get("why_short") or raw.get("goal") or raw.get("explain") or "Микродействие для входа в задачу.",
        "module": raw.get("module") or raw.get("track") or "procrastination",
        "category": raw.get("category") or raw.get("variant") or "micro_start",
        "behavioral_mechanism": mechanism,
        "barrier": barrier,
        "context": raw.get("context") or raw.get("context_tags") or ["any"],
        "difficulty_level": int(difficulty) if str(difficulty).isdigit() else difficulty,
        "duration": duration,
        "steps": _unique_skill_steps(raw),
        "minimum_success_criterion": _clean_skill_line(minimum),
        "contraindications": raw.get("contraindications") or raw.get("limits") or [],
        "simplify_options": raw.get("simplify_options") or raw.get("simpler") or ["Сделать только минимальный критерий."],
        "intensify_options": raw.get("intensify_options") or raw.get("harder") or ["Продолжить ещё 1–3 минуты, если стало легче."],
        "feedback_to_collect": raw.get("feedback_to_collect") or ["completion", "difficulty", "helpfulness", "next_outcome"],
        "tags": raw.get("tags") or [str(barrier), str(mechanism)],
        "source": raw.get("source") or "legacy_skiller_catalog",
        "evidence_level": raw.get("evidence_level") or "expert_hypothesis",
        "real_user_validation_status": raw.get("real_user_validation_status") or "needs_more_data",
    }


def build_behavior_experiment(user_state: UserState, skill_id: str, skill: Dict[str, Any]) -> Dict[str, Any]:
    """Create the structured Context → Barrier → Intervention episode payload."""
    normalized = normalize_micro_skill(skill_id, skill)
    now = datetime.now(timezone.utc).isoformat()
    return {
        "user_id": str(user_state.get("user_id") or user_state.get("telegram_id") or ""),
        "module": normalized["module"],
        "task_type": user_state.get("current_task_object") or user_state.get("task_type") or "unknown",
        "task_text": _clamp_text(user_state.get("today_target") or user_state.get("current_task_title"), 300, ""),
        "barrier": user_state.get("current_barrier") or normalized["barrier"],
        "context": user_state.get("current_task_context") or "unknown",
        "energy": user_state.get("energy"),
        "stress": user_state.get("stress"),
        "available_time_minutes": user_state.get("available_time_minutes"),
        "skill_id": normalized["id"],
        "mechanism": normalized["behavioral_mechanism"],
        "difficulty_level": normalized["difficulty_level"],
        "hypothesis": f"Проверим, поможет ли механизм {normalized['behavioral_mechanism']} с барьером {normalized['barrier']}.",
        "minimum_success_criterion": normalized["minimum_success_criterion"],
        "started": None,
        "completed": None,
        "helpfulness": None,
        "difficulty": None,
        "continued_after_skill": None,
        "continuation_minutes": None,
        "user_feedback": "",
        "created_at": now,
    }


def build_evening_report_from_experiments(experiments: List[Dict[str, Any]]) -> str:
    """Render a short non-judgmental evening report from structured episodes."""
    items = [e for e in experiments or [] if isinstance(e, dict)]
    if not items:
        return "Сегодня данных мало. Завтра можно начать с одного эксперимента на 30 секунд."
    helped = [e for e in items if e.get("helpfulness") in {"helped", "slightly_helped", 4, 5}]
    hard = [e for e in items if e.get("difficulty") in {"too_hard", 4, 5} or e.get("completed") is False]
    if helped:
        best = helped[-1]
        return (
            f"Сегодня вы проверили {len(items)} эксперимент(а). "
            f"Лучше всего сработал механизм «{best.get('mechanism', 'маленький вход')}». "
            "Похоже, его стоит повторить в похожем контексте."
        )
    if hard:
        return (
            f"Сегодня {len(hard)} эксперимент(а) оказались слишком сложными. Это не провал. "
            "Следующий шаг лучше уменьшить до 30 секунд или заменить механизм."
        )
    return f"Сегодня вы собрали данные по {len(items)} эксперимент(а). Завтра используем их для следующей рекомендации."

def _target_header(target: str) -> str:
    target = (target or "").strip()
    if target == "__target_not_selected__":
        return "📌 Дело пока не выбрано\n\nБудем тренироваться\nна типичных ситуациях прокрастинации."
    return f"📌 Дело: {target}"

def build_skill_card(user_state: UserState, skill: Dict[str, Any]) -> Screen:
    """Build a UI-neutral skill card from live skill fields."""
    trainer_key = _trainer_key(user_state)
    steps_text = "\n".join(f"{idx}. {step}" for idx, step in enumerate(_unique_skill_steps(skill), start=1))
    minimum_action = _clean_skill_line(skill.get("minimum_action") or skill.get("minimum") or skill.get("micro") or "Открыть задачу на 30 секунд.")
    why_short = _clean_skill_line(skill.get("why_short") or skill.get("explain") or "Сейчас тренируем вход, а не результат.")
    skill_name = _clean_skill_line(skill.get("name", "Микро-шаг"))
    trainer_variants = skill.get("trainer_variants") or {}
    trainer_line = trainer_variants.get(trainer_key) or trainer_variants.get("marsha") or "Давай бережно: только маленький вход, без давления на результат."
    experiment = build_behavior_experiment(user_state, str(skill.get("skill_id") or ""), skill)
    text = (
        f"{_trainer_header(user_state)} {trainer_line}\n\n"
        f"🧩 Навык: {skill_name}\n\n"
        f"{why_short}\n\n"
        f"Сделай:\n{steps_text}\n\n"
        f"Минимум:\n{minimum_action}"
    )

    return _screen(
        text=text,
        buttons=SKILL_CARD_BUTTONS,
        next_state="training",
        events=[
            _event(
                "skill_card_shown",
                "training",
                {
                    "skill_id": skill.get("skill_id"),
                    "trainer_key": trainer_key,
                    "button_count": len(SKILL_CARD_BUTTONS),
                    "experiment": experiment,
                },
            )
        ],
        skill_id=skill.get("skill_id"),
        experiment=experiment,
    )


def handle_action_result(user_state: UserState, result: str) -> Screen:
    """Handle core action-loop outcomes without Telegram dependencies."""
    day = _safe_int(user_state.get("day"), 1)
    trainer_key = _trainer_key(user_state)
    result = (result or "").strip().lower()
    try:
        skill_id = select_skill(user_state).get("skill_id")
    except Exception:
        skill_id = user_state.get("pending_skill_id") or ""

    if result == "done":
        prompts = {
            "beck": "Факт есть. Подход засчитан.",
            "skinny": "Есть. Один подход. Без разбора.",
            "marsha": "Получилось. Маленький шаг засчитан.",
        }
        return _screen(
            text=prompts[trainer_key],
            buttons=DONE_BUTTONS,
            next_state="waiting_next_day",
            events=[_event("done", "training_done", {"day": day, "skill_id": skill_id, "trainer_key": trainer_key})],
            updates={"done_count_delta": 1, "points_delta": 2, "gamify_reason": "done"},
        )

    if result == "return":
        return _screen(
            text="Возврат засчитан. Это ключевой навык.",
            buttons=DONE_BUTTONS,
            next_state="waiting_next_day",
            events=[_event("return", "training", {"day": day, "skill_id": skill_id, "trainer_key": trainer_key})],
            updates={"return_count_delta": 1, "points_delta": 1, "gamify_reason": "return"},
        )

    if result == "failed":
        return _screen(
            text=(
                "Ок. Это не провал, это сигнал.\n\n"
                "🧭 Добавляю в карту:\n"
                "— текущий шаг мог быть слишком большим\n"
                "— возможно, вход требует ещё меньшего действия\n"
                "— сейчас лучше не давить, а уменьшать масштаб\n\n"
                "Пробуем шаг меньше."
            ),
            buttons=FAILED_BUTTONS,
            next_state="failed_options",
            events=[_event("not_done", "training", {"day": day, "skill_id": skill_id, "trainer_key": trainer_key})],
        )

    return _screen(
        text="Выбери, что сейчас ближе:",
        buttons=FAILED_BUTTONS,
        next_state=user_state.get("stage") or "training",
        events=[],
    )


def handle_downscale(user_state: UserState, reason: str) -> Screen:
    """Build the downscale skill card and state transition."""
    current_core_id = str(user_state.get("current_core_skill_id") or "") if user_state.get("current_core_skill_date") == _local_date(user_state) else ""
    variants = [sid for sid in variants_for_core_skill(current_core_id) if sid in SKILLS_DB]
    skill_id = variants[1] if len(variants) > 1 else (DOWNSCALE_PRIMARY_SKILL if DOWNSCALE_PRIMARY_SKILL in SKILLS_DB else DOWNSCALE_FALLBACK_SKILL)
    skill = deepcopy(SKILLS_DB[skill_id])
    skill.setdefault("skill_id", skill_id)
    local_state = dict(user_state)
    local_state["today_target"] = local_state.get("today_target") or "сегодняшняя задача"
    local_state["skill_variant_label"] = "Упрощение"
    card = build_skill_card(local_state, skill)
    card.update(
        {
            "buttons": DOWNSCALE_BUTTONS,
            "next_state": "downscale_action",
            "skill_id": skill_id,
            "updates": {
                "pending_skill_id": None,
                "pending_skill_day": None,
                "selected_skill": skill_id,
                "current_skill_variant_id": skill_id,
                "pattern": "initiation_before_tool",
                "plan_override_day": _safe_int(user_state.get("day"), 1),
            },
            "events": [
                _event("downscale_triggered", "training", {"reason": reason, "skill": skill_id}),
                *card.get("events", []),
            ],
        }
    )
    return card


def build_day3_summary(user_state: UserState) -> Screen:
    done_count = _safe_int(user_state.get("done_count"), 0)
    return_count = _safe_int(user_state.get("return_count"), 0)
    text = (
        "Итог первых 3 дней:\n\n"
        f"• выполненных подходов: {done_count}\n"
        f"• возвратов к действию: {return_count}\n\n"
        "Главное — не идеальность, а то, что маршрут входа уже появился."
    )
    return _screen(text=text, buttons=[], next_state=user_state.get("stage"), events=[_event("day3_summary_built", "training")])


def should_show_offer(user_state: UserState) -> bool:
    day = _safe_int(user_state.get("day"), 0)
    confirmed_working_skill = bool(
        user_state.get("confirmed_working_skill_exists")
        or user_state.get("last_successful_skill")
        or user_state.get("successful_skills")
    )
    return (
        (day >= _safe_int(user_state.get("offer_earliest_day"), 3) or confirmed_working_skill)
        and (_safe_int(user_state.get("completed_experiments"), 0) >= 2 or confirmed_working_skill)
        and (_safe_int(user_state.get("successful_or_partial"), 0) >= 1 or confirmed_working_skill)
        and bool(user_state.get("personalized_insight_exists"))
        and bool(user_state.get("value_report_seen_at"))
        and not bool(user_state.get("safety_active"))
        and (user_state.get("payment_status") or "trial") != "paid"
        and user_state.get("trial_phase") != "paid"
        and _safe_int(user_state.get("free_mode"), 0) != 1
    )


def build_offer(user_state: UserState) -> Screen:
    text = (
        "Первые 3 дня — это диагностика входа.\n\n"
        "Дальше можно продолжить маршрут с ежедневным сопровождением, адаптацией шагов и вечерними итогами."
    )
    return _screen(
        text=text,
        buttons=PAY_OFFER_BUTTONS,
        next_state="offer",
        events=[_event("offer_shown", "offer", {"day": _safe_int(user_state.get("day"), 0)})],
    )


def get_next_screen(user_state: UserState, event: Dict[str, Any]) -> Screen:
    """Route a user event through UI-independent behavior logic."""
    event_type = (event or {}).get("type")

    if event_type == "target_submitted":
        target = _clamp_text(event.get("text"), 200, "сегодняшняя задача")
        if target.lower() == "пропустить":
            target = "__target_not_selected__"
        selection = select_skill(user_state)
        local_state = dict(user_state)
        local_state["today_target"] = target
        card = build_skill_card(local_state, selection["skill"])
        card["updates"] = {
            "today_target": target,
            "pending_skill_id": None,
            "pending_skill_day": None,
            **build_day_core_updates(user_state, selection["skill_id"]),
        }
        card["events"] = [
            _event("target_set", "training", {"day": selection["day"], "text": target}),
            *card.get("events", []),
        ]
        card["skill_id"] = selection["skill_id"]
        return card

    if event_type == "repeat_skill_card":
        if core_round_count_today(user_state) >= MAX_CORE_ROUNDS_PER_DAY:
            return _screen(
                text=DAY_CORE_STOP_TEXT,
                buttons=DAY_STOP_BUTTONS,
                next_state="waiting_next_day",
                events=[_event("day_core_round_limit_reached", "training", {"round_count": core_round_count_today(user_state)})],
            )
        selection = select_skill(user_state)
        card = build_skill_card(user_state, selection["skill"])
        card["events"] = [_event("done_more_round", "training", {"round_count": core_round_count_today(user_state)}), *card.get("events", [])]
        card["skill_id"] = selection["skill_id"]
        return card

    if event_type == "action_result":
        return handle_action_result(user_state, event.get("result", ""))

    if event_type == "downscale":
        return handle_downscale(user_state, event.get("reason", "manual"))

    if event_type == "day3_summary":
        return build_day3_summary(user_state)

    if event_type == "offer":
        return build_offer(user_state)

    return _screen(
        text="Что дальше?",
        buttons=[],
        next_state=user_state.get("stage") or "training",
        events=[],
    )
