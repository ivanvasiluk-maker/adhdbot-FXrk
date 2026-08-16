#!/usr/bin/env python3
"""Reject post-baseline commits that combine or omit PATCH ownership."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "patches/sequence.json"
SUBJECT = re.compile(r"^(PATCH-(\d{2})):\s+\S")


def validate_commits(rows: list[tuple[str, str]]) -> list[str]:
    """Validate one owner per commit and monotonically ordered PATCH numbers."""
    errors: list[str] = []
    previous_number = -1
    for commit, subject in rows:
        if subject.startswith(("Merge ", "Revert ")):
            continue
        match = SUBJECT.match(subject)
        labels = set(re.findall(r"PATCH-\d{2}", subject))
        if not match or len(labels) != 1:
            errors.append(f"{commit[:12]} must use one prefix like 'PATCH-07: ...': {subject}")
            continue
        number = int(match.group(2))
        if number < previous_number:
            errors.append(
                f"{commit[:12]} moves backwards from PATCH-{previous_number:02d} to PATCH-{number:02d}"
            )
        previous_number = max(previous_number, number)
    return errors


def main() -> int:
    data = json.loads(LEDGER.read_text(encoding="utf-8"))
    baseline = str(data["enforced_after_commit"])
    if baseline.startswith("path-introduction:"):
        tracked_path = baseline.partition(":")[2]
        introduced = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%H", "--", tracked_path],
            cwd=ROOT, text=True, capture_output=True,
        )
        candidates = introduced.stdout.splitlines()
        if introduced.returncode != 0 or not candidates:
            print(f"PATCH COMMIT ERROR: cannot find introduction commit for {tracked_path}")
            return 1
        baseline = candidates[-1]
    result = subprocess.run(
        ["git", "log", "--reverse", "--format=%H%x09%s", f"{baseline}..HEAD"],
        cwd=ROOT, text=True, capture_output=True,
    )
    if result.returncode != 0:
        print(f"PATCH COMMIT ERROR: cannot inspect baseline {baseline}: {result.stderr.strip()}")
        return 1
    rows = [tuple(line.partition("\t")[::2]) for line in result.stdout.splitlines()]
    errors = validate_commits(rows)
    if errors:
        for error in errors:
            print(f"PATCH COMMIT ERROR: {error}")
        return 1
    print("Post-baseline commits each declare exactly one PATCH owner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
