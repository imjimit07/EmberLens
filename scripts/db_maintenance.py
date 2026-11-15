"""Safe SQLite maintenance and online backup for EmberLens.

Examples:
  python scripts/db_maintenance.py --integrity-check --optimize
  python scripts/db_maintenance.py --backup backups/emberlens-backup.sqlite
  python scripts/db_maintenance.py --checkpoint --optimize
  python scripts/db_maintenance.py --vacuum   # offline/quiet period only
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.core import connect  # noqa: E402
DB_PATH = os.getenv("EMBERLENS_DB", str(ROOT / "data" / "emberlens.sqlite"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=DB_PATH, help='SQLite database path')
    parser.add_argument('--backup', help='Destination path for an online consistent backup')
    parser.add_argument('--integrity-check', action='store_true', help='Run PRAGMA integrity_check')
    parser.add_argument('--optimize', action='store_true', help='Run PRAGMA optimize (recommended periodically)')
    parser.add_argument('--checkpoint', action='store_true', help='Checkpoint the WAL file')
    parser.add_argument('--vacuum', action='store_true', help='Rebuild the database; run during a quiet period')
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    if not db_path.exists():
        parser.error(f'Database does not exist: {db_path}')

    report: dict[str, object] = {'database': str(db_path)}
    with connect(db_path, initialize=True) as source:
        if args.integrity_check:
            result = source.execute('PRAGMA integrity_check').fetchone()[0]
            report['integrity_check'] = result
            if result != 'ok':
                print(json.dumps(report, indent=2))
                return 2
        if args.optimize:
            source.execute('PRAGMA optimize')
            report['optimize'] = 'completed'
        if args.checkpoint:
            row = source.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            report['wal_checkpoint'] = list(row) if row else None
        if args.vacuum:
            source.execute('VACUUM')
            report['vacuum'] = 'completed'
        if args.backup:
            destination = Path(args.backup).expanduser().resolve()
            if destination == db_path:
                parser.error('Backup destination must differ from source database')
            destination.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(destination)) as target:
                source.backup(target)
            report['backup'] = str(destination)
            report['backup_bytes'] = destination.stat().st_size
        report['database_bytes'] = db_path.stat().st_size
        report['journal_mode'] = source.execute('PRAGMA journal_mode').fetchone()[0]
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
