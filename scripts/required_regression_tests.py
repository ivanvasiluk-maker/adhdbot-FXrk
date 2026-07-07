#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("BOT_TOKEN", "")
os.environ.setdefault("OPENAI_API_KEY", "")

import bot  # noqa: E402
from db import default_user, init_db, migrate_db, save_user, get_user, update_user_profile  # noqa: E402
from texts import format_skill_card  # noqa: E402


class FakeFromUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.username = f"qa_{user_id}"
        self.first_name = "QA"


class FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class FakeMessage:
    def __init__(self, user_id: int, text: str = ""):
        self.from_user = FakeFromUser(user_id)
        self.chat = FakeChat(user_id)
        self.text = text
        self.voice = None
        self.answers: list[str] = []
    async def answer(self, text: str, **kwargs):
        self.answers.append(str(text)); return None


class FakeCallback:
    def __init__(self, user_id: int, data: str):
        self.from_user = FakeFromUser(user_id)
        self.data = data
        self.message = FakeMessage(user_id)
        self.answered = False
    async def answer(self, *args, **kwargs):
        self.answered = True


def test_rendered_skill_card_has_one_title_and_one_minimum():
    u = default_user(1); u["current_core_skill_id"] = "open_without_timer"
    skill = {"skill_id": "open_without_timer", "name": "Открыть без таймера", "steps": ["Открой файл"], "minimum": "10 секунд"}
    text = format_skill_card(u, skill, "задача")
    assert text.count("🧩 Навык:") == 1
    assert text.count("Минимум:") == 1
    assert "📚 Мини-урок" not in text


def test_too_hard_ladder_gets_strictly_smaller():
    u = default_user(1)
    u["current_task_name"] = "дописать презентацию клиенту"
    u["current_task_object"] = "презентация"
    assert bot.truly_smaller_step(u) == "Напиши 3 плохих тезиса для презентации. Не редактируй."
    assert bot.truly_smaller_step(u) == "Напиши одну плохую строку для презентации."
    assert bot.truly_smaller_step(u) == "Открой презентацию и просто поставь курсор."


def test_skill_change_renders_only_new_skill():
    u = default_user(1); u["current_core_skill_id"] = "phone_away_3_min"
    skill = {"skill_id": "body_first", "name": "Сначала тело", "steps": ["Выдох"], "minimum": "один выдох"}
    text = format_skill_card(u, skill, "задача")
    assert "Сначала тело" in text
    assert "Телефон" not in text


def test_skill_cannot_have_conflicting_final_statuses():
    statuses = [bot.skill_status_wording(x) for x in ["promising", "not_helpful", "tested_once", "proposed"]]
    assert len(statuses) == len(set(statuses))
    assert "есть первый сигнал, что помогает" in statuses
    assert "сейчас не подошёл" in statuses


def test_completed_and_neutral_maps_to_needs_another_try():
    assert bot.skill_status_wording("tested_once") == "нужна ещё попытка"


def test_helped_start_maps_to_first_positive_signal():
    assert bot.skill_status_wording("started_task") == "есть первый сигнал, что помогает"


def test_not_my_skill_maps_to_not_suitable_now():
    assert bot.skill_status_wording("not_helpful") == "сейчас не подошёл"


def test_not_fit_today_status_wording():
    assert bot.skill_status_wording("not_fit_today") == "сегодня не повторяем"


def test_not_fit_today_blocks_mechanism_skill_selection():
    u = default_user(1)
    bot.sync_active_attempt(u, current_mechanism="phone")
    today = bot.local_date_for_user(u)
    profile = {"not_fit_today": {today: ["phone_away_3_min"]}}
    skill = bot.select_daily_skill(u, profile)
    assert skill["skill_id"] != "phone_away_3_min"


def test_skill_confidence_levels():
    assert bot.skill_confidence_text(0) == "ещё не проверяли"
    assert bot.skill_confidence_text(1) == "есть первый сигнал, что этот вход может помогать"
    assert bot.skill_confidence_text(2) == "этот вход повторно сработал; пока считаем его рабочим кандидатом"
    assert bot.skill_confidence_text(3) == "похоже, это один из твоих устойчивых рабочих входов"


def test_last_user_mechanism_overrides_old_hypothesis():
    u = default_user(1)
    bot.sync_active_attempt(u, current_mechanism="phone")
    bot.sync_active_attempt(u, current_mechanism="anxiety")
    u["active_attempt"]["last_user_mechanism"] = "anxiety"
    assert u["active_attempt"]["current_mechanism"] == "anxiety"


def test_anxiety_does_not_select_phone_distraction_skill():
    u = default_user(1)
    bot.sync_active_attempt(u, current_mechanism="anxiety")
    skill = bot.select_daily_skill(u, {})
    assert skill["skill_id"] != "phone_away_3_min"


async def test_old_callback_after_skill_change_does_not_modify_state():
    uid = 92001
    u = default_user(uid); u["current_screen_id"] = "old"; u["current_skill"] = "open_without_timer"
    bot.sync_active_attempt(u, bump=True, is_closed=False)
    old_version = u["active_attempt"]["screen_version"] - 1
    c = FakeCallback(uid, f"show_map|sid:old|v:{old_version}")
    valid, _ = await bot.validate_callback_screen(c, u, "test")
    assert not valid
    assert u["current_skill"] == "open_without_timer"


async def test_old_callback_after_day_close_does_not_modify_state():
    uid = 92002
    u = default_user(uid); u["current_screen_id"] = "old"; u["current_skill"] = "open_without_timer"
    bot.sync_active_attempt(u, bump=True, is_closed=True, day_closed=True)
    version = u["active_attempt"]["screen_version"]
    c = FakeCallback(uid, f"show_map|sid:old|v:{version}")
    valid, _ = await bot.validate_callback_screen(c, u, "test")
    assert not valid
    assert u["current_skill"] == "open_without_timer"


async def test_crisis_phrase_stops_regular_skill_flow():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH; bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH); await migrate_db(bot.DB_PATH)
        uid = 92003; u = default_user(uid); await save_user(u, bot.DB_PATH)
        m = FakeMessage(uid, "не хочу жить")
        await bot.main_flow(m)
        fresh = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert int(fresh.get("crisis_redirected") or 0) == 1
        assert "Навык дня" not in "\n".join(m.answers)


async def test_crisis_redirect_does_not_offer_productivity_skill():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH; bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH); await migrate_db(bot.DB_PATH)
        uid = 92004; u = default_user(uid); await save_user(u, bot.DB_PATH); m = FakeMessage(uid)
        await bot.crisis_redirect(m, u)
        bot.DB_PATH = old
        assert "Навык дня" not in "\n".join(m.answers)


async def test_social_support_option_only_when_available():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH; bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH); await migrate_db(bot.DB_PATH)
        uid = 92006; u = default_user(uid); await save_user(u, bot.DB_PATH)
        assert await bot.maybe_social_support_entry_option(u) == []
        await update_user_profile(uid, {"social_support_available": 1, "social_support_can_message": 1}, bot.DB_PATH, source="test")
        assert await bot.maybe_social_support_entry_option(u) == ["👤 Написать человеку-опоре"]
        await bot.mark_social_support_entry_shown(u)
        assert await bot.maybe_social_support_entry_option(u) == []
        bot.DB_PATH = old


async def test_day_intro_is_not_sent_twice():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH; bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH); await migrate_db(bot.DB_PATH)
        uid = 92005; u = default_user(uid); u["stage"] = "training"; u["day_intro_sent"] = 0; await save_user(u, bot.DB_PATH)
        m1 = FakeMessage(uid); await bot.start_new_day(uid, m1, u, "admin_force_next_day")
        fresh = await get_user(uid, bot.DB_PATH)
        m2 = FakeMessage(uid); await bot.start_new_day(uid, m2, fresh, "admin_force_next_day")
        bot.DB_PATH = old
        assert "🌱 Новый день" in "\n".join(m1.answers)
        assert "🌱 Новый день" not in "\n".join(m2.answers)


def test_should_show_day3_offer_after_test_access():
    """After /test_access (is_test_user=1, payment_status=test), the day-3 offer
    must still auto-trigger in test mode.  It should only be suppressed once the
    user completes the actual payment flow (full_mode=1)."""
    from db import default_user as du
    # Simulate user state after /test_access
    u_test = du(99801)
    u_test["is_test_user"] = 1
    u_test["payment_status"] = "test"
    u_test["trial_phase"] = "paid"
    u_test["fast_forward_enabled"] = 1
    u_test["full_mode"] = 0
    u_test["free_mode"] = 0
    assert bot.should_show_day3_offer(u_test, 3), (
        "Offer should auto-trigger for test users after /test_access (full_mode=0)"
    )

    # After /simulate_payment full_mode is set to 1 — offer must be suppressed
    u_paid = dict(u_test)
    u_paid["full_mode"] = 1
    assert not bot.should_show_day3_offer(u_paid, 3), (
        "Offer must NOT auto-trigger once full_mode=1 (payment completed)"
    )

    # Free-mode users must never see the offer
    u_free = dict(u_test)
    u_free["free_mode"] = 1
    assert not bot.should_show_day3_offer(u_free, 3), (
        "Offer must NOT auto-trigger for free-mode users"
    )


def test_day3_offer_low_data_stays_honest():
    summary = {"done_count": 1, "skill_map": {"skills": []}}
    profile = {"successful_skills": ["open_only"]}
    text = bot.day3_personal_offer_text(summary, profile)
    assert "У нас появились первые гипотезы" in text
    assert "первый рабочий вход" not in text
    assert "мы уже увидели твой паттерн" not in text.lower()
    assert "не потерять темп" not in text.lower()


def test_day3_offer_after_three_attempts_uses_real_facts():
    summary = {
        "done_count": 4,
        "main_hypothesis": "проверить вход через плохой черновик",
        "attention_pattern": "scroll_autopilot",
        "attention_escape_count": 2,
        "skill_map": {
            "skills": [
                {"skill_id": "open_only", "title": "Открыть задачу", "attempt_count": 3, "completed_count": 2, "helpful_count": 2, "stuck_count": 0},
                {"skill_id": "bad_draft_entry", "title": "Плохой черновик", "attempt_count": 2, "completed_count": 0, "helpful_count": 0, "stuck_count": 2},
            ]
        },
    }
    profile = {}
    text = bot.day3_personal_offer_text(summary, profile)
    assert "Что уже видно:" in text
    assert "— попыток: 4;" in text
    assert "— дало облегчение: Открыть задачу (2 раз)" in text
    assert "— пока не помогло: Плохой черновик (2 раз)" in text
    assert "— следующий эксперимент: проверить вход через плохой черновик." in text
    assert "путь с куратором" not in text.lower()
    assert "не потерять темп" not in text.lower()


async def test_completed_profile_start_resumes_without_onboarding():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 92007
        u = default_user(uid)
        u.update({
            "name": "Иван",
            "trainer_key": "beck",
            "stage": "start",
            "day": 3,
            "has_started_training": 1,
            "profile_completed": 1,
            "diagnostic_completed": 1,
            "is_test_user": 1,
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "daily_skill_name": "Открыть задачу",
            "daily_skill_status": "in_progress",
        })
        await save_user(u, bot.DB_PATH)
        m = FakeMessage(uid, "/start")
        await bot.cmd_start(m)
        bot.DB_PATH = old
        joined = "\n".join(m.answers)
        assert "Как к тебе обращаться?" not in joined
        assert "Выбери тренера" not in joined
        assert "Готов начать разбор и перейти к первому дню?" not in joined
        assert "Продолжаем с того места, где остановились." in joined


async def test_force_next_day_and_set_day_keep_saved_profile_state():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 92008
        u = default_user(uid)
        u.update({
            "name": "Иван",
            "trainer_key": "beck",
            "notifications_enabled": 1,
            "stage": "training",
            "day": 2,
            "has_started_training": 1,
            "profile_completed": 1,
            "diagnostic_completed": 1,
            "is_test_user": 1,
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "daily_skill_name": "Открыть задачу",
            "daily_skill_status": "in_progress",
            "current_task_title": "дописать письмо",
            "done_count": 2,
        })
        await save_user(u, bot.DB_PATH)
        msg_next = FakeMessage(uid, "/force_next_day")
        assert await bot.handle_admin_command(msg_next, u, "/force_next_day")
        fresh = await get_user(uid, bot.DB_PATH)
        assert fresh["name"] == "Иван"
        assert fresh["trainer_key"] == "beck"
        assert fresh["current_task_title"] == "дописать письмо"
        assert int(fresh.get("attempts_count") or 0) >= 2
        msg_set = FakeMessage(uid, "/set_day 3")
        assert await bot.handle_admin_command(msg_set, fresh, "/set_day 3")
        updated = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert updated["name"] == "Иван"
        assert updated["trainer_key"] == "beck"
        assert updated["current_task_title"] == "дописать письмо"
        assert int(updated.get("day") or 0) == 3
        assert int(updated.get("profile_completed") or 0) == 1


def run():
    for fn in [
        test_rendered_skill_card_has_one_title_and_one_minimum,
        test_too_hard_ladder_gets_strictly_smaller,
        test_skill_change_renders_only_new_skill,
        test_skill_cannot_have_conflicting_final_statuses,
        test_completed_and_neutral_maps_to_needs_another_try,
        test_helped_start_maps_to_first_positive_signal,
        test_not_my_skill_maps_to_not_suitable_now,
        test_not_fit_today_status_wording,
        test_not_fit_today_blocks_mechanism_skill_selection,
        test_skill_confidence_levels,
        test_last_user_mechanism_overrides_old_hypothesis,
        test_anxiety_does_not_select_phone_distraction_skill,
        test_should_show_day3_offer_after_test_access,
        test_day3_offer_low_data_stays_honest,
        test_day3_offer_after_three_attempts_uses_real_facts,
    ]: fn()
    for fn in [
        test_old_callback_after_skill_change_does_not_modify_state,
        test_old_callback_after_day_close_does_not_modify_state,
        test_crisis_phrase_stops_regular_skill_flow,
        test_crisis_redirect_does_not_offer_productivity_skill,
        test_social_support_option_only_when_available,
        test_day_intro_is_not_sent_twice,
        test_completed_profile_start_resumes_without_onboarding,
        test_force_next_day_and_set_day_keep_saved_profile_state,
    ]: asyncio.run(fn())
    print("[TEST] required regressions OK")


if __name__ == "__main__":
    run()
