import asyncio
import json
from unittest.mock import AsyncMock, patch

import bot


def _texts(markup):
    return [button.text for row in markup.inline_keyboard for button in row]


def test_all_map_aliases_are_known_global_routes():
    for label in bot.MAP_BUTTON_ALIASES:
        assert label in bot.known_reply_button_texts()
        assert bot.global_button_kind(label, label.lower()) == "map"


def test_legacy_navigation_aliases_never_become_unknown():
    aliases = bot.MAP_BUTTON_ALIASES | bot.ACTION_BUTTON_ALIASES | bot.CLOSE_DAY_BUTTON_ALIASES
    assert all(bot.global_button_kind(label, label.lower()) for label in aliases)


def test_current_experiment_snapshot_does_not_inherit_previous_minimum():
    user = {
        "active_experiment_id": 2,
        "current_skill": "return_after_slip",
        "current_next_physical_step": "Одна вкладка на 10 секунд",
        "active_attempt": {
            "attempt_id": "attempt-b", "behavioral_experiment_id": 2,
            "behavioral_experiment_revision": 1, "skill_id": "return_after_slip",
            "instruction": "Сказать «возврат» и открыть задачу",
            "minimum_action": "Сказать «возврат» и открыть задачу",
        },
    }
    snapshot = bot.current_experiment_snapshot(user)
    assert snapshot["skill_id"] == "return_after_slip"
    assert "возврат" in snapshot["minimum"].lower()
    assert "Одна вкладка" not in json.dumps(snapshot, ensure_ascii=False)


def test_public_skill_ids_have_human_labels():
    for internal in ("one_visible_step", "open_only", "phone_far_3min", "return_after_slip"):
        rendered = bot.public_enum_text(internal)
        assert internal not in rendered
        assert "_" not in rendered


def test_low_information_correction_is_not_saved():
    user = {"analysis_json": json.dumps({"hypothesis": "перегруз", "specific_pattern": "много задач"})}
    message = AsyncMock()
    with patch.object(bot, "save_user", AsyncMock()):
        asyncio.run(bot.apply_conclusion_correction(message, user, "все говно"))
    model = json.loads(user["analysis_json"])
    assert model["hypothesis"] == "перегруз"
    assert model["specific_pattern"] == "много задач"
    assert "Не буду записывать" in message.answer.await_args.args[0]


def test_actionable_correction_is_annotation_not_hypothesis():
    user = {"analysis_json": json.dumps({"hypothesis": "перегруз", "specific_pattern": "много задач"})}
    message = AsyncMock()
    with patch.object(bot, "save_user", AsyncMock()):
        asyncio.run(bot.apply_conclusion_correction(
            message, user, "Главный стопор скорее страх ошибки, а не перегруз"
        ))
    model = json.loads(user["analysis_json"])
    assert model["hypothesis"] == "перегруз"
    assert model["specific_pattern"] == "много задач"
    assert model["user_correction"].startswith("Главный стопор")
    assert model["hypothesis_status"] == "needs_recheck"


def test_test_payment_hidden_from_production_user_and_allowed_for_test_user():
    with patch.object(bot, "FREE_BETA_ACCESS", False), patch.object(bot, "PAYMENT_ACCEPT_ANY", True), patch.object(bot, "TEST_MODE", False), patch.object(bot, "is_admin", return_value=False):
        assert not any("Я оплатил(а) — тест" in text for text in _texts(bot.offer_inline_keyboard(100)))
        assert any("Я оплатил(а) — тест" in text for text in _texts(bot.offer_inline_keyboard(100, True)))
        assert not bot.test_payment_allowed(100)
        assert bot.test_payment_allowed(100, True)


def test_offer_contains_every_release_path():
    callbacks = {button.callback_data for row in bot.offer_inline_keyboard(100).inline_keyboard for button in row}
    required = {"bot", "live", "group", "stay_free", "conclusion_full", "next_plan"}
    enabled = {key for key in required if key not in {"bot", "live", "group"} or (
        key == "bot" and bot.paid_plan_available()
        or key == "live" and bot.ENABLE_HUMAN_OFFER
        or key == "group" and bot.ENABLE_GROUP_OFFER
    )}
    assert {bot.OFFER_CALLBACKS[key] for key in enabled} <= callbacks
