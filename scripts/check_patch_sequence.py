#!/usr/bin/env python3
"""Validate the ordered PATCH-00..17 rollout and its acceptance contracts."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "patches/sequence.json"
PATCH_ID = re.compile(r"^PATCH-(\d{2})$")


def _command_target_exists(parts: list[str]) -> bool:
    if len(parts) >= 2 and parts[1].endswith(".py"):
        return (ROOT / parts[1]).is_file()
    if len(parts) >= 4 and parts[1:3] == ["-m", "unittest"]:
        modules = [value for value in parts[3:] if not value.startswith("-")]
        return all((ROOT / (module.replace(".", "/") + ".py")).is_file() for module in modules)
    return False


def validate(ledger: Path = LEDGER) -> list[str]:
    data = json.loads(ledger.read_text(encoding="utf-8"))
    errors: list[str] = []
    patches = data.get("patches") or []
    expected = [f"PATCH-{number:02d}" for number in range(18)]
    actual = [item.get("id") for item in patches]
    if actual != expected:
        errors.append(f"patch order must be {expected}, got {actual}")
    phase_order = [value for phase in data.get("phases") or [] for value in phase.get("patches") or []]
    if phase_order != expected:
        errors.append("phase order must contain PATCH-00..17 exactly once")
    known: set[str] = set()
    for index, item in enumerate(patches):
        patch_id = str(item.get("id") or "")
        if not PATCH_ID.fullmatch(patch_id):
            errors.append(f"invalid patch id: {patch_id!r}")
        expected_requires = [] if index == 0 else [expected[index - 1]]
        if item.get("requires") != expected_requires:
            errors.append(f"{patch_id}: requires must be {expected_requires}")
        if any(value not in known for value in item.get("requires") or []):
            errors.append(f"{patch_id}: dependency is not completed earlier")
        tests = item.get("tests") or []
        if not tests:
            errors.append(f"{patch_id}: at least one acceptance command is required")
        for command in tests:
            parts = shlex.split(command)
            if len(parts) < 2 or parts[0] != "python":
                errors.append(f"{patch_id}: unsupported acceptance command {command!r}")
            elif not _command_target_exists(parts):
                errors.append(f"{patch_id}: acceptance target does not exist for {command!r}")
        known.add(patch_id)
    if not str(data.get("enforced_after_commit") or "").strip():
        errors.append("enforced_after_commit is required")
    exceptions = data.get("legacy_subject_exceptions") or []
    seen_exception_commits: set[str] = set()
    for item in exceptions:
        commit = str(item.get("commit") or "")
        subject = str(item.get("subject") or "")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            errors.append(f"invalid legacy exception commit: {commit!r}")
        if not subject.strip():
            errors.append(f"legacy exception {commit!r} requires the exact subject")
        if commit in seen_exception_commits:
            errors.append(f"duplicate legacy exception commit: {commit}")
        seen_exception_commits.add(commit)
    return errors


def main() -> int:
    errors = validate()
    if errors:
        for error in errors:
            print(f"PATCH SEQUENCE ERROR: {error}")
        return 1
    print("PATCH-00..17 sequence and acceptance contracts are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
