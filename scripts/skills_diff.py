#!/usr/bin/env python3
"""Compare manifests and reject content changes without a version bump."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("before")
    parser.add_argument("after")
    args = parser.parse_args()
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    old = {(row["skill_id"], row["version"]): row for row in before.get("cards", [])}
    new = {(row["skill_id"], row["version"]): row for row in after.get("cards", [])}
    errors = []
    for key in sorted(old.keys() & new.keys()):
        if old[key]["sha256"] != new[key]["sha256"]:
            errors.append(f"{key[0]} changed without version bump ({key[1]})")
    for key in sorted(new.keys() - old.keys()):
        print(f"ADDED {key[0]}@{key[1]}")
    for key in sorted(old.keys() - new.keys()):
        print(f"REMOVED {key[0]}@{key[1]}")
    if errors:
        print("\n".join(errors))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
