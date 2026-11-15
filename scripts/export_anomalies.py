"""Write a retrospective seasonal analysis in JSON, with provenance and caveats.

Example:
python scripts/export_anomalies.py --region punjab --start 2020-01-01 --end 2025-12-31 --sensor VIIRS --output stage4-report.json

Uses the active project SQLite data (or EMBERLENS_DB environment variable).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.core import connect
from backend.anomalies import analyze_anomalies


def main():
    parser = argparse.ArgumentParser(description='Export Stage 4 retrospective analysis (not a fire alert)')
    parser.add_argument('--region', choices=['punjab','california','amazon'], default='punjab')
    parser.add_argument('--start',default='2020-01-01')
    parser.add_argument('--end',default='2025-12-31')
    parser.add_argument('--sensor',choices=['VIIRS','MODIS'],default='VIIRS')
    parser.add_argument('--grid-km',type=float,default=10.0)
    parser.add_argument('--threshold',type=float,default=2.5)
    parser.add_argument('--output',default='-',help='Output JSON file, or - to print to stdout')
    args=parser.parse_args()
    path=Path(os.getenv('EMBERLENS_DB',str(Path(__file__).resolve().parent.parent/'data'/'emberlens.sqlite')))
    path.parent.mkdir(parents=True,exist_ok=True)
    with connect(path) as db:
        mode=0 if db.execute('SELECT COUNT(*) FROM detections WHERE simulated=0').fetchone()[0] else 1
        result=analyze_anomalies(db,args.region,args.start,args.end,mode,args.grid_km,args.sensor,args.threshold)
    output=json.dumps(result,indent=2,allow_nan=False)+'\n'
    if args.output=='-':print(output,end='')
    else:
        dest=Path(args.output)
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(output,encoding='utf-8')
        print(f'Exported {dest}: {result["status"]}, {result["comparable_weeks"]} comparable weeks, {result["elevated_weeks"]} flagged')


if __name__=='__main__':main()
