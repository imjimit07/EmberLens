"""Small repeatable SQLite query benchmark for the active study region.

This reports timings on the user's database; it does not manufacture a benchmark
claim for an empty or simulated dataset.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.core import REGIONS, connect  # noqa: E402
DB_PATH = os.getenv("EMBERLENS_DB", str(ROOT / "data" / "emberlens.sqlite"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--region', choices=[x for x in REGIONS if x != 'all'], default='punjab')
    parser.add_argument('--start', default='2020-01-01')
    parser.add_argument('--end', default=date.today().isoformat())
    parser.add_argument('--runs', type=int, default=7)
    args = parser.parse_args()
    if args.runs < 1 or args.runs > 100:
        parser.error('--runs must be between 1 and 100')
    if date.fromisoformat(args.start) > date.fromisoformat(args.end):
        parser.error('--start must not follow --end')
    west, south, east, north = REGIONS[args.region]['bbox']
    sql = '''SELECT sensor, COUNT(*) AS detections, COUNT(DISTINCT acq_date) AS positive_days
             FROM detections WHERE simulated=? AND acq_date BETWEEN ? AND ?
             AND longitude BETWEEN ? AND ? AND latitude BETWEEN ? AND ? GROUP BY sensor'''
    times = []
    with connect(args.db, initialize=False) as db:
        mode = db.execute('SELECT CASE WHEN EXISTS(SELECT 1 FROM detections WHERE simulated=0) THEN 0 ELSE 1 END').fetchone()[0]
        for _ in range(args.runs):
            began = time.perf_counter()
            rows = [dict(x) for x in db.execute(sql, (mode, args.start, args.end, west, east, south, north))]
            times.append((time.perf_counter() - began) * 1000)
        plan = [r[3] for r in db.execute('EXPLAIN QUERY PLAN ' + sql,
                (mode, args.start, args.end, west, east, south, north)).fetchall()]
        total = db.execute('SELECT COUNT(*) FROM detections WHERE simulated=?', (mode,)).fetchone()[0]
        mode_name = 'simulated' if mode and total else ('real' if total else 'empty')
    print(json.dumps({
        'database': str(Path(args.db).resolve()), 'data_mode': mode_name,
        'region': args.region, 'range': [args.start, args.end],
        'dataset_rows': total, 'result': rows, 'runs': args.runs,
        'median_ms': round(statistics.median(times), 3),
        'min_ms': round(min(times), 3), 'max_ms': round(max(times), 3),
        'query_plan': plan,
        'note': 'Local timing only; compare on the same machine, SQLite version, database and filters.'
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
