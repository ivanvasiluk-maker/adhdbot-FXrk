#!/usr/bin/env python3
"""Import Google/XLSX skill rows or export legacy cards, always review-first."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skills import SKILLS_DB  # noqa: E402
from core.product_config import SKILL_LIBRARY_SOURCE_URL  # noqa: E402
from core.skill_importer import map_rows  # noqa: E402
from core.skill_spreadsheet import flatten, read_csv, read_xlsx, skill_tables  # noqa: E402


def export_rows() -> list[dict]:
    rows = []
    for skill_id, raw in SKILLS_DB.items():
        instruction = raw.get("how") or raw.get("goal") or raw.get("name") or skill_id
        minimum = raw.get("minimum") or instruction
        rows.append({
            "skill_id": skill_id, "version": "1.0.0", "status": "experimental",
            "migration_confidence": "low", "title": raw.get("name") or skill_id,
            "short_title": raw.get("name") or skill_id, "source_family": "OTHER",
            "mechanisms": [raw.get("mechanism") or "executive_start_deficit"],
            "action_targets": ["start"], "contexts": ["other"],
            "contraindications": ["acute_crisis", "severe_deterioration"],
            "safety_tags": ["requires_review"], "prerequisites": [], "fallback_skills": [],
            "fallback_policy": "legacy_flow", "next_skills": [],
            "difficulty_levels": [{"level": 1, "instruction_key": "minimum"},
                                  {"level": 2, "instruction_key": "standard"}],
            "variants": {"minimum": str(minimum), "standard": str(instruction)},
            "minimum_successes": 2,
            "mastery_criteria": {"successful_practice_count": 2, "independent_use_count": 2},
            "maintenance_rule": "on_similar_mechanism", "generalization_contexts": [],
            "completion_criteria": str(minimum), "feedback_schema": {"action_started": "required"},
            "safety_level": "review_required", "source_references": [{"internal_ref": "legacy_skills_db"}],
            "reviewer_status": "unreviewed", "trainer_texts": {
                "marsha": str(instruction), "skinny": str(instruction), "beck": str(instruction),
            },
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/skills/legacy_export.json")
    parser.add_argument("--write", action="store_true", help="write output; otherwise dry-run only")
    parser.add_argument("--source", help="Google Sheets URL or local .xlsx/.csv file")
    parser.add_argument("--google", action="store_true", help="use SKILL_LIBRARY_SOURCE_URL")
    parser.add_argument("--inspect", action="store_true", help="print sheet names/headers without importing")
    args = parser.parse_args()
    source = args.source or (SKILL_LIBRARY_SOURCE_URL if args.google else "")
    if source:
        try:
            content, label = load_source(source)
            tables = read_csv(content) if label.lower().endswith(".csv") else read_xlsx(content)
        except (OSError, ValueError, httpx.HTTPError) as exc:
            print(f"Import failed: {exc}", file=sys.stderr)
            return 1
        if args.inspect:
            for table in tables:
                print(f"{table.name}: {', '.join(table.headers)} ({len(table.rows)} rows)")
            return 0
        import_tables = skill_tables(tables)
        if not import_tables:
            print("Import stopped: no worksheet with skill titles and instructions found", file=sys.stderr)
            return 1
        rows, problems = map_rows(flatten(import_tables), source_ref=source)
        for problem in problems:
            print(f"row {problem.row_number} [{problem.skill_id or '?'}]: {problem.message}", file=sys.stderr)
        if problems:
            print("Import stopped: fix or explicitly exclude invalid rows", file=sys.stderr)
            return 1
    else:
        rows = export_rows()
    if args.write:
        target = ROOT / args.output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {len(rows)} experimental cards to {target}")
    else:
        kind = "spreadsheet" if source else "legacy"
        print(f"Dry run: {len(rows)} {kind} cards would be written; production is not changed")
    return 0


def google_export_url(value: str) -> str:
    marker = "/spreadsheets/d/"
    if marker not in value:
        return value
    sheet_id = value.split(marker, 1)[1].split("/", 1)[0]
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"


def load_source(source: str) -> tuple[bytes, str]:
    path = Path(source)
    if path.exists():
        return path.read_bytes(), path.name
    url = google_export_url(source)
    response = httpx.get(url, follow_redirects=True, timeout=60)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        raise ValueError("Google returned HTML; share the sheet for link access or provide a local XLSX export")
    return response.content, ".csv" if "csv" in content_type else ".xlsx"


if __name__ == "__main__":
    raise SystemExit(main())
