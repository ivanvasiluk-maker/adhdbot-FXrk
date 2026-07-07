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
    def __init__(self, user_id: int): self.id = user_id


class FakeMessage:
    def __init__(self, user_id: int, text: str = ""):
        self.from_user = FakeFromUser(user_id)
        self.text = text
        self.voice = None
        self.bot = None
        self.answers: list[str] = []
    async def answer(self, text: str, **kwargs):
        self.answers.append(str(text)); return None


class FakeTelegramBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
    async def send_message(self, chat_id: int, text: str, **kwargs):
        self.sent.append((chat_id, str(text))); return None


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


def test_can_show_offer_strict_day_gate():
    u = default_user(1)
    profile = {"completed_days": [1, 2], "completed_skill_days": [1, 2]}
    u["day"] = 1
    assert bot.can_show_offer(u, profile) is False
    u["day"] = 2
    assert bot.can_show_offer(u, profile) is False
    u["day"] = 3
    assert bot.can_show_offer(u, profile) is False
    profile = {"completed_days": [1, 2, 3], "completed_skill_days": [1, 2, 3]}
    assert bot.can_show_offer(u, profile) is True
    profile["offer_shown"] = 1
    assert bot.can_show_offer(u, profile) is False


def test_set_day_3_alone_does_not_show_offer():
    u = default_user(1)
    u["day"] = 3
    assert bot.can_show_offer(u, {}) is False


def test_curator_contact_points_to_ivan():
    u = default_user(1)
    text = bot.curator_path_text(u, {})
    assert bot.CURATOR_TELEGRAM_ID == 312112015
    assert "https://t.me/Ivan_Vasiliuk" in text


def test_curator_path_removes_extra_reply_buttons():
    assert bot.curator_path_reply_markup().__class__.__name__ == "ReplyKeyboardRemove"


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


async def test_ready_button_starts_input_mode():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH; bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH); await migrate_db(bot.DB_PATH)
        uid = 92007; u = default_user(uid); u["stage"] = "trainer_intro"; await save_user(u, bot.DB_PATH)
        m = FakeMessage(uid, "✅ Готов")
        await bot.main_flow(m)
        fresh = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert fresh["stage"] == "await_input_mode"


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


async def test_curator_notification_sends_dm_to_ivan():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH; bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH); await migrate_db(bot.DB_PATH)
        uid = 92008; u = default_user(uid); await save_user(u, bot.DB_PATH)
        fake_message = FakeMessage(uid)
        fake_bot = FakeTelegramBot()
        fake_message.bot = fake_bot
        assert await bot.notify_curator_map_review(fake_message, u, {}, "test", "сегодня") is True
        bot.DB_PATH = old
        assert fake_bot.sent
        assert fake_bot.sent[0][0] == 312112015
        assert "Заявка на живой разбор карты" in fake_bot.sent[0][1]
        assert "Когда удобно пользователю: сегодня" in fake_bot.sent[0][1]


async def test_show_offer_force_enables_offer_prerequisites():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH; bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH); await migrate_db(bot.DB_PATH)
        uid = 92009
        u = default_user(uid)
        u["day"] = 1
        u["is_test_user"] = 1
        u["fast_forward_enabled"] = 1
        await save_user(u, bot.DB_PATH)
        msg = FakeMessage(uid, "/show_offer")
        assert await bot.handle_user_command(msg, u, msg.text) is True
        fresh = await get_user(uid, bot.DB_PATH)
        profile = await bot.get_user_profile(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert fresh["stage"] == "offer"
        assert fresh["day"] == 3
        assert profile.get("completed_days") == [1, 2, 3]
        assert profile.get("completed_skill_days") == [1, 2, 3]
        assert profile.get("offer_shown") == 1
        assert profile.get("offer_seen_at")
        assert msg.answers


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
        test_can_show_offer_strict_day_gate,
        test_set_day_3_alone_does_not_show_offer,
        test_curator_contact_points_to_ivan,
        test_curator_path_removes_extra_reply_buttons,
        test_skill_confidence_levels,
        test_last_user_mechanism_overrides_old_hypothesis,
        test_anxiety_does_not_select_phone_distraction_skill,
    ]: fn()
    for fn in [
        test_old_callback_after_skill_change_does_not_modify_state,
        test_old_callback_after_day_close_does_not_modify_state,
        test_ready_button_starts_input_mode,
        test_crisis_phrase_stops_regular_skill_flow,
        test_crisis_redirect_does_not_offer_productivity_skill,
        test_social_support_option_only_when_available,
        test_curator_notification_sends_dm_to_ivan,
        test_show_offer_force_enables_offer_prerequisites,
        test_day_intro_is_not_sent_twice,
    ]: asyncio.run(fn())
    print("[TEST] required regressions OK")


if __name__ == "__main__":
    run()
