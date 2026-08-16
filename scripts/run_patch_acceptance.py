#!/usr/bin/env python3
"""Run acceptance commands for one ordered patch after validating dependencies."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path

from check_patch_sequence import validate

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "patches/sequence.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("patch", help="PATCH-00 through PATCH-17")
    args = parser.parse_args()
    errors = validate()
    if errors:
        raise SystemExit("; ".join(errors))
    patches = json.loads(LEDGER.read_text(encoding="utf-8"))["patches"]
    selected = next((item for item in patches if item["id"] == args.patch), None)
    if selected is None:
        raise SystemExit(f"unknown patch: {args.patch}")
    env = dict(os.environ, OPENAI_API_KEY="", TEST_MODE="0", PAYMENT_ACCEPT_ANY="0")
    for command in selected["tests"]:
        print("+", command, flush=True)
        subprocess.run(shlex.split(command), cwd=ROOT, env=env, check=True)
    print(f"{args.patch} acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
