#!/usr/bin/env python3
"""Database and application spool cleanup utility for the Relay FTP web application.

Usage:
    python3 tools/clean_db.py [--data-dir app-data] [--all] [--force]
"""

import argparse
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def clean_database(data_dir: Path, *, clean_all: bool = False, force: bool = False, recreate: bool = False) -> int:
    """Clean the SQLite database and optional transfer spool files."""
    db_path = data_dir / 'app.db'
    wal_path = data_dir / 'app.db-wal'
    shm_path = data_dir / 'app.db-shm'
    spool_dir = data_dir / 'transfers'

    targets: list[Path] = []
    for p in (db_path, wal_path, shm_path):
        if p.exists():
            targets.append(p)
    if clean_all and spool_dir.exists():
        targets.append(spool_dir)

    if not targets:
        print(f"No database or spool files found in '{data_dir}'. Nothing to clean.")
        return 0

    print(f"The following targets in '{data_dir}' will be removed/cleaned:")
    for t in targets:
        size = t.stat().st_size if t.is_file() else sum(f.stat().st_size for f in t.glob('**/*') if f.is_file())
        kind = 'directory' if t.is_dir() else 'file'
        print(f"  - {t.name} ({kind}, {size:,} bytes)")

    if not force:
        try:
            confirm = input("Are you sure you want to proceed? [y/N]: ").strip().lower()
            if confirm not in ('y', 'yes'):
                print("Aborted.")
                return 1
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            return 1

    removed_count = 0
    for t in targets:
        try:
            if t.is_dir():
                shutil.rmtree(t)
                t.mkdir(parents=True, exist_ok=True, mode=0o700)
                print(f"Emptied spool directory: {t.name}")
            else:
                t.unlink()
                print(f"Removed file: {t.name}")
            removed_count += 1
        except OSError as exc:
            print(f"Error removing {t.name}: {exc}", file=sys.stderr)

    if recreate:
        try:
            # Recreate blank database schema
            from ftp_lab.webapp.database import Database
            Database(db_path)
            print(f"Re-initialized empty database schema: {db_path.name}")
        except Exception as exc:
            print(f"Could not re-initialize database: {exc}", file=sys.stderr)

    print(f"Cleanup complete. Cleaned {removed_count} item(s).")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--data-dir', type=Path, default=Path('app-data'), help='Path to app-data directory (default: app-data)')
    parser.add_argument('--all', action='store_true', help='Also empty the transfers spool directory')
    parser.add_argument('--force', '-f', action='store_true', help='Do not prompt for confirmation')
    parser.add_argument('--recreate', '-r', action='store_true', help='Re-initialize a fresh empty database schema after removal')
    args = parser.parse_args(argv)

    return clean_database(args.data_dir, clean_all=args.all, force=args.force, recreate=args.recreate)


if __name__ == '__main__':
    sys.exit(main())
