#!/usr/bin/env python3
"""Prevent writes to legacy state mirrors outside their compatibility adapter."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    violations: list[str] = []
    for relative in ("bot.py", "flows.py"):
        path = ROOT / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            for target in targets:
                if not isinstance(target, ast.Subscript):
                    continue
                if not isinstance(target.value, ast.Name) or target.value.id not in {"u", "user", "fresh"}:
                    continue
                key = target.slice.value if isinstance(target.slice, ast.Constant) else None
                if key in {"stage", "day"}:
                    violations.append(f"{relative}:{node.lineno}: direct legacy {key!r} write")
    if violations:
        print("\n".join(violations))
        return 1
    print("Legacy state write boundary passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
