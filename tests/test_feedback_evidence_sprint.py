import tempfile
import unittest
from unittest.mock import patch
import bot
from db import init_db, migrate_db, save_user, get_user, get_user_profile
from test_post_action_feedback import FakeMessage
from core.post_action_feedback import ReflectionContext, build_post_action_reflection
from core.learning_engine import ExperimentEvidence

class EvidenceTests(unittest.TestCase):
    def test_worse_is_negative_even_when_executed(self):
        evidence = ExperimentEvidence('open_only', True, 'worse', 'stopped_after_step', 'START')
        self.assertEqual(evidence.normalized_state_effect, 'negative')
        self.assertNotIn(evidence.result, {'STRONG_SUCCESS', 'WEAK_SUCCESS'})
        reflection = build_post_action_reflection(ReflectionContext('отчёт', '', 'Открыть файл', 'открыть файл', True, False, 'worse', False))
        self.assertIn('стало хуже', reflection.interpretation)
        self.assertIn('не повторяем', reflection.memory_anchor)

    def test_zero_reductions_do_not_imply_benefit(self):
        with patch.object(bot, '_metrics_from_profile', return_value={'today':{}}), patch.object(bot, 'action_metrics_text', return_value='Пока нет отметок'):
            text = bot.day_finish_summary_text({}, {})
        self.assertNotIn('уменьшение шага может помогать', text)
        self.assertIn('Данных пока немного', text)

class FeedbackJourney(unittest.IsolatedAsyncioTestCase):
    async def test_done_worse_stopped_survives_reload_and_blocks_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            path = folder + '/test.db'
            await init_db(path)
            await migrate_db(path)
            u = bot.default_user(771)
            u.update(stage='training', daily_skill_id='open_only', current_task_title='написать клиенту', current_day_id='771:1')
            bot.mark_action_card_active(u)
            await save_user(u, path)
            with patch.object(bot, 'DB_PATH', path):
                m = FakeMessage(771, '✅ Сделал')
                await bot.main_flow(m)
                self.assertFalse(any('Получилось сделать?' in text for text in m.answers))
                u = await get_user(771, path)
                self.assertEqual(u['stage'], 'minimal_feedback_help')
                for text in ['Стало хуже', 'Остановился после шага']:
                    reply = FakeMessage(771, text)
                    await bot.main_flow(reply)
                    u = await get_user(771, path)
                self.assertIn('стало хуже', '\n'.join(reply.answers))
                profile = await get_user_profile(771, path)
            attempt = bot.user_skill_attempts(u)[-1]
            self.assertTrue(attempt['completed'])
            self.assertEqual(attempt['subjective_effect'], 'worse')
            self.assertFalse(attempt['continued_target_task'])
            self.assertIsNone(attempt['returned_after_distraction'])
            self.assertNotIn(attempt['experiment_result'], {'STRONG_SUCCESS', 'WEAK_SUCCESS'})
            self.assertEqual(profile['last_unhelpful_skill'], attempt['skill_id'])
            self.assertTrue(bot.should_block_skill_for_repetition(u, attempt['skill_id']))
            self.assertIn('не повторяем', profile['last_memory_anchor'])
