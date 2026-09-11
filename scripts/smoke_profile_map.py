import asyncio
import os
import sys
import tempfile
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import bot
from db import (
    init_db, migrate_db, get_user, save_user, get_user_profile,
    render_development_mirror_report, render_development_mirror_reports,
    daily_profile_explanation, determine_development_focus,
)


class DummyMessage:
    def __init__(self, uid: int, text: str):
        self.text = text
        self.voice = None
        self.from_user = types.SimpleNamespace(id=uid, username="smoke_user")
        self.answers = []

    async def answer(self, text, reply_markup=None, parse_mode=None):
        self.answers.append({"text": text, "reply_markup": reply_markup, "parse_mode": parse_mode})
        return None


async def run():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "smoke.db")
        bot.DB_PATH = db_path
        bot.SHEETS_WEBHOOK_URL = ""
        await init_db(db_path)
        await migrate_db(db_path)

        uid = 424242
        u = await get_user(uid, db_path)
        u["stage"] = "training"
        u["bucket"] = "mixed"
        u["analysis_json"] = "{}"
        u["plan_json"] = "[\"open_only\", \"task_naming\", \"ninety_sec_start\"]"
        u["day"] = 3
        await save_user(u, db_path)

        # 1) Deterministic diagnosis signal -> perfectionism_start_block.
        # Do not make this offline smoke depend on a configured LLM.
        await bot.record_profile_signal(uid, "analysis", {
            "main_pattern": "perfectionism_start_block",
            "avoidance_reason": "fear_of_bad_result",
            "main_hypothesis": "страх ошибки или оценки",
            "recommended_track": "procrastination",
            "recommended_variant": "bad_draft",
        }, source="smoke_analysis")
        p1 = await get_user_profile(uid, db_path)

        # 2) One failed entry is recorded without fabricating a conversation.
        await bot.record_profile_signal(uid, "training", {
            "last_not_completed_reason": "страх ошибки",
            "failed_skill": "phone_away_3_min",
            "action_failed_count": 1,
        }, source="smoke_failed")
        p2 = await get_user_profile(uid, db_path)

        # 3) One successful entry -> best_skill and value proof.
        await bot.record_profile_signal(uid, "training", {
            "best_skill": "bad_draft",
            "last_successful_skill": "bad_draft",
            "action_done_count": 1,
            "last_skill_effect": "helped",
        }, source="action_done")
        p3 = await get_user_profile(uid, db_path)

        # 4) Day3 offer and payment URL fallback selection
        u = await get_user(uid, db_path)
        bot.PAYMENT_URL_MONTH_1498 = "https://buy.stripe.com/test-skiller-full"
        bot.ENABLE_PAYMENTS = True
        bot.ENABLE_PAID_PLAN = True
        offer_msg = DummyMessage(uid, "")
        await bot.show_day3_offer(offer_msg, u, "smoke_test")
        offer_text = offer_msg.answers[-1]["text"] if offer_msg.answers else ""
        kb = offer_msg.answers[-1].get("reply_markup") if offer_msg.answers else None
        kb_texts = []
        if kb:
            rows = getattr(kb, "keyboard", None) or getattr(kb, "inline_keyboard", None) or []
            for row in rows:
                for btn in row:
                    kb_texts.append(getattr(btn, "text", ""))

        print("[SMOKE] perfectionism main_pattern:", p1.get("main_pattern"))
        print("[SMOKE] failed main_pattern:", p2.get("main_pattern"))
        print("[SMOKE] done best_skill:", p3.get("best_skill"))
        avatar = p3.get("development_avatar") or {}
        metrics = avatar.get("metrics") or {}
        task_start = metrics.get("task_initiation") or {}
        prompt = p3.get("profile_prompt") or ""
        dev_map = p3.get("development_map") or {}
        print("[SMOKE] done action_done_count:", p3.get("action_done_count"))
        print("[SMOKE] avatar task_initiation:", task_start.get("value"))
        print("[SMOKE] profile_prompt_present:", bool(prompt))
        print("[SMOKE] development_map_events:", dev_map.get("behavior_events_count"))
        history = p3.get("development_history") or {}
        daily_explanation = daily_profile_explanation(p3, "open_only")
        focus = determine_development_focus(p3)
        mirror_month = render_development_mirror_report(p3, period_days=30)
        old_snapshot = dict((history.get("snapshots") or [{}])[-1])
        old_snapshot["created_at"] = "2026-01-01T00:00:00+00:00"
        old_snapshot["barriers"] = ["раньше старт задачи почти всегда застревал"]
        old_metrics = dict(old_snapshot.get("avatar_metrics") or {})
        old_metrics["task_initiation"] = 20
        old_snapshot["avatar_metrics"] = old_metrics
        profile_with_old_history = {
            **p3,
            "development_history": {
                **history,
                "snapshots": [old_snapshot, *(history.get("snapshots") or [])],
            },
        }
        mirror_90 = render_development_mirror_report(profile_with_old_history, period_days=90)
        mirror_all = render_development_mirror_reports(profile_with_old_history)
        print("[SMOKE] recommended_track:", p3.get("recommended_track"))
        print("[SMOKE] development_history_snapshots:", len(history.get("snapshots") or []))
        print("[SMOKE] daily_focus:", focus.get("code"))
        has_group_offer = "👥 Хочу в группу — €240" in kb_texts
        has_live_offer = any("👤 Личная терапия" in t for t in kb_texts)
        has_primary_map = "📌 Краткое заключение" in offer_text and "Как держится проблема" in offer_text
        has_day3_conclusion = "Главный узел" in offer_text and "лучший сигнал" in offer_text.lower()
        has_personal_offer = (
            "Продолжить бесплатный тест" in kb_texts
            and has_group_offer and has_live_offer
        )
        has_model_value = "START → STAY → RETURN" in offer_text
        has_selling_specifics = (
            "выбери формат и напиши Ивану" in offer_text
            and "📖 Почему такой вывод" in kb_texts
        )
        assert int(task_start.get("value") or 0) >= 20, avatar
        assert prompt.startswith("USER PROFILE"), prompt
        assert int(dev_map.get("behavior_events_count") or 0) >= 1, dev_map
        assert dev_map.get("helps") or p3.get("completed_skills_effect_unknown") is not None or p3.get("last_skill_effect") in {"unknown", None}, dev_map
        assert len(history.get("snapshots") or []) >= 1, history
        assert "🪞 Зеркало развития — месячный отчёт" in mirror_month, mirror_month
        assert "Кем ты был(а) раньше" in mirror_month, mirror_month
        assert "Какие стратегии сработали" in mirror_month, mirror_month
        assert "Главные направления роста" in mirror_month, mirror_month
        assert "отчёт за 90 дней" in mirror_all and "отчёт за 180 дней" in mirror_all, mirror_all
        assert "Сравнение с состоянием около 2026-01-01" in mirror_90, mirror_90
        assert "раньше старт задачи" in mirror_90, mirror_90
        assert focus.get("code") in {"task_initiation", "attention_holding", "self_regulation", "self_criticism", "slip_recovery", "social_activity", "professional_activity"}, focus
        assert "Сегодняшний фокус" in daily_explanation, daily_explanation
        assert "Мы проверим навык" in daily_explanation, daily_explanation
        assert "эта модель будет уточняться" in daily_explanation.lower(), daily_explanation
        assert "точный показатель" not in daily_explanation.lower(), daily_explanation
        assert "USER PROFILE" not in offer_text, offer_text
        assert has_group_offer, kb_texts
        assert has_live_offer, kb_texts
        assert has_primary_map, offer_text
        assert has_day3_conclusion, offer_text
        assert has_personal_offer, offer_text
        assert has_model_value, offer_text
        assert has_selling_specifics, offer_text
        print("[SMOKE] offer has group button:", has_group_offer)
        print("[SMOKE] offer has personal-work button:", has_live_offer)
        print("[SMOKE] offer contains primary map title:", has_primary_map)
        print("[SMOKE] offer contains day3 conclusion:", has_day3_conclusion)
        print("[SMOKE] offer contains personal profile:", has_personal_offer)
        print("[SMOKE] offer contains model value:", has_model_value)
        print("[SMOKE] offer contains selling specifics:", has_selling_specifics)
        print("[SMOKE] mirror monthly report present:", "месячный отчёт" in mirror_month)
        print("[SMOKE] mirror long reports present:", "отчёт за 180 дней" in mirror_all)
        print("[SMOKE] daily explanation personalized:", "Сегодняшний фокус" in daily_explanation)
        print("[SMOKE] payment url month set:", bool(bot.PAYMENT_URL_MONTH_1498))
        print("[SMOKE] OK")


if __name__ == "__main__":
    asyncio.run(run())
