#!/usr/bin/env python3
"""Database and application spool cleanup utility for the Relay FTP web application.

Usage:
    uv run --locked --no-dev python tools/clean_db.py [--data-dir app-data] [--all] [--force]
"""

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def clean_database(
    data_dir: Path, *, clean_all: bool = False, force: bool = False, recreate: bool = False
) -> int:
    """Clean the SQLite database and optional transfer spool files.

    What it does:
        Removes SQLite database files (main db, WAL journal, and shared-memory index)
        and optionally purges transfer spool files from the specified application data directory.

    How it does:
        1. Identifies targets: app.db, app.db-wal, app.db-shm, and optionally the transfers spool
            directory.
        2. Prompts user for interactive confirmation unless force is True.
        3. Removes selected files and directories with shutil.rmtree and Path.unlink.
        4. Re-initializes a fresh database schema via Database(db_path) if recreate is True.
        5. Returns exit code 0 on success or 1 on user abortion.
    """
    # Keep the explicit cleanup inventory, confirmation, and removal sequence together.
    # pylint: disable=too-many-locals
    # pylint: disable=too-many-branches
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
        size = (
            t.stat().st_size
            if t.is_file()
            else sum(f.stat().st_size for f in t.glob('**/*') if f.is_file())
        )
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
            # Delay this optional action until after the standalone script bootstraps sys.path.
            from ftp_lab.webapp.database import Database  # pylint: disable=import-outside-toplevel

            Database(db_path)
            print(f"Re-initialized empty database schema: {db_path.name}")
        except (OSError, sqlite3.Error, ValueError) as exc:
            print(f"Could not re-initialize database: {exc}", file=sys.stderr)

    print(f"Cleanup complete. Cleaned {removed_count} item(s).")
    return 0


def main(argv=None):
    """Run the database cleanup command-line tool.

    What it does:
        Parses CLI arguments and invokes clean_database with the configured options.

    How it does:
        Constructs an ArgumentParser, parses CLI options (--data-dir, --all, --force, --recreate),
        and delegates execution to clean_database.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--data-dir',
        type=Path,
        default=Path('app-data'),
        help='Path to app-data directory (default: app-data)',
    )
    parser.add_argument(
        '--all', action='store_true', help='Also empty the transfers spool directory'
    )
    parser.add_argument('--force', '-f', action='store_true', help='Do not prompt for confirmation')
    parser.add_argument(
        '--recreate',
        '-r',
        action='store_true',
        help='Re-initialize a fresh empty database schema after removal',
    )
    args = parser.parse_args(argv)

    return clean_database(
        args.data_dir, clean_all=args.all, force=args.force, recreate=args.recreate
    )


if __name__ == '__main__':
    sys.exit(main())
