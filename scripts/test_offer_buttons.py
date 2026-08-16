#!/usr/bin/env python3
"""Manual/static smoke test for SKILLER offer callback wiring."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BOT_TEXT = (REPO_ROOT / "bot.py").read_text(encoding="utf-8")


def callback_is_registered(callback: str) -> bool:
    callback_to_key = {
        "offer:bot": "bot",
        "offer:live_review": "live",
        "offer:guided": "guided",
        "offer:group": "group",
        "offer:compare": "compare",
        "offer:stay_free": "stay_free",
        "offer:back": "back",
        "offer:request_group": "request_group",
    }
    key = callback_to_key[callback]
    return (
        f'"{key}": "{callback}"' in BOT_TEXT
        and f'OFFER_CALLBACKS["{key}"]' in BOT_TEXT
        and "async def on_offer_callbacks" in BOT_TEXT
    )


def test_offer_buttons_have_handlers() -> None:
    required_callbacks = [
        "offer:bot",
        "offer:live_review",
        "offer:guided",
        "offer:group",
        "offer:compare",
        "offer:stay_free",
        "offer:back",
        "offer:request_group",
    ]

    for cb in required_callbacks:
        assert callback_is_registered(cb), f"Missing handler for {cb}"


if __name__ == "__main__":
    test_offer_buttons_have_handlers()
    print("Offer button callback smoke test passed")
