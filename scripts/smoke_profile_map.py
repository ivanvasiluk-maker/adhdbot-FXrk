import asyncio
import os
import tempfile
import types

import bot
from db import init_db, migrate_db, get_user, save_user, get_user_profile


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
        if kb and getattr(kb, "keyboard", None):
            for row in kb.keyboard:
                for btn in row:
                    kb_texts.append(getattr(btn, "text", ""))

        print("[SMOKE] perfectionism main_pattern:", p1.get("main_pattern"))
        print("[SMOKE] failed main_pattern:", p2.get("main_pattern"))
        print("[SMOKE] done best_skill:", p3.get("best_skill"))
        print("[SMOKE] done action_done_count:", p3.get("action_done_count"))
        print("[SMOKE] recommended_track:", p3.get("recommended_track"))
        print("[SMOKE] offer has month button:", any("€14.98" in t and "Месяц" in t for t in kb_texts))
        print("[SMOKE] offer contains primary map title:", "Твоя первичная карта" in offer_text)
        print("[SMOKE] payment url month set:", bool(bot.PAYMENT_URL_MONTH_1498))
        print("[SMOKE] OK")


if __name__ == "__main__":
    asyncio.run(run())
