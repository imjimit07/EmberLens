"""Resumable NASA FIRMS bulk importer for LOCAL SQLite (CSV, CSV.GZ, ZIP).

No Supabase account, PostgreSQL connection, or browser upload needed.
Run: py scripts/bulk_import_sqlite.py --dataset MODIS_SP "C:\\FIRMS\\modis.csv"
Repeated --dataset SOURCE PATH flags are supported.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.archive import INSERT_DETECTIONS, discover_archives, iter_archive_members, sha256_file
from backend.core import SOURCES, connect, iter_firms_csv


def import_member(db, stream, source: str, filename: str, digest: str, *, batch_size: int = 2000, progress=None):
    """Commit in bounded batches; uniquely fingerprinted detections make reruns safe."""
    existing = db.execute('SELECT id FROM archive_manifests WHERE source=? AND sha256=? LIMIT 1',
                          (source, digest)).fetchone()
    if existing:
        return {'source': source, 'filename': filename, 'status': 'already_completed', 'rows_added': 0}
    stats = {'rows_seen': 0, 'rows_accepted': 0, 'rows_invalid': 0,
             'first_date': None, 'last_date': None}
    batch = []
    added = 0

    def flush():
        nonlocal added
        if not batch:
            return
        with db:
            cur = db.executemany(INSERT_DETECTIONS, batch)
            added += max(cur.rowcount, 0)
        batch.clear()
        if progress:
            progress(stats, added)

    for normalized in iter_firms_csv(stream, source, stats):
        batch.append(normalized)
        if len(batch) >= batch_size:
            flush()
    flush()
    if stats['rows_seen'] and not stats['rows_accepted']:
        raise ValueError(f'No valid detections in {filename}; check the selected source')
    with db:
        db.execute('''INSERT INTO archive_manifests
            (source, filename, sha256, rows_seen, rows_invalid, rows_accepted, rows_added, first_date, last_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (source, filename[:512], digest, stats['rows_seen'], stats['rows_invalid'],
             stats['rows_accepted'], added, stats['first_date'], stats['last_date']))
    return {**stats, 'source': source, 'filename': filename, 'status': 'completed',
            'rows_added': added, 'coverage_claim': 'positive_detections_only'}


def import_path(db, path: Path, source: str, *, batch_size: int = 2000, progress=None):
    results = []
    for archive_path in discover_archives(path):
        archive_hash = sha256_file(archive_path)
        for member_name, binary_stream in iter_archive_members(archive_path):
            digest = hashlib.sha256((archive_hash + '|' + member_name).encode()).hexdigest()
            display = str(archive_path.resolve()) + '!' + member_name
            with io.TextIOWrapper(binary_stream, encoding='utf-8-sig', newline='') as stream:
                results.append(import_member(db, stream, source, display, digest,
                                             batch_size=batch_size, progress=progress))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dataset', nargs=2, metavar=('SOURCE', 'PATH'), action='append', required=True)
    parser.add_argument('--db', type=Path, default=ROOT / 'data' / 'emberlens.sqlite')
    parser.add_argument('--batch-size', type=int, default=2000)
    parser.add_argument('--no-progress', action='store_true')
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 10000:
        parser.error('--batch-size must be 1..10000')
    for source, path in args.dataset:
        if source not in SOURCES:
            parser.error(f'Unsupported source {source}; use one of: {", ".join(sorted(SOURCES))}')
        if not Path(path).exists():
            parser.error(f'Input not found: {path}')
    start = time.monotonic()
    results = []
    with connect(args.db) as db:
        for source, path in args.dataset:
            print(f'Importing {source}: {path}', flush=True)
            last = [0]
            def report(stats, added):
                if not args.no_progress and stats['rows_seen'] - last[0] >= 50000:
                    print(f'  {stats["rows_seen"]:,} scanned; {added:,} new; {stats["rows_invalid"]:,} invalid', flush=True)
                    last[0] = stats['rows_seen']
            for result in import_path(db, Path(path), source, batch_size=args.batch_size, progress=report):
                results.append(result)
                print(f'  {result["status"]}: {result["filename"]} ({result["rows_added"]:,} new)', flush=True)
        counts = [dict(row) for row in db.execute(
            'SELECT source, COUNT(*) AS detections FROM detections WHERE simulated=0 GROUP BY source ORDER BY source')]
    print(json.dumps({'database': str(args.db.resolve()), 'seconds': round(time.monotonic()-start,2),
                      'rows_added': sum(x['rows_added'] for x in results), 'files': len(results),
                      'detections_by_source': counts, 'results': results,
                      'note': 'Hotspot detections do not establish complete satellite observation coverage.'}, indent=2))


if __name__ == '__main__':
    main()
