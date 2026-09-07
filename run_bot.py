"""Production entrypoint that installs optional admin routes before the main router."""

import asyncio
import sys

import bot
from broadcast_admin import router as broadcast_router


original_build_dispatcher = bot.build_dispatcher


def build_dispatcher_with_admin_routes():
    dispatcher = original_build_dispatcher()
    dispatcher.include_router(broadcast_router)
    return dispatcher


bot.build_dispatcher = build_dispatcher_with_admin_routes


if __name__ == "__main__":
    sys.exit(asyncio.run(bot.main()))
