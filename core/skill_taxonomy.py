"""Canonical PATCH-18 taxonomy loaded from the repository data file.

The file uses the JSON subset of YAML 1.2 so runtime loading stays dependency-free.
"""

from __future__ import annotations

import json
from pathlib import Path

TAXONOMY_PATH = Path(__file__).resolve().parents[1] / "data/skills/taxonomy.yaml"
_REQUIRED_GROUPS = {
    "approaches", "statuses", "mechanisms", "contexts", "action_phases",
    "emotions", "task_types", "barrier_types",
}


def load_taxonomy(path: str | Path = TAXONOMY_PATH) -> dict[str, frozenset[str]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = _REQUIRED_GROUPS - set(raw)
    if missing:
        raise ValueError(f"skill taxonomy missing groups: {', '.join(sorted(missing))}")
    result = {}
    for name in sorted(_REQUIRED_GROUPS):
        values = tuple(str(value).strip() for value in raw[name] if str(value).strip())
        if not values or len(values) != len(set(values)):
            raise ValueError(f"skill taxonomy group {name!r} must be non-empty and unique")
        result[name] = frozenset(values)
    return result


TAXONOMY = load_taxonomy()
APPROACHES = TAXONOMY["approaches"]
QUALITY_STATUSES = TAXONOMY["statuses"]
MECHANISM_CODES = TAXONOMY["mechanisms"]
CONTEXTS = tuple(sorted(TAXONOMY["contexts"]))
ACTION_PHASES = tuple(sorted(TAXONOMY["action_phases"]))
EMOTIONS = TAXONOMY["emotions"]
TASK_TYPES = TAXONOMY["task_types"]
BARRIER_TYPES = TAXONOMY["barrier_types"]
