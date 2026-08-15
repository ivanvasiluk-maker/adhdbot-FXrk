#!/usr/bin/env python3
"""Validate the complete file library and optionally write its hash manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.skill_registry import FileSkillRegistry, SkillLibraryError  # noqa: E402
from skills import SKILL_REGISTRY  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="data/skills")
    parser.add_argument("--manifest", default="data/skills_manifest.json")
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()
    try:
        registry = FileSkillRegistry.load(
            ROOT / args.path, fail_closed=True, baseline_skills=SKILL_REGISTRY.all(),
        )
    except (OSError, ValueError, SkillLibraryError) as exc:
        print(f"Skill validation failed: {exc}", file=sys.stderr)
        return 1
    manifest = registry.manifest()
    if args.write_manifest:
        target = ROOT / args.manifest
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Validated {len(manifest['cards'])} versioned skill cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
