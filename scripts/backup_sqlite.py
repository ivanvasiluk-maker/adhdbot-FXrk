#!/usr/bin/env python3
"""Create an online, integrity-checked SQLite backup before deployment."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def backup_database(source: Path, destination: Path) -> Path:
    if not source.is_file():
        raise FileNotFoundError(f"SQLite database not found: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite backup: {destination}")
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)
        result = backup_db.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path, help="Source SQLite database")
    parser.add_argument("--output", type=Path, help="Backup path; defaults to <db>.<UTC>.backup")
    args = parser.parse_args()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = args.output or args.db.with_name(f"{args.db.name}.{timestamp}.backup")
    print(backup_database(args.db, destination))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
