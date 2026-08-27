import unittest

from core.content_registry import CONTENT_REGISTRY, ContentItem, ContentRegistry, render_content_suggestion
from core.personal_working_model import render_working_model, update_working_model
from core.session_continuity import render_return_continuity, render_session_closure


class PersonalWorkingModelTests(unittest.TestCase):
    def test_requires_evidence_and_uses_progressive_language(self):
        with self.assertRaises(ValueError):
            update_working_model({}, barrier="неясность", skill_title="Один шаг", context="work", successful=True, evidence_ref="")

        model = update_working_model(
            {}, barrier="неясность", skill_title="Один шаг", context="work",
            successful=True, evidence_ref="experiment:1", step_size="открыть файл",
        )
        self.assertEqual(model.confidence, "hypothesis")
        self.assertIn("Сегодня появилась гипотеза", render_working_model(model.as_dict()))
        model = update_working_model(
            model.as_dict(), barrier="неясность", skill_title="Один шаг", context="study",
            successful=True, evidence_ref="experiment:2",
        )
        self.assertEqual(model.confidence, "repeating")
        self.assertIn("Кажется, это повторяется", render_working_model(model.as_dict()))

    def test_success_and_failure_are_counted_separately(self):
        model = update_working_model({}, barrier="тревога", skill_title="Таймер", context="work", successful=False, evidence_ref="e:1")
        self.assertEqual(model.failed_skills["Таймер"], 1)
        self.assertNotIn("Таймер", model.successful_skills)


class ContentAndClosureTests(unittest.TestCase):
    def test_no_content_never_invents_url(self):
        text = render_content_suggestion(ContentRegistry().select(barrier_type="anxiety"))
        self.assertIn("закрепить сегодняшний навык", text)
        self.assertNotIn("http", text)

    def test_only_reviewed_content_is_rankable(self):
        reviewed = ContentItem("r", "start", "anxiety", (), "ru", "article", "Проверено", "https://example.test/r", "review", "4 мин", True)
        draft = ContentItem("d", "start", "anxiety", (), "ru", "article", "Черновик", "https://example.test/d", "draft", "4 мин", False)
        selected = ContentRegistry([draft, reviewed]).select(barrier_type="anxiety")
        self.assertEqual(selected.content_id, "r")

    def test_default_registry_has_reviewed_inline_material_without_external_link(self):
        selected = CONTENT_REGISTRY.select(barrier_type="too_hard")
        text = render_content_suggestion(selected, reason="сегодня порог входа был слишком высоким")
        self.assertIn("первый наблюдаемый контакт", text)
        self.assertNotIn("http", text)

    def test_real_material_matches_fear_of_error_and_distraction(self):
        perfectionism = CONTENT_REGISTRY.select(barrier_type="страшно ошибиться и стыдно")
        distraction = CONTENT_REGISTRY.select(barrier_type="ушёл в телефон и YouTube")
        self.assertEqual(perfectionism.content_id, "cci_perfectionism")
        self.assertEqual(distraction.content_id, "cci_procrastination")
        self.assertIn("cci.health.wa.gov.au", render_content_suggestion(perfectionism))

    def test_closure_and_return_keep_interaction_open(self):
        closure = render_session_closure("помогло открыть файл")
        self.assertIn("если хочется продолжить", closure)
        self.assertNotIn("до завтра", closure.lower())
        self.assertIn("Проверим тот же принцип", render_return_continuity("помогло открыть файл."))


if __name__ == "__main__":
    unittest.main()
