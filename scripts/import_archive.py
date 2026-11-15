"""Load historical NASA FIRMS archive CSV, CSV.GZ or ZIP files without RAM spikes.

Example:
    python scripts/import_archive.py --source MODIS_SP --input downloads/modis_2020.csv
    python scripts/import_archive.py --source VIIRS_SNPP_SP --input downloads/viirs/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.archive import discover_archives, import_archive_file  # noqa: E402
from backend.core import SOURCES, connect  # noqa: E402
from backend.postgres import connect_postgres, initialize_postgres_schema  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='Import downloaded NASA FIRMS historical archives')
    parser.add_argument('--source', choices=sorted(SOURCES), required=True,
                        help='Match the source to the downloaded product; archive formats do not reliably encode SP/NRT.')
    parser.add_argument('--input', type=Path, required=True, help='CSV, CSV.GZ, ZIP or a directory of archives')
    parser.add_argument('--db', type=Path, default=Path(os.getenv('EMBERLENS_DB') or
                        Path(__file__).resolve().parents[1] / 'data' / 'emberlens.sqlite'))
    args = parser.parse_args()
    files = discover_archives(args.input)
    if not files:
        parser.error('No CSV/CSV.GZ/ZIP files found')
    report = []
    database_url = (os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL') or '').strip()
    if database_url:
        db = connect_postgres(database_url)
        try:
            initialize_postgres_schema(db)
            with db:
                for path in files:
                    report.extend(import_archive_file(db, path, args.source))
        finally:
            db.close()
        destination = 'Supabase PostgreSQL'
    else:
        args.db.parent.mkdir(parents=True, exist_ok=True)
        with connect(args.db) as db:
            for path in files:
                report.extend(import_archive_file(db, path, args.source))
        destination = str(args.db)
    print(json.dumps({'destination': destination, 'files': len(files), 'members_imported': len(report),
                      'rows_added': sum(x['rows_added'] for x in report),
                      'rows_invalid': sum(x['rows_invalid'] for x in report),
                      'details': report,
                      'warning': 'Archive CSVs show detected hotspots only; missing days are unknown, not certified zero-fire days.'},
                     indent=2))


if __name__ == '__main__':
    main()
