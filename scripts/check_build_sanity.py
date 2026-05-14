#!/usr/bin/env python3
"""Fail fast on syntax errors and unresolved merge artifacts.

This script is intentionally small and dependency-free so it can run during the
Docker build before the bot starts. It checks Python syntax and scans deployable
text files for merge-conflict markers, so a bad conflict resolution fails during
build instead of breaking the running bot.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

CONFLICT_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")
REPO_ROOT = Path(__file__).resolve().parents[1]
SKIPPED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
TEXT_EXTENSIONS = {
    "",
    ".cfg",
    ".conf",
    ".css",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def repo_files() -> list[Path]:
    """Return files that should be safe to ship in the Docker build context."""
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return sorted(
            path
            for path in REPO_ROOT.rglob("*")
            if path.is_file() and not any(part in SKIPPED_DIRS for part in path.parts)
        )

    files = []
    for line in result.stdout.splitlines():
        path = REPO_ROOT / line
        if path.is_file():
            files.append(path)
    return files


def should_scan_as_text(path: Path) -> bool:
    if path.name in {"Dockerfile", ".dockerignore"}:
        return True
    return path.suffix.lower() in TEXT_EXTENSIONS


def read_text_if_scannable(path: Path) -> str | None:
    if not should_scan_as_text(path):
        return None
    data = path.read_bytes()
    if b"\x00" in data:
        return None
    return data.decode("utf-8", errors="replace")


def check_conflict_markers(path: Path, source: str) -> list[str]:
    rel = path.relative_to(REPO_ROOT)
    errors: list[str] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        if any(stripped.startswith(marker) for marker in CONFLICT_MARKERS):
            errors.append(f"{rel}:{line_number}: unresolved merge conflict marker")
    return errors


def check_python_syntax(path: Path, source: str) -> list[str]:
    if path.suffix != ".py":
        return []

    rel = path.relative_to(REPO_ROOT)
    try:
        ast.parse(source, filename=str(rel))
    except SyntaxError as exc:
        return [f"{rel}:{exc.lineno or 0}:{exc.offset or 0}: {exc.msg}"]
    return []


def check_file(path: Path) -> list[str]:
    source = read_text_if_scannable(path)
    if source is None:
        return []
    return [
        *check_conflict_markers(path, source),
        *check_python_syntax(path, source),
    ]


def main() -> int:
    errors: list[str] = []
    for path in repo_files():
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
