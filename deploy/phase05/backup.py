#!/usr/bin/env python3
"""Consistent SQLite snapshot. Dry-run unless --apply is explicit."""
import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote


parser = argparse.ArgumentParser()
parser.add_argument("--database", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--apply", action="store_true")
args = parser.parse_args()
if not args.apply:
    print(f"DRY RUN: SQLite backup {args.database} -> {args.output}")
    raise SystemExit(0)
if not args.database.is_file() or args.output.exists() or args.database.resolve() == args.output.resolve():
    raise SystemExit("database missing or output already exists")
if not args.output.parent.is_dir():
    raise SystemExit("backup parent missing")
fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(fd)
try:
    with closing(sqlite3.connect(f"file:{quote(str(args.database.resolve()))}?mode=ro", uri=True)) as source, \
         closing(sqlite3.connect(args.output)) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("integrity check failed")
        if target.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("foreign key check failed")
        target.commit()
except Exception:
    args.output.unlink(missing_ok=True)
    raise
print("backup and SQLite integrity checks: OK")
