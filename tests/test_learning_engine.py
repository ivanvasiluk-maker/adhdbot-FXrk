import tempfile
import unittest

from core.experiment_core import BehavioralExperiment
from core.learning_engine import (
    LearningCriteria, LearningSignal, apply_learning_signal, criteria_from_skill, initial_mastery,
    regression_message, scaffolding_instruction,
)
from core.mechanism_model import MechanismHypothesis, SituationSnapshot
from core.ranking_engine import PersonalSkillState, RankingInput, choose_skill
from core.skill_schema import Skill
from db import (
    apply_skill_mastery_signal, create_behavioral_experiment, create_mechanism_hypothesis,
    create_situation_snapshot, get_skill_mastery, init_db,
)


CRITERIA = LearningCriteria(minimum_successes=2, independent_uses_for_generalizing=1)


class LearningEngineTests(unittest.TestCase):
    def test_call_scenario_reduces_scaffolding_then_transfers_and_masters(self):
        state = initial_mastery(1, "make_call")
        self.assertEqual(state.scaffolding_level, "full")

        first = apply_learning_signal(state, LearningSignal(1, "work", successful=True), CRITERIA)
        self.assertEqual((first.state.status, first.state.scaffolding_level), ("LEARNING", "reduced"))

        second = apply_learning_signal(first.state, LearningSignal(2, "work", successful=True), CRITERIA)
        self.assertEqual((second.state.status, second.state.scaffolding_level), ("PRACTICING", "minimal"))

        independent = apply_learning_signal(
            second.state, LearningSignal(3, "work", successful=True, independent=True), CRITERIA,
        )
        self.assertEqual((independent.state.status, independent.state.scaffolding_level), ("GENERALIZING", "none"))

        transfer = apply_learning_signal(
            independent.state,
            LearningSignal(4, "health", successful=True, independent=True, used_without_prompt=True, is_new_context=True),
            CRITERIA,
        )
        self.assertEqual(transfer.state.status, "MASTERED")
        self.assertEqual(transfer.state.generalized_contexts, ("health",))
        self.assertIn("transfer", [event.event_type for event in transfer.events])
        self.assertIn("mastered", [event.event_type for event in transfer.events])

    def test_too_hard_does_not_reset_mastery(self):
        state = apply_learning_signal(
            initial_mastery(1, "make_call"), LearningSignal(1, "work", successful=True), CRITERIA,
        ).state
        failed = apply_learning_signal(
            state, LearningSignal(2, "health", successful=False, failure_reason_code="too_hard"), CRITERIA,
        ).state
        self.assertEqual(failed.successful_practice_count, state.successful_practice_count)
        self.assertEqual(failed.status, state.status)
        self.assertEqual(failed.failed_contexts, ("health",))

    def test_regression_returns_mastered_to_practicing_neutrally(self):
        state = initial_mastery(1, "make_call")
        state = state.__class__(
            **{**state.__dict__, "status": "MASTERED", "scaffolding_level": "none", "successful_practice_count": 3}
        )
        update = apply_learning_signal(state, LearningSignal(5, "work", regression=True), CRITERIA)
        self.assertEqual(update.state.status, "PRACTICING")
        self.assertEqual(update.state.scaffolding_level, "reduced")
        self.assertTrue(update.state.regression_flag)
        self.assertEqual(update.events[0].event_type, "regression")
        text = regression_message()
        self.assertIn("не наказание", text)
        self.assertNotIn("потеря навыка.", text)

    def test_scaffolding_content_shrinks_to_observation_only(self):
        state = initial_mastery(1, "make_call")
        self.assertEqual(scaffolding_instruction(state, full="full example", short="short", prompt="question"), "full example")
        minimal = state.__class__(**{**state.__dict__, "scaffolding_level": "minimal"})
        self.assertEqual(scaffolding_instruction(minimal, full="full", short="short", prompt="question"), "question")
        none = state.__class__(**{**state.__dict__, "scaffolding_level": "none"})
        self.assertIn("самостоятельно", scaffolding_instruction(none, full="full", short="short", prompt="question"))

    def test_mastered_matching_skill_beats_new_skill_in_ranking(self):
        def card(skill_id):
            return Skill(
                skill_id, 2, skill_id, skill_id, "OTHER", ("overwhelm",), ("start",), ("work",),
                (), (), (), (), (), (1,), "min", "standard", "done", (), "mastery", 2,
                "maintain", ("work",), "test", "production", {"marsha": "m", "skinny": "s", "beck": "b"},
            )
        decision, _ = choose_skill([card("mastered"), card("new")], RankingInput(
            {"overwhelm": 1.0}, "start", "work", 1, "marsha",
            personal_states={"mastered": PersonalSkillState("mastered", mastery_status="MASTERED")},
        ))
        self.assertEqual(decision.selected_skill_id, "mastered")

    def test_minimum_success_threshold_comes_from_skill_card(self):
        card = Skill(
            "card", 2, "card", "card", "OTHER", ("overwhelm",), ("start",), ("work",),
            (), (), (), (), (), (1,), "min", "standard", "done", (), "three successes", 3,
            "maintain", ("work",), "test", "production", {"marsha": "m", "skinny": "s", "beck": "b"},
        )
        self.assertEqual(criteria_from_skill(card).minimum_successes, 3)


class LearningPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_state_and_events_are_persisted_with_experiment_reference(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as file:
            await init_db(file.name)
            situation_id = await create_situation_snapshot(file.name, SituationSnapshot(
                None, 7, None, "позвонить", "набрать номер", "work", "start", 40, 50, "today",
            ))
            hypothesis_id = await create_mechanism_hypothesis(file.name, MechanismHypothesis(
                None, situation_id, "evaluation_avoidance", "medium", ("звонок отложен",), (), (), "rules", False,
            ))
            experiment_id = await create_behavioral_experiment(file.name, BehavioralExperiment(
                None, 7, situation_id, "make_call", "evaluation_avoidance", "work", 1,
                "Набери номер", "позвонить", "номер набран", None, None, "proposed", None,
                "first", "MECHANISM_MATCH", "beck", 0,
            ), mechanism_hypothesis_id=hypothesis_id)
            update = await apply_skill_mastery_signal(
                file.name, user_id=7, skill_id="make_call",
                signal=LearningSignal(experiment_id, "work", successful=True, occurred_at="2026-08-06T10:00:00Z"),
                criteria=CRITERIA,
            )
            stored = await get_skill_mastery(file.name, user_id=7, skill_id="make_call")
            self.assertEqual(stored["status"], update.state.status)
            self.assertEqual(stored["successful_practice_count"], 1)


if __name__ == "__main__":
    unittest.main()
