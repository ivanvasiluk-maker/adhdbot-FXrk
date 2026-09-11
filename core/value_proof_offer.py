"""PATCH-13: offer eligibility based on demonstrated personal value."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.product_config import BASE_OFFER_EUR, OFFER_EARLIEST_DAY, format_eur


@dataclass(frozen=True)
class ValueProof:
    completed_experiments: int
    successful_or_partial: int
    personalized_insight_exists: bool
    user_has_seen_value_report: bool
    safety_active: bool
    current_day: int
    confirmed_working_skill_exists: bool = False


@dataclass(frozen=True)
class OfferEligibility:
    eligible: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class PersonalValueReport:
    barrier: str
    experiment_checked: str
    observed_change: str
    skill_to_consolidate: str


def evaluate_value_proof(proof: ValueProof, *, earliest_day: int = OFFER_EARLIEST_DAY) -> OfferEligibility:
    missing = []
    if proof.completed_experiments < 2 and not proof.confirmed_working_skill_exists:
        missing.append("NEEDS_TWO_COMPLETED_EXPERIMENTS")
    if proof.successful_or_partial < 1 and not proof.confirmed_working_skill_exists:
        missing.append("NO_MEASURED_BENEFIT_YET")
    if not proof.personalized_insight_exists:
        missing.append("NO_PERSONALIZED_INSIGHT")
    if not proof.user_has_seen_value_report:
        missing.append("VALUE_REPORT_NOT_SEEN")
    if proof.safety_active:
        missing.append("SAFETY_ACTIVE")
    if proof.current_day < earliest_day and not proof.confirmed_working_skill_exists:
        missing.append("TOO_EARLY")
    return OfferEligibility(not missing, tuple(missing) if missing else ("VALUE_PROOF_CONFIRMED",))


def render_personal_value_report(report: PersonalValueReport) -> str:
    return (
        "Твои фактические результаты\n\n"
        f"Что мешало: {report.barrier}\n"
        f"Какой эксперимент проверили: {report.experiment_checked}\n"
        f"Что изменилось: {report.observed_change}\n"
        f"Что разумно закрепить: {report.skill_to_consolidate}\n\n"
        "Это рабочий вывод из твоих попыток, а не диагноз."
    )


def render_base_unlock_offer(*, price: Decimal = BASE_OFFER_EUR) -> str:
    return (
        f"Подписка SKILLER Founding Member — €{format_eur(price)} / месяц.\n"
        "Цена сохраняется, пока подписка остаётся активной. Внутри: персональная карта навыков, "
        "история попыток, адаптивные следующие тесты, напоминания, журнал, поведенческие цепочки, "
        "новые навыки и тренеры."
    )


def render_no_value_review() -> str:
    return (
        "Пока недостаточно данных, чтобы честно сказать, что продолжение уже доказало пользу.\n"
        "Вместо продажи можно бесплатно пересмотреть механизм или оставить обратную связь."
    )
