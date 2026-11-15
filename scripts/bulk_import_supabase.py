"""Resumable, bounded-memory NASA FIRMS archive importer for EmberLens Supabase.

Run on your own computer, not in Supabase SQL Editor or Vercel. The same
normalization/fingerprints as the EmberLens UI are used. ZIPs are streamed.
After interruption, rerun the command; committed detections are deduplicated.
Completed members are tracked in archive_manifests and skipped on reruns.

Examples (PowerShell; database password requested securely at runtime):
    python scripts/bulk_import_supabase.py --dataset MODIS_SP C:\\FIRMS\\modis.csv
    python scripts/bulk_import_supabase.py --dataset VIIRS_SNPP_SP C:\\FIRMS\\snpp.zip \\
      --dataset VIIRS_NOAA20_SP C:\\FIRMS\\noaa20.zip \\
      --dataset VIIRS_NOAA21_SP C:\\FIRMS\\noaa21.zip
"""
from __future__ import annotations

import argparse
import hashlib
import getpass
import io
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.archive import INSERT_DETECTIONS, discover_archives, iter_archive_members, sha256_file  # noqa: E402
from backend.core import SOURCES, iter_firms_csv  # noqa: E402
from backend.postgres import connect_postgres  # noqa: E402

# Project-specific Supabase direct PostgreSQL connection.
# The password is NEVER saved in this file. It is requested at runtime.
# Supabase Session Pooler configuration
SUPABASE_HOST = "aws-0-ap-southeast-2.pooler.supabase.com"
SUPABASE_PORT = 5432
SUPABASE_USER = "postgres.dhqopulxjrlbuzhdjjyk"
SUPABASE_DATABASE = "postgres"


def resolve_database_url():
    """Prefer an explicit environment override, otherwise prompt securely."""
    override = (os.getenv('SUPABASE_DB_URL') or '').strip()
    if override:
        return override
    password = getpass.getpass('Supabase database password (hidden): ')
    if not password:
        raise ValueError('Database password is required.')
    return (f'postgresql://{SUPABASE_USER}:{quote(password, safe="")}@'
            f'{SUPABASE_HOST}:{SUPABASE_PORT}/{SUPABASE_DATABASE}?sslmode=require')


def import_member(db, stream, source: str, filename: str, digest: str, *, batch_size: int = 2000, progress=None):
    if source not in SOURCES:
        raise ValueError(f'Unsupported source: {source}')
    if not 1 <= batch_size <= 10000:
        raise ValueError('batch_size must be 1..10000')
    existing = db.execute('SELECT id FROM archive_manifests WHERE source=? AND sha256=? LIMIT 1',
                          (source, digest)).fetchone()
    if existing:
        return {'source': source, 'filename': filename, 'status': 'already_completed', 'rows_added': 0}
    stats = {'rows_seen': 0, 'rows_accepted': 0, 'rows_invalid': 0,
             'first_date': None, 'last_date': None}
    added = 0
    batch = []

    def flush():
        nonlocal added
        if not batch:
            return
        # Commit each bounded batch to avoid a multi-million-row transaction.
        # If killed, rerun; the fingerprint PK avoids duplicated detections.
        try:
            with db:
                cursor = db.executemany(INSERT_DETECTIONS, batch)
                added += max(cursor.rowcount, 0)
        except Exception:
            db.rollback()
            raise
        batch.clear()
        if progress:
            progress(stats, added)

    try:
        for row in iter_firms_csv(stream, source, stats):
            batch.append(row)
            if len(batch) >= batch_size:
                flush()
        flush()
        if stats['rows_seen'] and not stats['rows_accepted']:
            raise ValueError(f'No valid detections in {filename}; check satellite/source selection')
        # Mark completed ONLY after all rows are read and committed.
        with db:
            db.execute('''INSERT INTO archive_manifests
                (source,filename,sha256,rows_seen,rows_invalid,rows_accepted,rows_added,first_date,last_date)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (source, filename[:512], digest, stats['rows_seen'], stats['rows_invalid'],
                 stats['rows_accepted'], added, stats['first_date'], stats['last_date']))
    except Exception:
        db.rollback()
        raise
    return {**stats, 'source': source, 'filename': filename, 'status': 'completed',
            'rows_added': added, 'coverage_claim': 'positive_detections_only'}


def import_path(db, input_path: Path, source: str, batch_size: int = 2000, progress=None):
    results = []
    for path in discover_archives(input_path):
        # Hash on compressed archive first to avoid huge memory consumption.
        file_hash = sha256_file(path)
        for member_name, raw in iter_archive_members(path):
            digest = hashlib.sha256((file_hash + '|' + member_name).encode()).hexdigest()
            # Keep input files with identical filenames from different directories distinct.
            display = str(path.resolve()) + '!' + member_name
            with io.TextIOWrapper(raw, encoding='utf-8-sig', newline='') as stream:
                results.append(import_member(db, stream, source, display, digest,
                                             batch_size=batch_size, progress=progress))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dataset', nargs=2, action='append', required=True, metavar=('SOURCE', 'PATH'),
                        help='Repeat for MODIS_SP / VIIRS_SNPP_SP / VIIRS_NOAA20_SP / VIIRS_NOAA21_SP')
    parser.add_argument('--batch-size', type=int, default=2000, help='Rows committed at a time (default 2000)')
    parser.add_argument('--no-progress', action='store_true')
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 10000:
        parser.error('batch size must be 1..10000')
    for source, path in args.dataset:
        if source not in SOURCES:
            parser.error(f'Unsupported source {source}; choices: {", ".join(sorted(SOURCES))}')
        if not Path(path).exists():
            parser.error(f'Input not found: {path}')
    try:
        url = resolve_database_url()
    except ValueError as exc:
        parser.error(str(exc))
    db = connect_postgres(url)
    started = time.monotonic()
    results = []
    try:
        # Existing deployment migrations create the needed schema. Do not
        # automatically create/change production tables from a bulk importer.
        db.execute('SELECT 1 FROM detections LIMIT 1')
        db.execute('SELECT 1 FROM archive_manifests LIMIT 1')
        db.commit()
        for source, path in args.dataset:
            print(f'Importing {source}: {path}', flush=True)
            last_report = [0]
            def report(stats, added):
                if not args.no_progress and stats['rows_seen'] - last_report[0] >= 50000:
                    print(f'  {stats["rows_seen"]:,} scanned; {added:,} new in this run; '
                          f'{stats["rows_invalid"]:,} invalid', flush=True)
                    last_report[0] = stats['rows_seen']
            for item in import_path(db, Path(path), source, args.batch_size, report):
                results.append(item)
                print(f'  {item["status"]}: {item["filename"]} ({item["rows_added"]:,} new)', flush=True)
    finally:
        db.close()
    print(json.dumps({'seconds': round(time.monotonic() - started, 2),
                      'rows_added': sum(r['rows_added'] for r in results),
                      'files': len(results), 'results': results,
                      'note': 'FIRMS archives document detected hotspots, not confirmed observation coverage.'},
                     indent=2))


if __name__ == '__main__':
    main()
