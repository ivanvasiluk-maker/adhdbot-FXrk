import unittest

from core.trainer_voice import (
    VoiceContent,
    day_summary_content,
    experiment_result_content,
    render_message,
)


TRAINERS = ("skinny", "marsha", "beck")


class TrainerVoiceTests(unittest.TestCase):
    def test_strong_success_preserves_same_fact_and_classification(self):
        content = experiment_result_content(
            result="STRONG_SUCCESS", target_function="START", skill_name="Плохой черновик",
            completed=True, effect="helped", after_action="continued_target_task",
        )
        rendered = [render_message(trainer, content) for trainer in TRAINERS]
        self.assertTrue(all(item.result == "STRONG_SUCCESS" for item in rendered))
        self.assertTrue(all(item.target_function == "START" for item in rendered))
        self.assertTrue(all("продолж" in item.text.lower() for item in rendered))

    def test_executed_not_helped_never_claims_success(self):
        content = experiment_result_content(
            result="EXECUTED_ONLY", target_function="START", skill_name="Плохой черновик",
            completed=True, effect="did_not_help", after_action="did_something_else",
        )
        forbidden = ("сработал", "помог", "полезный навык")
        for trainer in TRAINERS:
            text = render_message(trainer, content).text.lower()
            self.assertFalse(any(phrase in text for phrase in forbidden), (trainer, text))

    def test_skinny_is_shorter_than_beck(self):
        content = experiment_result_content(
            result="STRONG_SUCCESS", target_function="START", skill_name="Плохой черновик",
            completed=True, effect="helped", after_action="continued_target_task",
        )
        self.assertLess(len(render_message("skinny", content).text), len(render_message("beck", content).text))

    def test_beck_distinguishes_execution_from_effect(self):
        content = experiment_result_content(
            result="EXECUTED_ONLY", target_function="STAY", skill_name="Одна вкладка",
            completed=True, effect="did_not_help", after_action="did_something_else",
        )
        text = render_message("beck", content).text.lower()
        self.assertIn("исполн", text)
        self.assertIn("эффект", text)

    def test_marsha_failure_is_not_shaming_or_aggressive(self):
        content = experiment_result_content(
            result="FAILED", target_function="START", skill_name="Плохой черновик",
            completed=False, effect="unknown", after_action="unknown",
        )
        text = render_message("marsha", content).text.lower()
        self.assertFalse(any(word in text for word in ("ленив", "слабак", "соберись", "позор", "должен")))
        self.assertIn("не будем", text)

    def test_recent_template_is_not_repeated_immediately(self):
        content = experiment_result_content(
            result="STRONG_SUCCESS", target_function="START", skill_name="Плохой черновик",
            completed=True, effect="helped", after_action="continued_target_task",
        )
        first = render_message("beck", content)
        second = render_message("beck", content, recent_template_ids=[first.template_id])
        self.assertNotEqual(first.template_id, second.template_id)
        self.assertNotEqual(first.text, second.text)

    def test_not_helped_is_not_redefined_as_success(self):
        content = experiment_result_content(
            result="EXECUTED_ONLY", target_function="START", skill_name="Открыть задачу",
            completed=True, effect="did_not_help", after_action="stopped_after_step",
        )
        for trainer in TRAINERS:
            rendered = render_message(trainer, content)
            self.assertEqual(rendered.result, "EXECUTED_ONLY")
            self.assertNotIn("успех", rendered.text.lower())

    def test_start_success_and_stay_failure_shift_to_stay_in_three_voices(self):
        content = day_summary_content(start_skill_name="Плохой черновик", stay_skill_name="Одна вкладка")
        texts = [render_message(trainer, content).text for trainer in TRAINERS]
        self.assertTrue(all("STAY" in text for text in texts))
        self.assertEqual(len(set(texts)), 3)
        self.assertTrue(all(render_message(trainer, content).target_function == "STAY" for trainer in TRAINERS))

    def test_skill_instruction_keeps_core_instruction(self):
        content = VoiceContent(
            "skill_instruction", target_function="START", skill_name="Плохой черновик",
            facts={"instruction": "Напиши одну намеренно плохую строку."},
        )
        for trainer in TRAINERS:
            self.assertIn("Напиши одну намеренно плохую строку.", render_message(trainer, content).text)


if __name__ == "__main__":
    unittest.main()
