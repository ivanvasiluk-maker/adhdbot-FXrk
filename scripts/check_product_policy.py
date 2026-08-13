#!/usr/bin/env python3
"""Validate one feature specification or every JSON spec in a directory."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.product_policy import evaluate_feature


def _paths(value: Path) -> list[Path]:
    if value.is_dir():
        return sorted(value.glob("*.json"))
    return [value]


def main() -> int:
    if len(sys.argv) > 2:
        print("usage: python scripts/check_product_policy.py [FEATURE.json|DIRECTORY]", file=sys.stderr)
        return 2
    target = Path(sys.argv[1]) if len(sys.argv) == 2 else Path("features")
    paths = _paths(target)
    if not paths:
        print(f"MISSING_FEATURE_SPECS: no JSON specifications in {target}", file=sys.stderr)
        return 2
    failed = False
    names: set[str] = set()
    for path in paths:
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"{path}: INVALID_FEATURE_SPEC: {exc}", file=sys.stderr)
            failed = True
            continue
        name = str(spec.get("name") or "").strip()
        if not name or name in names:
            print(f"{path}: INVALID_FEATURE_NAME: name is required and must be unique", file=sys.stderr)
            failed = True
            continue
        names.add(name)
        decision = evaluate_feature(spec)
        print(f"{path}: {decision['reason_code']}: {decision['explanation']}")
        failed = failed or not decision["allowed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
