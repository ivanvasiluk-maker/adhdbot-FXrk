#!/usr/bin/env python3
"""Fail fast on Python syntax errors and unresolved merge artifacts.

This script is intentionally small and dependency-free so it can run during the
Docker build before the bot starts. It scans every tracked Python file instead
of maintaining a fragile hand-written module list in the Dockerfile.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")
REPO_ROOT = Path(__file__).resolve().parents[1]


def tracked_python_files() -> list[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=REPO_ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return sorted(REPO_ROOT.rglob("*.py"))

    files = []
    for line in result.stdout.splitlines():
        path = REPO_ROOT / line
        if path.exists():
            files.append(path)
    return files


def check_file(path: Path) -> list[str]:
    rel = path.relative_to(REPO_ROOT)
    source = path.read_text(encoding="utf-8")
    errors: list[str] = []

    for line_number, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if any(stripped.startswith(marker) for marker in CONFLICT_MARKERS):
            errors.append(f"{rel}:{line_number}: unresolved merge conflict marker")

    try:
        ast.parse(source, filename=str(rel))
    except SyntaxError as exc:
        errors.append(f"{rel}:{exc.lineno or 0}:{exc.offset or 0}: {exc.msg}")

    return errors


def main() -> int:
    errors: list[str] = []
    for path in tracked_python_files():
        errors.extend(check_file(path))

    if errors:
        print("Build sanity check failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("Build sanity check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
