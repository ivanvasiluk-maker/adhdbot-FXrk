"""Non-diagnostic situation and behavioral-mechanism model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from core.skill_taxonomy import MECHANISM_CODES

ContextDomain = Literal["work", "study", "relationships", "health", "home", "finance", "other"]
ActionPhase = Literal["start", "continue", "return", "choose", "finish", "rest", "stabilize"]
Confidence = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class MechanismPriority:
    """Keeps an explicit user choice separate from a model inference."""

    primary: str
    secondary: str | None


def prioritize_mechanisms(*, user_selected_mechanism: str, model_inferred_mechanism: str | None) -> MechanismPriority:
    if not user_selected_mechanism:
        raise ValueError("user_selected_mechanism is required")
    secondary = model_inferred_mechanism if model_inferred_mechanism != user_selected_mechanism else None
    return MechanismPriority(primary=user_selected_mechanism, secondary=secondary)

DIAGNOSIS_FIELDS = frozenset({"diagnosis", "diagnostic_label", "disorder", "adhd_type"})

# Mechanism is the primary key. Values are deliberately short candidate classes,
# never day plans or diagnostic cohorts.
MECHANISM_SKILL_MAP: dict[str, tuple[str, ...]] = {
    "evaluation_avoidance": ("check_the_facts_light", "bad_first_step"),
    "executive_start_deficit": ("open_only", "ninety_sec_start"),
    "choice_overload": ("task_naming", "visible_next_step"),
    "low_activation": ("body_before_task", "minimum_viable_day"),
    "rumination": ("one_tab_focus", "ninety_sec_start"),
    "emotional_avoidance": ("urge_surf_60", "open_only"),
    "perfectionism_error_fear": ("bad_first_step", "self_criticism_to_instruction"),
    "attention_drift": ("phone_far_3min", "one_tab_focus"),
    "unclear_next_action": ("visible_next_step", "task_naming"),
    "low_reward": ("ninety_sec_start", "if_then_plan"),
    "overwhelm": ("open_only", "minimum_viable_day"),
    "recovery_after_lapse": ("restart_after_slip", "restart_after_break"),
}


@dataclass(frozen=True)
class SituationSnapshot:
    id: int | None
    user_id: int
    created_at: str | None
    task_summary: str
    desired_action: str
    context_domain: ContextDomain
    action_phase: ActionPhase
    emotion_intensity_0_100: int
    energy_0_100: int
    urgency: str
    raw_text_ref: str | None = None

    def __post_init__(self) -> None:
        for name in ("emotion_intensity_0_100", "energy_0_100"):
            if not 0 <= getattr(self, name) <= 100:
                raise ValueError(f"{name} must be between 0 and 100")
        if not self.task_summary.strip() or not self.desired_action.strip():
            raise ValueError("A concise task summary and desired action are required")


@dataclass(frozen=True)
class MechanismHypothesis:
    id: int | None
    situation_id: int
    mechanism_code: str
    confidence: Confidence
    evidence: tuple[str, ...] = field(default_factory=tuple)
    unknowns: tuple[str, ...] = field(default_factory=tuple)
    disconfirming_questions: tuple[str, ...] = field(default_factory=tuple)
    source: Literal["rules", "llm", "user_confirmed"] = "rules"
    confirmed_by_user: bool = False

    def __post_init__(self) -> None:
        if self.mechanism_code not in MECHANISM_CODES:
            raise ValueError(f"Unknown mechanism: {self.mechanism_code}")
        if any(not str(item).strip() for item in (*self.evidence, *self.unknowns)):
            raise ValueError("Evidence and unknowns must contain explicit non-empty facts")
        if len(self.disconfirming_questions) > 1 and self.confidence == "low":
            raise ValueError("A low-confidence branch may ask at most one clarifying question")


def validate_ranking_features(features: dict) -> None:
    """Prevent diagnoses from becoming a primary skill-ranking key."""
    forbidden = DIAGNOSIS_FIELDS.intersection(features)
    if forbidden:
        raise ValueError(f"Diagnosis cannot rank skills: {', '.join(sorted(forbidden))}")


def select_skill_for_mechanism(
    hypothesis: MechanismHypothesis,
    available_skill_ids: set[str] | frozenset[str],
) -> str:
    """Select from mechanism only; day and diagnosis are not accepted inputs."""
    for skill_id in MECHANISM_SKILL_MAP[hypothesis.mechanism_code]:
        if skill_id in available_skill_ids:
            return skill_id
    raise LookupError(f"No active skill for mechanism {hypothesis.mechanism_code}")


def clarification_question(
    hypothesis: MechanismHypothesis,
    competing_mechanism_codes: tuple[str, ...] = (),
) -> str | None:
    """Ask at most one question only when low-confidence candidates diverge."""
    if hypothesis.confidence != "low" or not competing_mechanism_codes:
        return None
    primary_class = MECHANISM_SKILL_MAP[hypothesis.mechanism_code][0]
    diverges = any(MECHANISM_SKILL_MAP[code][0] != primary_class for code in competing_mechanism_codes)
    if not diverges:
        return None
    return hypothesis.disconfirming_questions[0] if hypothesis.disconfirming_questions else None


def can_start_without_clarification(hypothesis: MechanismHypothesis, *, safety_risk: bool) -> bool:
    """Prefer a minimal experiment whenever it is safe and a skill is testable."""
    return not safety_risk and bool(MECHANISM_SKILL_MAP.get(hypothesis.mechanism_code))


def hypothesis_from_structured_features(situation_id: int, features: dict, *, source: str = "llm") -> MechanismHypothesis:
    """Validate rule/LLM output without accepting diagnoses or invented evidence."""
    validate_ranking_features(features)
    allowed = {"mechanism_code", "confidence", "evidence", "unknowns", "disconfirming_questions"}
    if set(features) - allowed:
        raise ValueError("Structured extraction contains unsupported fields")
    evidence = tuple(features.get("evidence") or ())
    if not evidence:
        raise ValueError("A mechanism requires user-grounded evidence")
    return MechanismHypothesis(
        None, situation_id, str(features.get("mechanism_code") or ""),
        features.get("confidence", "low"), evidence,
        tuple(features.get("unknowns") or ()),
        tuple(features.get("disconfirming_questions") or ())[:1], source, False,
    )


def render_hypothesis(snapshot: SituationSnapshot, hypothesis: MechanismHypothesis) -> str:
    evidence = "; ".join(hypothesis.evidence) or "только описание текущей ситуации"
    unknowns = "; ".join(hypothesis.unknowns) or "нужен результат короткой проверки"
    return (
        f"Ты сообщил: {snapshot.task_summary}; хочешь — {snapshot.desired_action}.\n"
        f"Возможная гипотеза: {hypothesis.mechanism_code}. Основание: {evidence}.\n"
        f"Пока неизвестно: {unknowns}.\n"
        "Проверим это безопасным минимальным экспериментом, а не диагнозом."
    )
