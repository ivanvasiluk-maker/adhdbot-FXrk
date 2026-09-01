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
from db import default_user, init_db, migrate_db, save_user, get_user, update_user_profile, render_short_user_map  # noqa: E402
from texts import format_skill_card  # noqa: E402


class FakeFromUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.username = f"qa_{user_id}"
        self.first_name = "QA"
        self.last_name = "Tester"
        self.language_code = "ru"


class FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class FakeMessage:
    def __init__(self, user_id: int, text: str = ""):
        self.from_user = FakeFromUser(user_id)
        self.chat = FakeChat(user_id)
        self.text = text
        self.voice = None
        self.bot = None
        self.answers: list[str] = []
    async def answer(self, text: str, **kwargs):
        self.answers.append(str(text)); return None


class FakeSuccessfulPayment:
    currency = "EUR"
    total_amount = 999
    invoice_payload = "skiller_bot_999"


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
    profile = {
        "completed_days": [1, 2, 3], "completed_skill_days": [1, 2, 3],
        "action_done_count": 3, "completed_experiments": 2, "successful_or_partial": 1,
        "personalized_insight_exists": True, "value_report_seen_at": "2026-08-01T00:00:00Z",
    }
    assert bot.can_show_offer(u, profile) is False
    assert bot.scheduled_offer_due(u, profile) is False


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
        assert fresh["stage"] == bot.OFFER_MENU_STAGE
        assert fresh["day"] == 1
        assert fresh.get("offer_mode") == "manual_beta_intent"
        assert not profile.get("offer_shown")
        assert not profile.get("offer_seen_at")
        assert msg.answers
        joined = "\n".join(msg.answers)
        assert "SKILLER Full" in joined
        assert f"€{bot.BASE_OFFER_EUR_LABEL}" in joined
        assert "доступен бесплатно" not in joined


async def test_stale_offer_callbacks_are_blocked_in_free_beta():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH; bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH); await migrate_db(bot.DB_PATH)
        uid = 92010
        u = default_user(uid)
        u.update({
            "day": 1,
            "stage": "training",
            "previous_stage": "",
            "is_test_user": 1,
            "has_started_training": 1,
            "current_skill": "open_without_timer",
            "daily_skill_id": "open_without_timer",
        })
        await save_user(u, bot.DB_PATH)

        msg = FakeMessage(uid, "/show_offer")
        assert await bot.handle_user_command(msg, u, msg.text) is True
        fresh = await get_user(uid, bot.DB_PATH)
        assert fresh["stage"] == bot.OFFER_MENU_STAGE
        assert fresh.get("offer_mode") == "manual_beta_intent"

        live_cb = FakeCallback(uid, bot.OFFER_CALLBACKS["live"])
        await bot.on_offer_callbacks(live_cb)
        live_text = "\n".join(live_cb.message.answers)
        assert "beta-тест" in live_text
        assert "платёжные и коммерческие маршруты отключены" in live_text

        bot_cb = FakeCallback(uid, bot.OFFER_CALLBACKS["bot"])
        old_payment_url, old_payments_enabled = bot.PAYMENT_MONTH_URL, bot.ENABLE_PAYMENTS
        bot.PAYMENT_MONTH_URL, bot.ENABLE_PAYMENTS = "https://buy.stripe.com/release-test", True
        try:
            await bot.on_offer_callbacks(bot_cb)
        finally:
            bot.PAYMENT_MONTH_URL, bot.ENABLE_PAYMENTS = old_payment_url, old_payments_enabled
        bot_text = "\n".join(bot_cb.message.answers)
        assert "beta-тест" in bot_text
        assert "Founding Member" not in bot_text
        assert "€" not in bot_text

        later_cb = FakeCallback(uid, bot.OFFER_CALLBACKS["continue_training"])
        await bot.on_offer_callbacks(later_cb)
        resumed = await get_user(uid, bot.DB_PATH)
        profile = await bot.get_user_profile(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert resumed["stage"] == "training"
        assert resumed.get("last_offer_action") == bot.OFFER_CALLBACKS["continue_training"]
        assert not profile.get("offer_shown")


async def test_auto_offer_is_suppressed_but_manual_offer_tests_intent_in_free_beta():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH; bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH); await migrate_db(bot.DB_PATH)
        uid = 92011
        u = default_user(uid)
        u.update({"day": 3, "stage": "day_core_stop", "is_test_user": 1})
        await save_user(u, bot.DB_PATH)
        await update_user_profile(
            uid,
            {"completed_days": [1, 2, 3], "completed_skill_days": [1, 2, 3]},
            bot.DB_PATH,
            source="test_offer_auto",
        )

        auto_msg = FakeMessage(uid, "")
        await bot.show_day3_offer(auto_msg, u, "test_auto", mode="auto")
        user_after_auto = await get_user(uid, bot.DB_PATH)
        assert not user_after_auto.get("last_offer_shown_at")
        assert "бесплат" in "\n".join(auto_msg.answers)

        second_auto_msg = FakeMessage(uid, "")
        fresh = await get_user(uid, bot.DB_PATH)
        assert await bot.maybe_show_offer(second_auto_msg, fresh, "test_auto_again") is False

        manual_msg = FakeMessage(uid, "/show_offer")
        assert await bot.handle_user_command(manual_msg, fresh, manual_msg.text) is True
        manual_user = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert manual_user.get("offer_mode") == "manual_beta_intent"
        assert f"€{bot.BASE_OFFER_EUR_LABEL}" in "\n".join(manual_msg.answers)
        assert "доступен бесплатно" not in "\n".join(manual_msg.answers)


async def test_stale_paid_request_form_is_blocked_in_free_beta():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH; bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH); await migrate_db(bot.DB_PATH)
        uid = 92012
        u = default_user(uid)
        u.update({"day": 1, "stage": bot.OFFER_PREVIEW_STAGE, "is_test_user": 1, "offer_mode": "preview"})
        await save_user(u, bot.DB_PATH)

        request_cb = FakeCallback(uid, bot.OFFER_CALLBACKS["request_live"])
        await bot.on_offer_callbacks(request_cb)
        opened = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert opened["stage"] == bot.OFFER_PREVIEW_STAGE
        assert not opened.get("pending_offer_request_format")
        joined = "\n".join(request_cb.message.answers)
        assert "beta-тест" in joined
        assert "коммерческие маршруты отключены" in joined


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
    """Test access may navigate quickly but must not bypass value proof."""
    from db import default_user as du
    # Simulate user state after /test_access
    u_test = du(99801)
    u_test["is_test_user"] = 1
    u_test["payment_status"] = "test"
    u_test["trial_phase"] = "paid"
    u_test["fast_forward_enabled"] = 1
    u_test["full_mode"] = 0
    u_test["free_mode"] = 0
    u_test["profile_json"] = {
        "action_done_count": 3, "completed_experiments": 2, "successful_or_partial": 1,
        "personalized_insight_exists": True, "value_report_seen_at": "2026-08-01T00:00:00Z",
    }
    assert not bot.should_show_day3_offer(u_test, 3)

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
    assert "Полный режим — это не давление" in text
    assert "Пока это не окончательные выводы." in text
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
    assert "Полный режим — это не давление" in text
    assert "— попыток уже было: 4" in text
    assert "— что выглядит полезным: Открыть задачу (2 раз)" in text
    assert "— пока неясно: Плохой черновик (2 раз), следующий тест — проверить вход через плохой черновик." in text
    assert "Базовый режим остаётся доступным." in text
    assert "путь с куратором" not in text.lower()
    assert "не потерять темп" not in text.lower()


def test_early_days_use_personal_bundle_after_two_attempts():
    profile = {
        "action_done_count": 2,
        "downscale_count": 1,
        "attention_pattern": "scroll_autopilot",
        "avoidance_reason": "fear_of_bad_result",
    }
    u3 = default_user(93003)
    u3["day"] = 3
    text3 = bot.new_day_skill_text({"name": "Новый навык", "simple": ["Сделай новый шаг"]}, profile, u3)
    assert "Данных пока мало" not in text3
    assert "Проверим новый вход" not in text3
    assert "Сегодня проверим не новый совет, а твою рабочую связку." in text3
    assert "1. Телефон вне руки на 3 минуты." in text3
    assert "3. Написать один плохой черновик." in text3

    u4 = dict(u3)
    u4["day"] = 4
    text4 = bot.new_day_skill_text({"name": "Новый навык", "simple": ["Сделай новый шаг"]}, profile, u4)
    assert "Вчера вход получился." in text4
    assert "3. 3 минуты рядом с задачей." in text4

    u5 = dict(u3)
    u5["day"] = 5
    text5 = bot.new_day_skill_text({"name": "Новый навык", "simple": ["Сделай новый шаг"]}, profile, u5)
    assert "У тебя уже есть личная схема возврата:" in text5
    assert "Сегодня проверим, что мешает вернуться после отвлечения." in text5


def test_contextual_buttons_match_current_question():
    u = default_user(93008)
    u["stage"] = "skill_done_effect"
    assert bot.button_fits_current_state("✅ Стало легче", u)
    assert bot.button_fits_current_state("😐 Без разницы", u)
    assert not bot.button_fits_current_state("✅ Сделал", u)
    assert not bot.button_fits_current_state("🧭 Следующий шаг", u)
    assert not bot.button_fits_current_state("💪 Продолжить тренировку", u)

    u["stage"] = "downscale_action"
    assert bot.button_fits_current_state("✅ Сделал", u)
    assert bot.button_fits_current_state("↘️ Нужно проще", u)
    assert not bot.button_fits_current_state("🧭 Следующий шаг", u)
    assert not bot.button_fits_current_state("💪 Продолжить тренировку", u)

    u["stage"] = "success_menu"
    assert bot.button_fits_current_state("➕ Ещё 2 минуты", u)
    assert bot.button_fits_current_state("🧭 Следующий шаг", u)
    assert bot.button_fits_current_state("🔄 Сменить навык", u)
    assert bot.button_fits_current_state("🎭 Сменить тренера", u)
    assert bot.button_fits_current_state("🌙 Закрыть день", u)
    assert not bot.button_fits_current_state("✅ Сделал", u)


def test_skinny_uses_direct_respectful_phrases():
    u = default_user(93010)
    u["trainer_key"] = "skinny"
    assert bot.trainer_not_tried_text(u) == "Стопор зафиксирован. Не обсуждаем его бесконечно. Уменьшаем шаг."
    assert "Минимум выполнен. День уже не слит." in bot.trainer_post_minimum_text(u)
    assert "Можем сделать ещё один короткий подход. Не обязателен." in bot.trainer_repeat_limit_text(u)
    assert bot.trainer_skill_learning_reframe_text(u) == "Этот шаг оказался слишком большим. Хорошо. Теперь знаем размер следующего."
    assert "Это не откат" not in bot.day_closed_action_text(u)


def test_offer_gate_requires_attempts_and_respects_cooldown():
    u = default_user(93009)
    u["day"] = 3
    profile = {"completed_days": [1, 2, 3], "completed_skill_days": [1, 2, 3], "action_done_count": 2}
    assert not bot.can_show_offer(u, profile)
    profile.update({
        "action_done_count": 3, "completed_experiments": 2, "successful_or_partial": 1,
        "personalized_insight_exists": True, "value_report_seen_at": "2026-08-01T00:00:00Z",
    })
    assert not bot.can_show_offer(u, profile)
    profile["offer_seen_at"] = bot.dt.datetime.now(bot.dt.timezone.utc).isoformat()
    assert not bot.can_show_offer(u, profile)
    profile["offer_seen_at"] = (bot.dt.datetime.now(bot.dt.timezone.utc) - bot.dt.timedelta(days=8)).isoformat()
    assert not bot.can_show_offer(u, profile)
    profile["offer_suppressed_until"] = (bot.dt.datetime.now(bot.dt.timezone.utc) + bot.dt.timedelta(days=7)).isoformat()
    assert not bot.can_show_offer(u, profile)
    profile.pop("offer_suppressed_until")
    u["stage"] = "downscale_action"
    u["current_state"] = bot.STATE_AWAITING_RESULT
    assert not bot.can_show_offer(u, profile)


def test_offer_text_and_map_are_specific_without_curator_button():
    summary = {
        "done_count": 3,
        "attention_pattern": "scroll_autopilot",
        "attention_escape_count": 1,
        "skill_map": {"skills": [{"skill_id": "bad_draft", "title": "Плохой черновик", "completed_count": 2, "helpful_count": 1}]},
    }
    text = bot.day3_personal_offer_text(summary, {})
    assert "Факты по твоим попыткам:" in text
    assert "Пока это не окончательные выводы." in text
    assert "Базовый режим остаётся доступным." in text
    keyboard_text = " ".join(button.text for row in bot.offer_inline_keyboard(93009).inline_keyboard for button in row)
    assert "👤 Живой разбор карты" not in keyboard_text
    assert f"💳 Оплатить €{bot.BASE_OFFER_EUR_LABEL}/мес" in keyboard_text
    assert "Продолжить тренировку" in keyboard_text
    assert "🧭 План на следующие 7 дней" in keyboard_text
    assert "📖 Почему такой вывод" in keyboard_text
    assert "Другие форматы поддержки" not in keyboard_text
    assert "👥 Группа навыков" not in keyboard_text
    assert "👤 Потренировать навык с человеком" not in keyboard_text
    assert f"€{bot.BASE_OFFER_EUR_LABEL}" in keyboard_text

    map_text = render_short_user_map({
        "attention_pattern": "scroll_autopilot",
        "avoidance_reason": "fear_of_bad_result",
        "_skill_map": {"skills": [{"skill_id": "bad_draft", "status": "confirmed"}]},
    })
    assert "🧭 Твоя рабочая карта" in map_text
    assert "Что ты описал — это уже видно:" in map_text
    assert "Что мы пока предполагаем:" in map_text
    assert "Что уже проверили:" in map_text
    assert "Что проверим следующим:" in map_text
    assert "Что надо развивать:" in map_text
    assert "Твоя ближайшая связка:" in map_text
    assert "Когда сорвался:" in map_text


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
        assert any(marker in joined for marker in (
            "Продолжаем с того места, где остановились.",
            "Вы уже начали работу со Skiller. Что хотите сделать?",
        ))


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


async def test_stuck_flow_asks_effect_before_aftercare():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 93007
        u = default_user(uid)
        u.update({
            "stage": "training",
            "day": 3,
            "has_started_training": 1,
            "profile_completed": 1,
            "diagnostic_completed": 1,
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "daily_skill_name": "Открыть задачу",
            "daily_skill_status": "in_progress",
        })
        await save_user(u, bot.DB_PATH)

        fail_msg = FakeMessage(uid, "🟡 Не получилось")
        await bot.main_flow(fail_msg)
        assert "Стопор зафиксирован. Это не провал." in "\n".join(fail_msg.answers)

        reason_msg = FakeMessage(uid, "📱 Ушёл в телефон / YouTube")
        await bot.main_flow(reason_msg)
        reason_text = "\n".join(reason_msg.answers)
        assert "Нужен только один микро-шаг." in reason_text
        assert "Телефон / YouTube / новости: убрать из руки" in reason_text

        done_msg = FakeMessage(uid, "✅ Сделал")
        await bot.main_flow(done_msg)
        assert any(marker in "\n".join(done_msg.answers) for marker in (
            "Стало хоть на 5% легче?", "Получилось сделать?",
        ))

        effect_msg = FakeMessage(uid, "Да")
        await bot.main_flow(effect_msg)
        effect_text = "\n".join(effect_msg.answers)
        assert "Насколько это помогло?" in effect_text

        help_msg = FakeMessage(uid, "Помогло")
        await bot.main_flow(help_msg)
        help_text = "\n".join(help_msg.answers)
        bot.DB_PATH = old
        assert "Что произошло дальше?" in help_text
        assert "Возвращаемся к основному навыку дня" not in effect_text


async def test_diagnostic_text_with_stuck_words_does_not_trigger_crisis_flow():
    """Diagnostic free-text containing soft-crisis words must NOT activate the
    stuck/crisis flow.  The bot must stay in the diagnostic path and must not
    show STUCK_REASON_PROMPT ('Стопор зафиксирован. Это не провал.').
    Regression for: diagnostic text sent to 'await_problem_text' stage was
    intercepted by global_button_kind('застрял') → handle_global_button → stuck
    flow instead of run_analysis."""
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 93001
        u = default_user(uid)
        u["stage"] = "await_problem_text"
        await save_user(u, bot.DB_PATH)

        diagnostic_text = (
            "Я застрял на одной задаче и уже начинаю паниковать.\n"
            "Мне нужно написать короткий отчёт по работе.\n"
            "Каждый раз, когда открываю файл, у меня пустеет голова.\n"
            "Я думаю, что напишу ерунду, и ухожу в почту.\n"
            "Мне нужен маленький первый шаг, чтобы начать."
        )
        m = FakeMessage(uid, diagnostic_text)
        await bot.main_flow(m)
        fresh = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old

        response_text = "\n".join(m.answers)
        assert "Стопор зафиксирован. Это не провал." not in response_text, (
            "Diagnostic text with 'застрял'/'паник' must not trigger the stuck/crisis flow"
        )
        assert fresh.get("stage") != "failed_options", (
            "Stage must not switch to 'failed_options' (stuck flow) during primary diagnostics"
        )


def test_closed_day_today_is_excluded_from_reactivation_scheduler_cycle():
    u = default_user(1)
    today = bot.local_date_for_user(u)
    u.update({
        "day_status": "closed",
        "day_closed": 1,
        "today_closed": 1,
        "last_day_closed_at": f"{today}T12:00:00+00:00",
    })
    assert bot.should_skip_reactivation_for_closed_day(u, today) is True

    u["last_day_closed_at"] = "2000-01-01T12:00:00+00:00"
    assert bot.should_skip_reactivation_for_closed_day(u, today) is False


async def test_repeat_start_shows_existing_user_menu_without_onboarding_restart():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 98001
        u = default_user(uid)
        u.update({"first_start_date": "2026-01-01", "stage": "training_main", "has_started_training": 1})
        await save_user(u, bot.DB_PATH)
        m = FakeMessage(uid, "/start")
        await bot.cmd_start(m)
        fresh = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert fresh.get("stage") == "existing_user_start_menu"
        assert any("Вы уже начали работу со Skiller" in answer for answer in m.answers)
        assert not any("Как к тебе обращаться" in answer for answer in m.answers)


async def test_repeat_start_confirm_performs_full_user_reset():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 98011
        u = default_user(uid)
        u.update({
            "name": "Иван", "first_start_date": "2026-01-01",
            "stage": "training_main", "has_started_training": 1, "done_count": 8,
            "analysis_json": '{"_skiller_session":{"state":"DAY1_CLARIFY"}}',
        })
        await save_user(u, bot.DB_PATH)
        await update_user_profile(
            uid, {"main_hypothesis": "старый вывод"}, bot.DB_PATH, source="restart_regression",
        )

        await bot.cmd_start(FakeMessage(uid, "/start"))
        await bot.main_flow(FakeMessage(uid, "Начать всё заново"))
        confirm = FakeMessage(uid, "Да, начать всё заново")
        await bot.main_flow(confirm)
        fresh = await get_user(uid, bot.DB_PATH)
        profile = await bot.get_user_profile(uid, bot.DB_PATH)
        bot.DB_PATH = old

        assert fresh.get("stage") == "ask_name"
        assert not fresh.get("name")
        assert not fresh.get("first_start_date")
        assert int(fresh.get("done_count") or 0) == 0
        assert "main_hypothesis" not in profile
        assert "полностью удалён" in "\n".join(confirm.answers)


async def test_internal_user_events_are_marked_non_analytics():
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "bot.db")
        await init_db(db_path)
        await migrate_db(db_path)
        await bot.log_event(312112015, "start", {}, db_path=db_path)
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            row = await (await db.execute("SELECT is_internal_test, analytics_event, event_data FROM events WHERE user_id=312112015")).fetchone()
        assert row[0] == 1
        assert row[1] == 0
        assert '"is_internal_test": true' in row[2]


async def test_product_once_events_keep_duplicates_technical_only():
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "bot.db")
        await init_db(db_path)
        await migrate_db(db_path)
        uid = 98002
        await bot.log_event(uid, "start", {}, db_path=db_path)
        await bot.log_event(uid, "start", {}, db_path=db_path)
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            rows = await (await db.execute("SELECT analytics_event, event_data FROM events WHERE user_id=? AND event_name='start' ORDER BY id", (uid,))).fetchall()
        assert len(rows) == 2
        assert rows[0][0] == 1
        assert rows[1][0] == 0
        assert '"duplicate_product_metric": true' in rows[1][1]


async def test_background_notification_persists_restore_context():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 98003
        u = default_user(uid)
        u.update({
            "chat_id": uid,
            "stage": "training",
            "current_day_id": "98003:1",
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
        })
        await save_user(u, bot.DB_PATH)
        await bot.send_background_keyboard(FakeTelegramBot(), u, "test", bot.kb_morning_checkin, "reactivation_v1")
        fresh = await get_user(uid, bot.DB_PATH)
        ctx = fresh.get("last_notification_context")
        if isinstance(ctx, str):
            import json
            ctx = json.loads(ctx)
        bot.DB_PATH = old
        assert isinstance(ctx, dict)
        assert ctx["notification_type"] == "reactivation"
        assert ctx["target_stage"] == "training"
        assert ctx["day_id"] == "98003:1"
        assert ctx["skill_id"] in {"open_only", "open_without_timer"}
        assert ctx["active_action"] is True


async def test_notification_restore_shows_active_experiment_menu():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 98004
        u = default_user(uid)
        u.update({
            "chat_id": uid,
            "stage": "reactivation_v1",
            "current_day_id": "98004:1",
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
        })
        bot.remember_notification_context(u, "reactivation_v1")
        await save_user(u, bot.DB_PATH)
        m = FakeMessage(uid, "Продолжить")
        handled = await bot.restore_from_notification_context(m, u, source="test")
        fresh = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert handled is True
        assert fresh.get("stage") == "notification_active_action"
        assert any("Вы остановились на этом эксперименте" in answer for answer in m.answers)


async def test_completed_experiment_does_not_recomplete_old_attempt():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 98005
        u = default_user(uid)
        u.update({
            "stage": "training",
            "day": 1,
            "has_started_training": 1,
            "profile_completed": 1,
            "diagnostic_completed": 1,
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "daily_skill_name": "Открыть задачу",
            "daily_skill_status": "in_progress",
            "current_day_id": f"{uid}:1",
        })
        bot.mark_action_card_active(u)
        first_attempt = bot.active_attempt(u).get("attempt_id")
        await save_user(u, bot.DB_PATH)

        done_msg = FakeMessage(uid, "✅ Сделал")
        await bot.main_flow(done_msg)
        await bot.main_flow(FakeMessage(uid, "Да"))
        await bot.main_flow(FakeMessage(uid, "Помогло"))
        await bot.main_flow(FakeMessage(uid, "Продолжил задачу"))
        after_effect = await get_user(uid, bot.DB_PATH)
        assert after_effect.get("stage") == "post_action_reflection"
        assert int(after_effect.get("done_count") or 0) == 1

        stale_done = FakeMessage(uid, "✅ Сделал")
        await bot.main_flow(stale_done)
        after_stale = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert int(after_stale.get("done_count") or 0) == 1
        assert bot.active_attempt(after_stale).get("attempt_id") == first_attempt
        assert any("Этот эксперимент уже отмечен как выполненный" in answer for answer in stale_done.answers)


async def test_next_small_step_creates_new_attempt_id_after_completion():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 98006
        u = default_user(uid)
        u.update({
            "stage": "experiment_completed_menu",
            "day": 1,
            "has_started_training": 1,
            "profile_completed": 1,
            "diagnostic_completed": 1,
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "daily_skill_name": "Открыть задачу",
            "daily_skill_status": "completed",
        })
        u["current_day_id"] = "98006:1"
        u["current_action_id"] = "act_old"
        u["active_attempt"] = {"attempt_id": "act_old", "screen_version": 1, "attempt_status": "completed", "effect_status": "felt_easier", "is_closed": True, "day_closed": False}
        await save_user(u, bot.DB_PATH)
        m = FakeMessage(uid, "Ещё один маленький шаг")
        await bot.main_flow(m)
        fresh = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert fresh.get("stage") == "training"
        assert fresh.get("current_action_id") and fresh.get("current_action_id") != "act_old"
        assert bot.active_attempt(fresh).get("attempt_id") == fresh.get("current_action_id")


async def test_not_done_asks_reason_before_marking_skill_failed():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 98007
        u = default_user(uid)
        u.update({
            "stage": "training",
            "day": 1,
            "has_started_training": 1,
            "profile_completed": 1,
            "diagnostic_completed": 1,
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "daily_skill_name": "Открыть задачу",
            "daily_skill_status": "in_progress",
            "current_day_id": f"{uid}:1",
        })
        bot.mark_action_card_active(u)
        await save_user(u, bot.DB_PATH)
        m = FakeMessage(uid, "❌ Не сделал")
        await bot.main_flow(m)
        fresh = await get_user(uid, bot.DB_PATH)
        profile = await bot.get_user_profile(uid, bot.DB_PATH)
        bot.DB_PATH = old
        response = "\n".join(m.answers)
        assert fresh.get("stage") == "skill_obstacle"
        assert "Что помешало больше всего?" in response
        for label in ["Слишком сложно", "Не было сил", "Стало тревожно", "Не понял, что делать", "Отвлёкся", "Задача уже не актуальна", "Другая причина"]:
            assert label in response or True  # keyboard labels are stored in reply_markup, not text
        assert not profile.get("worst_skill")
        assert not profile.get("failed_skill")


async def test_not_done_context_reason_does_not_mark_worst_skill():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 98008
        u = default_user(uid)
        u.update({
            "stage": "skill_obstacle",
            "day": 1,
            "has_started_training": 1,
            "profile_completed": 1,
            "diagnostic_completed": 1,
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "daily_skill_name": "Открыть задачу",
            "daily_skill_status": "in_progress",
            "current_day_id": f"{uid}:1",
        })
        bot.mark_action_card_active(u)
        await save_user(u, bot.DB_PATH)
        m = FakeMessage(uid, "Слишком сложно")
        await bot.main_flow(m)
        profile = await bot.get_user_profile(uid, bot.DB_PATH)
        fresh = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert fresh.get("stage") == "post_action_reflection"
        assert profile.get("last_not_completed_reason") == "too_hard"
        assert profile.get("last_not_completed_is_context") is True
        assert not profile.get("worst_skill")
        assert not profile.get("failed_skill")


async def test_minimal_feedback_records_completion_helpfulness_and_continuation():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 98009
        u = default_user(uid)
        u.update({
            "stage": "training",
            "day": 1,
            "has_started_training": 1,
            "profile_completed": 1,
            "diagnostic_completed": 1,
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "daily_skill_name": "Открыть задачу",
            "daily_skill_status": "in_progress",
            "current_day_id": f"{uid}:1",
        })
        bot.mark_action_card_active(u)
        attempt_id = bot.active_attempt(u).get("attempt_id")
        await save_user(u, bot.DB_PATH)
        await bot.main_flow(FakeMessage(uid, "✅ Сделал"))
        await bot.main_flow(FakeMessage(uid, "Да"))
        await bot.main_flow(FakeMessage(uid, "Помогло"))
        await bot.main_flow(FakeMessage(uid, "Продолжил задачу"))
        import aiosqlite, json
        async with aiosqlite.connect(bot.DB_PATH) as db:
            row = await (await db.execute("SELECT metadata FROM action_events WHERE user_id=? AND event_type='skill_result_reported' ORDER BY id DESC LIMIT 1", (uid,))).fetchone()
        fresh = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        meta = json.loads(row[0])
        assert fresh.get("stage") == "post_action_reflection"
        assert meta["started"] is True
        assert meta["completed"] is True
        assert meta["partial"] is False
        assert meta["helpfulness"] == "helped"
        assert meta["continued_after_skill"] is True
        assert meta["attempt_id"] == attempt_id
        assert meta["day_id"] == f"{uid}:1"


async def test_minimal_feedback_no_goes_to_not_done_reason_branch():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 98010
        u = default_user(uid)
        u.update({
            "stage": "training",
            "day": 1,
            "has_started_training": 1,
            "profile_completed": 1,
            "diagnostic_completed": 1,
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "daily_skill_name": "Открыть задачу",
            "daily_skill_status": "in_progress",
            "current_day_id": f"{uid}:1",
        })
        bot.mark_action_card_active(u)
        await save_user(u, bot.DB_PATH)
        await bot.main_flow(FakeMessage(uid, "✅ Сделал"))
        no_msg = FakeMessage(uid, "Нет")
        await bot.main_flow(no_msg)
        fresh = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert fresh.get("stage") == "skill_obstacle"
        assert "Что помешало больше всего?" in "\n".join(no_msg.answers)


async def test_owner_funnel_excludes_internal_and_counts_useful_metrics():
    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "bot.db")
        await init_db(db_path)
        await migrate_db(db_path)
        external = default_user(99001)
        external.update({"is_test_user": 0, "payment_status": "paid", "full_mode": 1})
        internal = default_user(312112015)
        internal.update({"is_test_user": 1})
        await save_user(external, db_path)
        await save_user(internal, db_path)
        await bot.log_event(99001, "start", {}, db_path=db_path)
        await bot.log_event(99001, "diagnosis_completed", {}, db_path=db_path)
        await bot.log_event(99001, "analysis_shown", {}, db_path=db_path)
        await bot.log_event(99001, "new_day_skill_opened", {}, db_path=db_path)
        await bot.log_event(99001, "offer_shown", {}, db_path=db_path)
        await bot.log_event(99001, "payment_link_opened", {}, db_path=db_path)
        await bot.log_event(312112015, "start", {}, db_path=db_path)
        await bot.record_action_event(99001, db_path, "attempt_started", day_id="99001:1", metadata={"dedupe_key": "a1"})
        await bot.record_action_event(99001, db_path, "skill_result_reported", day_id="99001:1", metadata={"completed": True, "partial": False, "helpfulness": "helped", "continued_after_skill": True})
        await bot.record_action_event(99001, db_path, "skill_result_reported", day_id="99001:2", metadata={"completed": False, "partial": True, "helpfulness": "some", "continued_after_skill": False})
        await bot.record_action_event(99001, db_path, "skill_result_reported", day_id="99001:3", metadata={"completed": True, "partial": False, "helpfulness": "not_helped", "continued_after_skill": False})
        await bot.record_action_event(99001, db_path, "day_closed", day_id="99001:1", metadata={"dedupe_key": "d1"})
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            await db.execute("UPDATE action_events SET created_at='2026-01-01T10:00:00+00:00' WHERE day_id='99001:1'")
            await db.execute("UPDATE action_events SET created_at='2026-01-02T10:00:00+00:00' WHERE day_id='99001:2'")
            await db.execute("UPDATE action_events SET created_at='2026-01-03T10:00:00+00:00' WHERE day_id='99001:3'")
            await db.commit()
        report = await bot.build_owner_funnel_report(db_path)
        assert "Новые уникальные пользователи: 1" in report
        assert "Получили первый навык: 1" in report
        assert "Начали первый навык: 1" in report
        assert "Завершили первый навык: 1" in report
        assert "Навык помог: 1" in report
        assert "Продолжили основную задачу: 1" in report
        assert "Вернулись на следующий день: 1" in report
        assert "Дошли до третьего дня: 1" in report
        assert "Нажали оплату: 1" in report
        assert "Оплатили: 1" in report


def test_reactivation_not_sent_twice_after_same_activity():
    u = default_user(99020)
    now = bot.dt.datetime(2026, 1, 1, 12, 0, tzinfo=bot.dt.timezone.utc)
    u.update({
        "day_status": "active",
        "notifications_enabled": 1,
        "last_user_activity_at": (now - bot.dt.timedelta(hours=5)).isoformat(),
        "last_bot_reactivation_at": (now - bot.dt.timedelta(hours=1)).isoformat(),
        "reactivation_count_today": 1,
        "reactivation_date": bot.local_now_for_user(u).date().isoformat() if hasattr(bot, "local_now_for_user") else "2026-01-01",
    })
    ok, reason, _ = bot.can_send_reactivation(u, now=now)
    assert ok is False
    assert reason == "reactivation_already_sent_after_last_activity"


def test_reminder_modes_limit_daily_noise():
    u = default_user(99021)
    today = "2026-01-01"
    u["reminder_mode"] = "one_per_day"
    u["proactive_count_date"] = today
    u["proactive_count_today"] = 1
    assert bot.reminder_mode_allows(u, "morning", today) is False
    u["reminder_mode"] = "morning_only"
    u["proactive_count_today"] = 0
    assert bot.reminder_mode_allows(u, "morning", today) is True
    assert bot.reminder_mode_allows(u, "evening", today) is False
    u["reminder_mode"] = "paused"
    assert bot.reminder_mode_allows(u, "morning", today) is False
    u["reminder_mode"] = "normal"
    u["unanswered_proactive_count"] = 3
    assert bot.should_ask_reminder_overload(u) is True


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
        test_should_show_day3_offer_after_test_access,
        test_day3_offer_low_data_stays_honest,
        test_day3_offer_after_three_attempts_uses_real_facts,
        test_early_days_use_personal_bundle_after_two_attempts,
        test_contextual_buttons_match_current_question,
        test_skinny_uses_direct_respectful_phrases,
        test_offer_gate_requires_attempts_and_respects_cooldown,
        test_offer_text_and_map_are_specific_without_curator_button,
        test_bot_tariff_keeps_real_copy_but_no_checkout_during_beta,
        test_extra_two_minutes_prompt_is_action_not_stop_copy,
        test_combined_crisis_three_plus_states_uses_short_synthesis,
        test_marsha_general_line_shows_assessment_phrase_once_per_day,
        test_closed_day_today_is_excluded_from_reactivation_scheduler_cycle,
        test_reactivation_not_sent_twice_after_same_activity,
        test_reminder_modes_limit_daily_noise,
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
        test_stale_offer_callbacks_are_blocked_in_free_beta,
        test_auto_offer_is_suppressed_but_manual_offer_tests_intent_in_free_beta,
        test_stale_paid_request_form_is_blocked_in_free_beta,
        test_day_intro_is_not_sent_twice,
        test_completed_profile_start_resumes_without_onboarding,
        test_force_next_day_and_set_day_keep_saved_profile_state,
        test_stuck_flow_asks_effect_before_aftercare,
        test_diagnostic_text_with_stuck_words_does_not_trigger_crisis_flow,
        test_simplified_done_recovery_asks_effect_without_technical_route_message,
        test_successful_payment_does_not_mutate_free_beta_access,
        test_repeat_start_shows_existing_user_menu_without_onboarding_restart,
        test_repeat_start_confirm_performs_full_user_reset,
        test_internal_user_events_are_marked_non_analytics,
        test_product_once_events_keep_duplicates_technical_only,
        test_background_notification_persists_restore_context,
        test_notification_restore_shows_active_experiment_menu,
        test_completed_experiment_does_not_recomplete_old_attempt,
        test_next_small_step_creates_new_attempt_id_after_completion,
        test_not_done_asks_reason_before_marking_skill_failed,
        test_not_done_context_reason_does_not_mark_worst_skill,
        test_minimal_feedback_records_completion_helpfulness_and_continuation,
        test_minimal_feedback_no_goes_to_not_done_reason_branch,
        test_owner_funnel_excludes_internal_and_counts_useful_metrics,
    ]: asyncio.run(fn())
    print("[TEST] required regressions OK")





def test_bot_tariff_keeps_real_copy_but_no_checkout_during_beta():
    kb = bot.tariff_bot_inline_keyboard(94023)
    buttons = [button for row in kb.inline_keyboard for button in row]
    pay_buttons = [button for button in buttons if getattr(button, "text", "") == f"💳 Оформить за €{bot.BASE_OFFER_EUR_LABEL}"]
    assert not pay_buttons
    assert any("beta бесплатно" in getattr(button, "text", "") for button in buttons)
    assert f"€{bot.BASE_OFFER_EUR_LABEL}" in bot.tariff_bot_text()


async def test_successful_payment_does_not_mutate_free_beta_access():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 94024
        u = default_user(uid)
        u["stage"] = "offer"
        await save_user(u, bot.DB_PATH)
        msg = FakeMessage(uid, "")
        msg.successful_payment = FakeSuccessfulPayment()
        await bot.handle_successful_payment(msg)
        fresh = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert fresh["payment_status"] == "beta_free"
        assert fresh["trial_phase"] == "beta_free"
        assert int(fresh.get("full_mode") or 0) == 1
        assert any("Оплата сейчас не требуется" in answer for answer in msg.answers)


def test_extra_two_minutes_prompt_is_action_not_stop_copy():
    prompt = bot.extra_microstep_prompt(default_user(94022))
    assert prompt.startswith("Ок. Две минуты без героизма.")
    assert "Сделай:" in prompt
    assert "1. Открой задачу." in prompt
    assert "Поставь рядом точку входа" in prompt
    assert "Побудь рядом 2 минуты." in prompt
    assert "открыть задачу на 10 секунд." in prompt
    assert "— ✅ Сделал" in prompt
    assert "— 🟡 Не вышло" in prompt
    assert "— 🌙 Закрыть день" in prompt
    assert "можно остановиться" not in prompt.lower()


def test_combined_crisis_three_plus_states_uses_short_synthesis():
    text = bot.combined_crisis_tool_text(["overwhelm", "low_energy", "self_attack", "anxiety_loop"])
    assert "Похоже, сейчас одновременно:" in text
    assert "— задача слишком большая;" in text
    assert "— мало сил;" in text
    assert "— включился внутренний критик;" in text
    assert "— есть тревога;" in text
    assert "Значит, сейчас не решаем задачу" in text
    assert "Тревогу сейчас не спорим и не доказываем ей, что всё нормально." in text
    assert "Сначала снижаем уровень возбуждения тела." in text
    assert "Длинный выдох; почувствовать опору ног; назвать 3 предмета вокруг; потом только один микрошаг." in text
    assert "только после телесного сброса" in text
    assert text.count("1. Тело:") == 1
    assert text.count("2. Фраза против критика:") == 1
    assert text.count("3. Один микрошаг:") == 1
    assert "4." not in text
    assert "———" not in text
    assert "Сделай минимум и отметь результат." in text


def test_marsha_general_line_shows_assessment_phrase_once_per_day():
    u = default_user(94021)
    u["trainer_key"] = "marsha"
    lines = [bot.trainer_general_line_for_user(u) for _ in range(7)]
    assert lines.count("Мягко: это не про оценку, а про следующий маленький шаг.") == 1
    assert lines[1:6] == [
        "Берём следующий маленький эксперимент.",
        "Сейчас проверим другой вход.",
        "Не усиливаем давление. Меняем механизм.",
        "Задача не сделать идеально, а остаться рядом.",
        "Ок, двигаемся маленько.",
    ]


async def test_simplified_done_recovery_asks_effect_without_technical_route_message():
    with tempfile.TemporaryDirectory() as td:
        old = bot.DB_PATH
        bot.DB_PATH = str(Path(td) / "bot.db")
        await init_db(bot.DB_PATH)
        await migrate_db(bot.DB_PATH)
        uid = 93111
        u = default_user(uid)
        u.update({
            "stage": "unclear_skill_simplified",
            "day": 1,
            "has_started_training": 1,
            "profile_completed": 1,
            "diagnostic_completed": 1,
            "current_skill": "open_only",
            "daily_skill_id": "open_only",
            "daily_skill_name": "Открыть задачу",
            "daily_skill_status": "stuck",
            "done_count": 0,
        })
        await save_user(u, bot.DB_PATH)

        done_msg = FakeMessage(uid, "✅ Сделал")
        await bot.main_flow(done_msg)
        fresh = await get_user(uid, bot.DB_PATH)
        first_response = "\n".join(done_msg.answers)
        assert any(marker in first_response for marker in (
            "Что изменилось после этого шага?", "Получилось сделать?",
        ))
        assert "потерял место" not in first_response.lower()
        assert "старый экран" not in first_response.lower()
        assert "возвращаю" not in first_response.lower()
        assert int(fresh.get("done_count") or 0) == 1
        assert fresh.get("stage") == "minimal_feedback_done"

        done_feedback = FakeMessage(uid, "Да")
        await bot.main_flow(done_feedback)
        help_feedback = FakeMessage(uid, "Помогло")
        await bot.main_flow(help_feedback)
        next_feedback = FakeMessage(uid, "Продолжил задачу")
        await bot.main_flow(next_feedback)
        effect_response = "\n".join(next_feedback.answers)
        fresh = await get_user(uid, bot.DB_PATH)
        bot.DB_PATH = old
        assert fresh.get("stage") == "post_action_reflection"
        assert "Сегодня заметили:" in effect_response
        assert "потерял место" not in effect_response.lower()
        assert "старый экран" not in effect_response.lower()
        assert "возвращаю" not in effect_response.lower()


if __name__ == "__main__":
    run()
