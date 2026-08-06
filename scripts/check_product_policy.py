#!/usr/bin/env python3
"""CLI review check for a JSON feature specification."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.product_policy import evaluate_feature


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python scripts/check_product_policy.py FEATURE.json", file=sys.stderr)
        return 2
    try:
        spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"INVALID_FEATURE_SPEC: {exc}", file=sys.stderr)
        return 2
    decision = evaluate_feature(spec)
    print(f"{decision['reason_code']}: {decision['explanation']}")
    return 0 if decision["allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
