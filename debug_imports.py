#!/usr/bin/env python3
"""Диагностика импортов - проверяем какой модуль зависает."""

import sys
import warnings

print("[1] Testing basic imports...")

print("  - os...", end=" ", flush=True)
import os
print("✓")

print("  - re...", end=" ", flush=True)
import re
print("✓")

print("  - json...", end=" ", flush=True)
import json
print("✓")

print("  - time...", end=" ", flush=True)
import time
print("✓")

print("  - math...", end=" ", flush=True)
import math
print("✓")

print("  - asyncio...", end=" ", flush=True)
import asyncio
print("✓")

print("  - logging...", end=" ", flush=True)
import logging
print("✓")

print("\n[2] Testing third-party imports (with timeout)...")

print("  - dotenv...", end=" ", flush=True)
try:
    from dotenv import load_dotenv
    print("✓")
except Exception as e:
    print(f"✗ Error: {e}")

print("  - aiosqlite...", end=" ", flush=True)
try:
    import aiosqlite
    print("✓")
except Exception as e:
    print(f"✗ Error: {e}")

print("  - aiogram...", end=" ", flush=True)
sys.stdout.flush()
try:
    from aiogram import Bot, Dispatcher, Router, F
    print("✓")
except Exception as e:
    print(f"✗ Error: {e}")

print("  - openai...", end=" ", flush=True)
sys.stdout.flush()
try:
    import openai
    print("✓")
except Exception as e:
    print(f"✗ Error: {e}")

print("\n[3] Loading .env...", end=" ", flush=True)
load_dotenv(override=True)
print("✓")

print("  BOT_TOKEN present:", bool(os.getenv("BOT_TOKEN")))
print("\n✓ All imports successful!")
