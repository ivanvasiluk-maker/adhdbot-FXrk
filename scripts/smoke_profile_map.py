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

        # 1) Perfectionism trigger -> perfectionism_start_block
        m1 = DummyMessage(uid, "Хочу сделать идеально, потом доделаю")
        await bot.main_flow(m1)
        p1 = await get_user_profile(uid, db_path)

        # 2) Too hard branch -> entry_too_large (via ❌ Не сделал)
        m2 = DummyMessage(uid, "❌ Не сделал")
        await bot.main_flow(m2)
        p2 = await get_user_profile(uid, db_path)

        # 3) Done branch -> best_skill
        u = await get_user(uid, db_path)
        u["stage"] = "training"
        await save_user(u, db_path)
        m3 = DummyMessage(uid, "✅ Сделал(а)")
        await bot.main_flow(m3)
        p3 = await get_user_profile(uid, db_path)

        # 4) Day3 offer and payment URL fallback selection
        u = await get_user(uid, db_path)
        bot.PAYMENT_URL_MONTH_1498 = "https://pay.example/month1498"
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
        has_adaptive_payment = any("€14.98" in t and "Продолжить" in t for t in kb_texts)
        has_primary_map = "🧭 Первичная карта" in offer_text or "🧭 За первые дни карта уже начала собираться." in offer_text
        has_day3_conclusion = "📌 ПОЛНОЕ ЗАКЛЮЧЕНИЕ ПОСЛЕ 3 ДНЕЙ" in offer_text or "Похоже, твой цикл сейчас такой:" in offer_text
        has_personal_offer = ("ТВОЙ ПРОФИЛЬ" in offer_text or "Полный режим:" in offer_text) and "14.98 €/месяц" in offer_text
        has_model_value = ("Мы продаём не навыки" in offer_text and "персональной модели" in offer_text) or "личную инструкцию запуска" in offer_text
        has_selling_specifics = ("какие навыки реально работают" in offer_text and "какую сложность выдерживает" in offer_text) or ("подбор шага под твои реакции" in offer_text and "разбор залипаний" in offer_text)
        assert int(task_start.get("value") or 0) > 20, avatar
        assert prompt.startswith("USER PROFILE"), prompt
        assert int(dev_map.get("behavior_events_count") or 0) >= 1, dev_map
        assert dev_map.get("helps"), dev_map
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
        assert has_adaptive_payment, kb_texts
        assert has_primary_map, offer_text
        assert has_day3_conclusion, offer_text
        assert has_personal_offer, offer_text
        assert has_model_value, offer_text
        assert has_selling_specifics, offer_text
        print("[SMOKE] offer has adaptive payment button:", has_adaptive_payment)
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
