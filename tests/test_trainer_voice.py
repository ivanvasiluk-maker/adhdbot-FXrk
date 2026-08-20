import unittest

from core.trainer_voice import VoiceContent, generative_renderer_contract, render_message


def result_content(result="STRONG_SUCCESS", effect="helped", after="continued_target_task"):
    return VoiceContent(
        message_type="experiment_result", result=result, target_function="START",
        skill_name="Плохой черновик",
        facts={"completed": result != "FAILED", "effect": effect, "after_action": after},
        core_message="immutable", next_action="test STAY",
    )


class TrainerVoiceTests(unittest.TestCase):
    def test_same_strong_success_fact_and_classification_for_all_trainers(self):
        rendered = [render_message(name, result_content()) for name in ("skinny", "marsha", "beck")]
        self.assertTrue(all(item.result == "STRONG_SUCCESS" for item in rendered))
        self.assertTrue(all(item.facts["after_action"] == "continued_target_task" for item in rendered))
        self.assertTrue(all("продолж" in item.text.lower() and "задач" in item.text.lower() for item in rendered))

    def test_not_helped_executed_only_never_claims_success(self):
        content = result_content("EXECUTED_ONLY", "not_helped", "did_something_else")
        for trainer in ("skinny", "marsha", "beck"):
            text = render_message(trainer, content).text.lower()
            for forbidden in ("сработал", "помог", "полезный навык"):
                self.assertNotIn(forbidden, text)

    def test_skinny_is_shorter_than_beck(self):
        content = result_content("WEAK_SUCCESS", "some", "stopped_after_step")
        self.assertLess(len(render_message("skinny", content).text), len(render_message("beck", content).text))

    def test_beck_distinguishes_execution_from_effect(self):
        text = render_message("beck", result_content("EXECUTED_ONLY", "not_helped", "did_something_else")).text
        self.assertIn("различить", text)
        self.assertIn("исполн", text)
        self.assertIn("эффект", text)

    def test_marsha_failure_is_non_shaming(self):
        text = render_message("marsha", result_content("FAILED", "not_helped", "not_executed")).text.lower()
        self.assertNotIn("соберись", text)
        self.assertNotIn("лен", text)
        self.assertNotIn("провал", text)

    def test_variant_does_not_repeat_within_five_messages(self):
        history = []
        texts = []
        for _ in range(6):
            rendered = render_message("beck", result_content(), recent_variant_ids=history)
            history.append(rendered.variant_id)
            texts.append(rendered.text)
        self.assertEqual(len(texts), len(set(texts)))

    def test_not_helped_is_not_redefined_as_success(self):
        content = result_content("EXECUTED_ONLY", "not_helped", "did_something_else")
        self.assertTrue(all(render_message(t, content).result == "EXECUTED_ONLY" for t in ("skinny", "marsha", "beck")))

    def test_start_to_stay_summary_has_same_facts_in_distinct_voices(self):
        content = VoiceContent(
            message_type="summary", result="EXECUTED_ONLY", target_function="STAY",
            facts={"strong_skill": "Плохой черновик", "unconfirmed_skill": "Одна вкладка"},
        )
        texts = [render_message(t, content).text for t in ("skinny", "marsha", "beck")]
        self.assertEqual(3, len(set(texts)))
        self.assertTrue(all("Плохой черновик" in text and "Одна вкладка" in text for text in texts))
        self.assertTrue(all("удерж" in text for text in texts))

    def test_generative_contract_separates_facts_and_style(self):
        contract = generative_renderer_contract("beck", result_content("EXECUTED_ONLY"))
        self.assertIn("FACTS THAT MUST NOT CHANGE", contract)
        self.assertIn("STYLE INSTRUCTIONS", contract)
        self.assertIn("result=EXECUTED_ONLY", contract)


if __name__ == "__main__":
    unittest.main()
