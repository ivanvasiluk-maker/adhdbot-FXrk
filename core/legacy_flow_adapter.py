"""Single write boundary for legacy ``stage``/``day`` mirrors.

The normalized flow state remains authoritative.  Legacy delivery code may
temporarily update its UI mirrors only through these functions, which gives CI
one enforceable boundary while call sites are migrated incrementally.
"""

from __future__ import annotations

from typing import Any, MutableMapping


def set_legacy_stage(user: MutableMapping[str, Any], stage: str) -> str:
    value = str(stage or "")
    user["stage"] = value
    return value


def set_legacy_day(user: MutableMapping[str, Any], day: int) -> int:
    value = max(1, int(day))
    user["day"] = value
    return value
