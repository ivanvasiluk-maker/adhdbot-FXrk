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
    "❌ Не сделал",
    "😣 Слишком сложно",
    "🤔 Не понял",
    "🆘 Кризис",
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



def _target_header(target: str) -> str:
    target = (target or "").strip()
    if target == "__target_not_selected__":
        return "📌 Дело пока не выбрано\n\nБудем тренироваться\nна типичных ситуациях прокрастинации."
    return f"📌 Дело: {target}"

def build_skill_card(user_state: UserState, skill: Dict[str, Any]) -> Screen:
    """Build a UI-neutral skill card from live skill fields."""
    trainer_key = _trainer_key(user_state)
    target = _clamp_text(user_state.get("today_target"), 200, "Прокрастинация в целом")
    steps_text = "\n".join(f"{idx}. {step}" for idx, step in enumerate(_skill_steps(skill), start=1))
    minimum_action = skill.get("minimum_action") or skill.get("minimum") or skill.get("micro") or "Открыть задачу на 30 секунд."
    why_short = skill.get("why_short") or skill.get("explain") or "Сейчас тренируем вход, а не результат."
    skill_name = skill.get("name", "Микро-шаг")
    visible_core_id = user_state.get("current_core_skill_id") or core_skill_id_for_variant(str(skill.get("skill_id") or ""))
    visible_core_title = core_skill_title(str(visible_core_id))
    variant_label = user_state.get("skill_variant_label") or "Вариант сейчас"
    trainer_variants = skill.get("trainer_variants") or {}
    trainer_line = trainer_variants.get(trainer_key) or trainer_variants.get("marsha") or "Давай бережно: только маленький вход, без давления на результат."

    if trainer_key == "beck":
        text = (
            f"{_trainer_header(user_state)}\n\n"
            f"{_target_header(target)}\n\n"
            f"🧩 Навык дня: {visible_core_title}\n\n"
            f"{variant_label}:\n{skill_name}\n\n"
            f"{trainer_line}\n\n"
            f"Почему это работает:\n{why_short}\n\n"
            f"Сделай:\n{steps_text}\n\n"
            f"Минимум:\n{minimum_action}"
        )
    elif trainer_key == "skinny":
        text = (
            f"{_trainer_header(user_state)}\n\n"
            f"{_target_header(target)}\n\n"
            f"🧩 Навык дня: {visible_core_title}\n\n"
            f"{variant_label}:\n{skill_name}\n\n"
            f"{trainer_line}\n\n"
            f"Делаешь только это:\n\n{steps_text}\n\n"
            f"Минимум:\n{minimum_action}\n\n"
            "Сделал — вернулся сюда."
        )
    else:
        text = (
            f"{_trainer_header(user_state)}\n\n"
            f"{_target_header(target)}\n\n"
            f"🧩 Навык дня: {visible_core_title}\n\n"
            f"{variant_label}:\n{skill_name}\n\n"
            f"{trainer_line}\n\n"
            f"Попробуй:\n{steps_text}\n\n"
            f"Минимум:\n{minimum_action}\n\n"
            "Если не получится — это не провал, мы просто уменьшим шаг."
        )

    return _screen(
        text=text,
        buttons=SKILL_CARD_BUTTONS,
        next_state="training",
        events=[
            _event(
                "skill_card_shown",
                "training",
                {"skill_id": skill.get("skill_id"), "trainer_key": trainer_key, "button_count": len(SKILL_CARD_BUTTONS)},
            )
        ],
        skill_id=skill.get("skill_id"),
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
            text={
                "beck": "Ок. Это данные. Значит, текущий шаг слишком большой. Уменьшаем.",
                "skinny": "Не сделал — значит шаг большой. Режем задачу.",
                "marsha": "Ок. Это не провал. Похоже, шаг был тяжёлым. Давай сделаем его меньше.",
            }[trainer_key],
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
    local_state["today_target"] = local_state.get("today_target") or "Прокрастинация в целом"
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
    return (
        day == 3
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
        target = _clamp_text(event.get("text"), 200, "Прокрастинация в целом")
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
