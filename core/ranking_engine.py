"""PATCH-07: deterministic, policy-owned and explainable skill ranking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Sequence

from core.skill_schema import Skill

POLICY_VERSION = "ranking-v1"
FAMILIAR_STATUSES = frozenset({"MASTERED", "GENERALIZING"})
BLOCKED_EFFECTIVENESS = frozenset({"avoid", "unreliable"})


@dataclass(frozen=True)
class PersonalSkillState:
    skill_id: str
    mastery_status: str = "NEW"
    effectiveness_band: str = "unknown"
    attempts_count: int = 0
    successes_count: int = 0
    last_result_successful: bool = False
    recent_failed_exact_variant: bool = False
    recent_repetitions: int = 0
    preferred_trainer_style: str = ""
    recommendation_disabled: bool = False

    @property
    def familiar_working(self) -> bool:
        return self.mastery_status in FAMILIAR_STATUSES or self.effectiveness_band == "working"


@dataclass(frozen=True)
class RankingInput:
    mechanism_probabilities: Mapping[str, float]
    action_phase: str
    context_domain: str
    requested_difficulty: int
    trainer_style: str
    personal_states: Mapping[str, PersonalSkillState] = field(default_factory=dict)
    curriculum_skill_ids: tuple[str, ...] = ()
    active_contraindications: frozenset[str] = frozenset()
    safety_tags: frozenset[str] = frozenset()
    consolidation_required: bool = False
    explicit_override_skill_ids: frozenset[str] = frozenset()
    policy_version: str = POLICY_VERSION


@dataclass(frozen=True)
class RankedCandidate:
    skill_id: str
    selected_difficulty: int
    score: float
    breakdown: Mapping[str, float]
    reason_codes: tuple[str, ...]
    priority_tier: int
    familiar: bool


@dataclass(frozen=True)
class RejectedCandidate:
    skill_id: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class RankingDecision:
    selected_skill_id: str
    selected_difficulty: int
    progression_type: Literal["first", "repeat", "simplify", "advance", "transfer", "maintenance"]
    reason_codes: tuple[str, ...]
    rejected_top_candidates: tuple[RejectedCandidate, ...]
    policy_version: str


def _difficulty(skill: Skill, requested: int) -> int:
    return min(skill.difficulty_levels, key=lambda level: (abs(level - requested), level))


def _mechanism_strength(skill: Skill, probabilities: Mapping[str, float]) -> float:
    return max((float(probabilities.get(code, 0.0)) for code in skill.mechanisms), default=0.0)


def _priority_tier(skill: Skill, state: PersonalSkillState, data: RankingInput) -> int:
    same_mechanism = _mechanism_strength(skill, data.mechanism_probabilities) > 0
    if same_mechanism and state.familiar_working:
        return 1
    if (
        same_mechanism and data.consolidation_required and state.mastery_status == "PRACTICING"
        and state.last_result_successful
    ):
        return 2
    return 3


def _rank_one(skill: Skill, data: RankingInput) -> RankedCandidate:
    state = data.personal_states.get(skill.id, PersonalSkillState(skill.id))
    mechanism_strength = _mechanism_strength(skill, data.mechanism_probabilities)
    mechanism_match = mechanism_strength * 40
    action_phase_match = 20.0 if data.action_phase in skill.action_phases else 0.0
    if state.familiar_working:
        prior_personal_effect = 25.0
    elif state.mastery_status == "PRACTICING" and state.last_result_successful:
        prior_personal_effect = 15.0
    elif state.successes_count:
        prior_personal_effect = 8.0
    else:
        prior_personal_effect = 0.0
    context_match = 10.0 if data.context_domain in skill.contexts else 0.0
    curriculum_hint = 3.0 if skill.id in data.curriculum_skill_ids else 0.0
    trainer_fit = 2.0 if state.preferred_trainer_style == data.trainer_style else 0.0
    selected_difficulty = _difficulty(skill, data.requested_difficulty)
    repetition_penalty = min(20.0, state.recent_repetitions * 5.0)
    difficulty_mismatch_penalty = abs(selected_difficulty - data.requested_difficulty) * 6.0
    breakdown = {
        "mechanism_match": mechanism_match,
        "action_phase_match": action_phase_match,
        "prior_personal_effect": prior_personal_effect,
        "context_match": context_match,
        "curriculum_hint": curriculum_hint,
        "trainer_fit": trainer_fit,
        "contraindication_penalty": 0.0,
        "recent_failed_same_variant_penalty": 0.0,
        "repetition_penalty": repetition_penalty,
        "difficulty_mismatch_penalty": difficulty_mismatch_penalty,
    }
    score = sum(value for key, value in breakdown.items() if not key.endswith("penalty")) - sum(
        value for key, value in breakdown.items() if key.endswith("penalty")
    )
    reasons = []
    if mechanism_match:
        reasons.append("MECHANISM_MATCH")
    if action_phase_match:
        reasons.append("ACTION_PHASE_MATCH")
    if context_match:
        reasons.append("CONTEXT_MATCH")
    if state.familiar_working:
        reasons.append("REUSE_WORKING_SKILL")
    elif state.mastery_status == "PRACTICING" and state.last_result_successful:
        reasons.append("CONSOLIDATE_SUCCESSFUL_PRACTICE")
    else:
        reasons.append("NEW_PRODUCTION_SKILL")
    if curriculum_hint:
        reasons.append("CURRICULUM_HINT")
    if difficulty_mismatch_penalty == 0:
        reasons.append("DIFFICULTY_MATCH")
    return RankedCandidate(
        skill.id, selected_difficulty, score, breakdown, tuple(reasons),
        _priority_tier(skill, state, data), state.familiar_working,
    )


def rank_candidates(skills: Sequence[Skill], data: RankingInput) -> tuple[list[RankedCandidate], list[RejectedCandidate]]:
    """Rank production candidates; safety and negative history are policy gates."""
    ranked: list[RankedCandidate] = []
    rejected: list[RejectedCandidate] = []
    for skill in skills:
        state = data.personal_states.get(skill.id, PersonalSkillState(skill.id))
        override = skill.id in data.explicit_override_skill_ids
        reasons = []
        if skill.quality_status != "production":
            reasons.append("NOT_PRODUCTION")
        if (set(skill.contraindications) & set(data.active_contraindications)) or (
            set(skill.safety_tags) & set(data.safety_tags)
        ):
            reasons.append("SAFETY_CONTRAINDICATION")
        if state.effectiveness_band in BLOCKED_EFFECTIVENESS and not override:
            reasons.append("PERSONAL_EFFECT_BLOCKED")
        if state.recent_failed_exact_variant and not override:
            reasons.append("RECENT_EXACT_VARIANT_FAILED")
        if state.recommendation_disabled and not override:
            reasons.append("USER_DISABLED_RECOMMENDATION")
        if reasons:
            rejected.append(RejectedCandidate(skill.id, tuple(reasons)))
            continue
        ranked.append(_rank_one(skill, data))
    # Tier enforces reuse/consolidation policy. Within a tier, close scores
    # deterministically prefer lower difficulty and familiarity.
    ranked.sort(key=lambda item: (
        item.priority_tier, -round(item.score / 3.0), item.selected_difficulty,
        not item.familiar, -item.score, item.skill_id,
    ))
    rejected.sort(key=lambda item: item.skill_id)
    return ranked, rejected


def choose_skill(skills: Sequence[Skill], data: RankingInput) -> tuple[RankingDecision, list[RankedCandidate]]:
    ranked, rejected = rank_candidates(skills, data)
    if not ranked:
        raise LookupError("No policy-eligible production skill")
    winner = ranked[0]
    state = data.personal_states.get(winner.skill_id, PersonalSkillState(winner.skill_id))
    if winner.priority_tier == 1:
        progression = "transfer" if data.context_domain not in (skills_by_id(skills)[winner.skill_id].generalization_contexts) else "maintenance"
    elif winner.priority_tier == 2:
        progression = "repeat"
    else:
        progression = "first"
    # Auditing needs both policy exclusions and the strongest eligible losers;
    # user-facing rendering never exposes either internal code or score.
    eligible_losers = tuple(
        RejectedCandidate(item.skill_id, ("LOWER_POLICY_RANK", *item.reason_codes))
        for item in ranked[1:4]
    )
    decision = RankingDecision(
        winner.skill_id, winner.selected_difficulty, progression, winner.reason_codes,
        tuple((*rejected, *eligible_losers)[:5]), data.policy_version,
    )
    return decision, ranked


def skills_by_id(skills: Sequence[Skill]) -> dict[str, Skill]:
    return {skill.id: skill for skill in skills}


def explain_decision_for_user(decision: RankingDecision) -> str:
    """Render human rationale without leaking scores or internal reason codes."""
    reasons = set(decision.reason_codes)
    if "REUSE_WORKING_SKILL" in reasons:
        return "В похожей ситуации этот навык уже помогал, поэтому начнём с знакомого способа."
    if "CONSOLIDATE_SUCCESSFUL_PRACTICE" in reasons:
        return "Прошлая попытка была успешной; короткое повторение поможет закрепить навык."
    if "MECHANISM_MATCH" in reasons and "CONTEXT_MATCH" in reasons:
        return "Этот короткий навык подходит к текущему затруднению и ситуации."
    return "Это безопасный небольшой шаг, подходящий для текущей задачи."
