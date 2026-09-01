"""Acceptance regressions for the action-first SKILLER router."""
import unittest

from core.skiller_router import ACTIONS, CLARIFICATION_ANSWERS, DialogState, new_session, route_callback, route_user_input


class SkillerDay1RouterTests(unittest.TestCase):
    def test_finish_is_hard_action(self):
        session = new_session(DialogState.DAY_OPEN)
        result = route_callback(session, "day.finish", callback_id="finish-1")
        self.assertEqual(session["state"], DialogState.DAY_CLOSED.value)
        self.assertNotIn("Что чаще ломает вход", result["text"])

    def test_clarification_is_structured_and_never_a_story(self):
        session = new_session(DialogState.DAY1_CLARIFY)
        result = route_callback(session, "clarify.entry.overload", callback_id="barrier-1")
        self.assertEqual(session["structured_answers"], [{"question_id": "entry_barrier", "answer": "overload"}])
        self.assertEqual(session["clarification_count"], 1)
        self.assertIn("Если бы других задач", result["text"])
        self.assertNotIn("😵 Перегруз", session["case_facts"])

    def test_full_report_is_concrete_and_not_empty(self):
        session = new_session(DialogState.DAY1_INTAKE)
        route_user_input(session, "Не могу начать квартальный отчёт и ухожу в телефон")
        route_callback(session, "clarify.entry.distraction")
        route_callback(session, "clarify.function.stay")
        result = route_callback(session, "diagnosis.full_report")
        self.assertIn("квартальный отчёт", result["text"])
        self.assertIn("8. Ограничение", result["text"])

    def test_text_or_voice_correction_updates_same_case(self):
        session = new_session(DialogState.DAY1_INTAKE)
        route_user_input(session, "Откладываю письмо клиенту")
        route_callback(session, "diagnosis.correct")
        result = route_user_input(session, "Дело не в перегрузе, а в страхе реакции", kind="voice")
        self.assertEqual(len(session["case_facts"]), 2)
        self.assertIn("страхе реакции", session["full_report"])
        self.assertIn("Обновил рабочую карту", result["text"])

    def test_offer_continue_restores_state(self):
        session = new_session(DialogState.DAY1_SUMMARY)
        blocked = route_callback(session, "offer.subscription")
        result = route_callback(session, "offer.continue")
        self.assertEqual(session["state"], DialogState.DAY1_SUMMARY.value)
        self.assertNotIn("Продолжить", session["case_facts"])
        self.assertIn("beta-тест", blocked["text"])
        self.assertIn("бесплат", result["text"])

    def test_offer_back_does_not_render_commercial_menu_in_beta(self):
        session = new_session(DialogState.EXPERIMENT_FEEDBACK)
        route_callback(session, "offer.group")
        menu = route_callback(session, "navigation.back")
        self.assertNotIn("offer.subscription", {action for _, action in menu["buttons"]})
        route_callback(session, "offer.later")
        self.assertEqual(session["state"], DialogState.EXPERIMENT_FEEDBACK.value)

    def test_map_and_resources_actions_render_real_content(self):
        session = new_session(DialogState.DAY1_INTAKE)
        route_user_input(session, "Не могу вернуться к презентации после сообщения в чате")
        route_callback(session, "clarify.entry.distraction")
        route_callback(session, "clarify.function.return")
        full_map = route_callback(session, "map.full")
        resources = route_callback(session, "resources.show")
        self.assertIn("Не могу вернуться к презентации", full_map["text"])
        self.assertIn("Подробный разбор", full_map["text"])
        self.assertIn("не новый эксперимент", resources["text"])

    def test_feedback_is_one_question_and_never_more_than_two(self):
        session = new_session(DialogState.DAY1_SUMMARY)
        route_callback(session, "experiment.start")
        result = route_callback(session, "experiment.done")
        self.assertEqual(session["feedback_questions"], 1)
        self.assertEqual(result["text"], "Что произошло после шага?")
        route_callback(session, "experiment.result.promising")
        self.assertLessEqual(session["feedback_questions"], 2)

    def test_continued_task_is_promising_and_updates_map_once(self):
        session = new_session(DialogState.EXPERIMENT_FEEDBACK)
        session["active_skill"] = "Открыть без таймера"
        route_callback(session, "experiment.result.promising", callback_id="result-1")
        self.assertEqual(session["last_classification"], "PROMISING")
        self.assertEqual(session["skill_map"]["successful_skills"][0]["trials"], 1)

    def test_post_close_extra_is_limited_and_day_stays_closed(self):
        session = new_session(DialogState.DAY_CLOSED)
        route_callback(session, "experiment.extra", callback_id="extra-1")
        route_callback(session, "experiment.extra.done", callback_id="extra-done")
        second = route_callback(session, "experiment.extra", callback_id="extra-2")
        self.assertEqual(session["state"], DialogState.DAY_CLOSED.value)
        self.assertEqual(len(session["post_close_skills"]), 1)
        self.assertIn("уже использован", second["text"])

    def test_callback_retry_is_idempotent_and_telemetered(self):
        session = new_session(DialogState.DAY1_SUMMARY)
        route_callback(session, "experiment.start", callback_id="telegram-42", user_id=9, screen_id="summary")
        retry = route_callback(session, "experiment.start", callback_id="telegram-42", user_id=9, screen_id="summary")
        self.assertEqual(session["experiment_count"], 1)
        self.assertTrue(retry["duplicate"])
        event = session["telemetry"][-1]
        self.assertTrue(event["duplicate"])
        self.assertFalse(event["callback_fell_into_text_router"])

    def test_stale_button_has_safe_equivalent_not_text_analysis(self):
        session = new_session(DialogState.DAY_OPEN)
        result = route_callback(session, "legacy.short_skill", callback_id="old-1")
        self.assertEqual(session["state"], DialogState.EXPERIMENT_ACTIVE.value)
        self.assertIn("Эксперимент", result["text"])

    def test_distinct_double_tap_does_not_duplicate_experiment_or_result(self):
        session = new_session(DialogState.DAY1_SUMMARY)
        route_callback(session, "experiment.start", callback_id="tap-1")
        retry = route_callback(session, "experiment.start", callback_id="tap-2")
        self.assertTrue(retry["duplicate"])
        self.assertEqual(session["experiment_count"], 1)
        route_callback(session, "experiment.done", callback_id="done-1")
        route_callback(session, "experiment.result.promising", callback_id="result-1")
        retry = route_callback(session, "experiment.result.promising", callback_id="result-2")
        self.assertTrue(retry["duplicate"])
        self.assertEqual(session["skill_map"]["successful_skills"][0]["trials"], 1)

    def test_every_stable_action_returns_a_non_dead_end_response(self):
        """Button sweep: every registered callback is action-routed and human-readable."""
        all_actions = ACTIONS | set(CLARIFICATION_ANSWERS) | {
            "clarify.other", "clarify.function.start", "clarify.function.stay",
            "clarify.function.return", "legacy.short_skill", "want_short_skill",
        }
        preferred_state = {
            **{action: DialogState.DAY1_CLARIFY for action in CLARIFICATION_ANSWERS},
            "clarify.other": DialogState.DAY1_CLARIFY,
            "clarify.function.start": DialogState.DAY1_CLARIFY,
            "clarify.function.stay": DialogState.DAY1_CLARIFY,
            "clarify.function.return": DialogState.DAY1_CLARIFY,
            "experiment.done": DialogState.EXPERIMENT_ACTIVE,
            "experiment.result.promising": DialogState.EXPERIMENT_FEEDBACK,
            "experiment.result.partial": DialogState.EXPERIMENT_FEEDBACK,
            "experiment.result.no_effect": DialogState.EXPERIMENT_FEEDBACK,
            "experiment.result.negative": DialogState.EXPERIMENT_FEEDBACK,
            "experiment.extra": DialogState.DAY_CLOSED,
            "experiment.extra.done": DialogState.DAY_CLOSED,
            "offer.continue": DialogState.OFFER,
            "offer.later": DialogState.OFFER,
        }
        for index, action in enumerate(sorted(all_actions)):
            with self.subTest(action=action):
                session = new_session(preferred_state.get(action, DialogState.DAY_OPEN))
                if action in {"offer.continue", "offer.later"}:
                    session["state_before_offer"] = DialogState.DAY1_SUMMARY.value
                result = route_callback(session, action, callback_id=f"sweep-{index}")
                self.assertTrue(result["text"].strip())
                self.assertNotIn("Classification", result["text"])
                self.assertFalse(session["telemetry"][-1]["callback_fell_into_text_router"])


class ThreeJourneyCases(unittest.TestCase):
    def _complete(self, story, barrier, functional, outcome):
        session = new_session()
        first = route_user_input(session, story)
        self.assertTrue(first["buttons"])
        route_callback(session, barrier)
        summary = route_callback(session, functional)
        self.assertEqual(session["state"], DialogState.DAY1_SUMMARY.value)
        self.assertIn(story, session["full_report"])
        self.assertIn("Твоя рабочая карта", summary["text"])
        route_callback(session, "experiment.start")
        route_callback(session, "experiment.done")
        feedback = route_callback(session, outcome)
        self.assertEqual(session["state"], DialogState.DAY_OPEN.value)
        closed = route_callback(session, "day.finish")
        self.assertEqual(session["state"], DialogState.DAY_CLOSED.value)
        self.assertIn("День закрыт", closed["text"])
        return session, feedback

    def test_case_overload_then_continued(self):
        session, feedback = self._complete(
            "Открываю квартальный отчёт, вижу десять задач и ухожу в мессенджер",
            "clarify.entry.overload", "clarify.function.stay", "experiment.result.promising")
        self.assertIn("хороший первый сигнал", feedback["text"])
        self.assertEqual(len(session["skill_map"]["successful_skills"]), 1)

    def test_case_evaluation_fear_then_partial(self):
        session, feedback = self._complete(
            "Третий день не отправляю письмо клиенту, потому что боюсь его реакции",
            "clarify.entry.fear", "clarify.function.start", "experiment.result.partial")
        self.assertIn("STAY", feedback["text"])
        self.assertEqual(len(session["skill_map"]["partial_skills"]), 1)

    def test_case_distraction_then_no_effect(self):
        session, feedback = self._complete(
            "Начинаю читать документ и через минуту автоматически открываю телефон",
            "clarify.entry.distraction", "clarify.function.stay", "experiment.result.no_effect")
        self.assertIn("не дал заметного эффекта", feedback["text"])
        self.assertEqual(len(session["skill_map"]["failed_skills"]), 1)


if __name__ == "__main__":
    unittest.main()
