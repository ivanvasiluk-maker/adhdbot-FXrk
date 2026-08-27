"""PATCH-27 acceptance matrix for the deterministic, offline surfaces."""

import unittest

import bot
from core.content_registry import CONTENT_REGISTRY, ContentRegistry, render_content_suggestion
from core.personal_working_model import render_working_model, update_working_model
from core.post_action_feedback import ReflectionContext, build_post_action_reflection
from core.session_continuity import render_return_continuity, render_session_closure


def reflection(*, completed=False, partial=False, barrier="too_hard"):
    return build_post_action_reflection(ReflectionContext(
        situation="позвонить клиенту", barrier=barrier, skill_title="Проверить вход",
        tested_action="открыть контакт", completed=completed, partial=partial,
        helpfulness="some" if completed or partial else "not_helped", continued=False,
    ))


class NeverDeadEndAcceptanceTests(unittest.TestCase):
    def test_01_done_gets_specific_feedback(self):
        self.assertIn("открыть контакт", reflection(completed=True).render())

    def test_02_not_done_changes_interpretation(self):
        text = reflection().render()
        self.assertIn("не сработал", text)
        self.assertIn("не повторить то же самое", text)

    def test_03_partial_is_not_full_failure(self):
        self.assertTrue(reflection(partial=True).reaction.startswith("Получилось"))

    def test_04_substantive_message_beats_closed_day(self):
        self.assertTrue(bot.closed_day_substantive_message("Снова не могу начать отчёт"))

    def test_05_closed_day_keeps_actions(self):
        buttons = {button.text for row in bot.kb_completed_day_open.keyboard for button in row}
        self.assertIn("🎯 Разобрать ещё одну ситуацию", buttons)

    def test_06_next_return_uses_memory(self):
        self.assertIn("В прошлый раз", render_return_continuity("помогло открыть файл"))

    def test_07_one_case_is_hypothesis(self):
        model = update_working_model({}, barrier="неясность", skill_title="Шаг", context="work", successful=True, evidence_ref="e1")
        self.assertIn("гипотеза", render_working_model(model.as_dict()))

    def test_08_repeated_case_is_visible(self):
        model = {}
        for ref in ("e1", "e2"):
            model = update_working_model(model, barrier="неясность", skill_title="Шаг", context="work", successful=True, evidence_ref=ref).as_dict()
        self.assertIn("повторяется", render_working_model(model))

    def test_09_reviewed_material_is_available(self):
        self.assertIsNotNone(CONTENT_REGISTRY.select(barrier_type="too_hard"))

    def test_10_missing_material_never_becomes_a_dead_end(self):
        text = render_content_suggestion(ContentRegistry().select(barrier_type="missing"))
        self.assertIn("закрепить сегодняшний навык", text)
        self.assertNotIn("нет подходящего", text)

    def test_11_session_has_one_anchor(self):
        self.assertEqual(reflection(completed=True).render().count("Запомнить:"), 1)

    def test_12_closure_is_not_terminal(self):
        text = render_session_closure("помог конкретный вход")
        self.assertIn("продолжить", text)
        self.assertNotIn("нельзя", text)


if __name__ == "__main__":
    unittest.main()
