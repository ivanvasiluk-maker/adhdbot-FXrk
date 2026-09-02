#!/usr/bin/env python3
"""PATCH-17 mandatory offline regression gate used locally and in CI."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

CHECKS = (
    (PYTHON, "scripts/check_patch_sequence.py"),
    (PYTHON, "scripts/check_patch_commits.py"),
    (PYTHON, "-m", "unittest", "discover", "-s", "tests"),
    (PYTHON, "scripts/required_regression_tests.py"),
    (PYTHON, "scripts/test_state_machine.py"),
    (PYTHON, "scripts/smoke_crisis_buttons.py"),
    (PYTHON, "scripts/smoke_global_buttons.py"),
    (PYTHON, "scripts/test_offer_buttons.py"),
    (PYTHON, "scripts/smoke_profile_map.py"),
    (PYTHON, "scripts/smoke_user_map_events.py"),
    (PYTHON, "scripts/smoke_skill_change.py"),
    (PYTHON, "scripts/smoke_trainer_switch.py"),
    (PYTHON, "scripts/smoke_post_action_buttons.py"),
    (PYTHON, "scripts/smoke_persistent_user_state.py"),
    (PYTHON, "scripts/smoke_day_flow.py"),
    (PYTHON, "scripts/check_build_sanity.py"),
    (PYTHON, "scripts/check_product_policy.py"),
    (PYTHON, "scripts/check_legacy_state_writes.py"),
    (PYTHON, "scripts/validate_skills.py"),
)


def run(command: tuple[str, ...], *, env: dict[str, str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def main() -> int:
    env = dict(os.environ)
    env.update({"OPENAI_API_KEY": "", "TEST_MODE": "0", "PAYMENT_ACCEPT_ANY": "0"})
    for command in CHECKS:
        command_env = env
        if command[1:] == ("scripts/required_regression_tests.py",):
            command_env = dict(env)
            command_env.update({
                "ENABLE_PAYMENTS": "1",
                "PAYMENT_URL": "https://pay.stripe.com/skiller-ci",
            })
        run(command, env=command_env)
    with tempfile.TemporaryDirectory() as directory:
        startup_env = dict(env)
        startup_env.update({
            "BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmno",
            "BOT_STARTUP_CHECK": "1",
            "DB_PATH": str(Path(directory) / "startup-check.db"),
        })
        run((PYTHON, "bot.py"), env=startup_env)
    print("Regression gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
