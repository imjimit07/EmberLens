"""Export the Stage 3 scientific comparison as reproducible JSON.

Usage: python scripts/export_validation.py --region punjab --start 2020-01-01 --end 2025-12-31 --output report.json
This exports ALL paired weeks and metrics; data is never claimed to be ground truth.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.core import connect
from backend.harmonization import analyze_harmonization


def main():
    ap=argparse.ArgumentParser(description='Export evidence from a held-out MODIS/VIIRS comparison')
    ap.add_argument('--region',choices=['punjab','california','amazon'],default='punjab')
    ap.add_argument('--start',default='2020-01-01')
    ap.add_argument('--end',default='2026-10-29')
    ap.add_argument('--grid-km',type=float,default=10.0)
    ap.add_argument('--output',default='-',help='JSON output path, or - for stdout')
    args=ap.parse_args()
    db_path=os.getenv('EMBERLENS_DB',str(Path(__file__).resolve().parent.parent/'data'/'emberlens.sqlite'))
    Path(db_path).parent.mkdir(parents=True,exist_ok=True)
    with connect(db_path) as db:
        real=db.execute('SELECT COUNT(*) FROM detections WHERE simulated=0').fetchone()[0]
        mode=0 if real else 1
        result=analyze_harmonization(db,args.region,args.start,args.end,mode,args.grid_km)
    out=json.dumps(result,indent=2,allow_nan=False)+'\n'
    if args.output=='-':print(out,end='')
    else:
        dest=Path(args.output)
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(out,encoding='utf-8')
        print(f'Wrote {dest} · {result["status"]} · {result["eligible_weeks"]} eligible paired weeks')

if __name__=='__main__':main()
