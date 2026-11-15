"""Create unmistakably SIMULATED interface-testing records (never NASA observations)."""
from __future__ import annotations
import csv
import io
import os
import random
from datetime import date,timedelta
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from backend.core import connect,import_rows,parse_csv
from backend.postgres import connect_postgres, initialize_postgres_schema

DB = os.getenv('EMBERLENS_DB',str(Path(__file__).resolve().parent.parent/'data'/'emberlens.sqlite'))
SUPABASE_DB_URL = (os.getenv('SUPABASE_DB_URL') or os.getenv('DATABASE_URL') or '').strip()


def open_target():
    if SUPABASE_DB_URL:
        db = connect_postgres(SUPABASE_DB_URL)
        initialize_postgres_schema(db)
        return db
    return connect(DB)


def main():
    rng=random.Random(20261029)
    if not SUPABASE_DB_URL:
        Path(DB).parent.mkdir(parents=True,exist_ok=True)
    fields=['latitude','longitude','acq_date','acq_time','satellite','instrument','confidence','frp','version']
    counts={'MODIS_SP':0,'VIIRS_SNPP_SP':0}
    seasonal=[1,1,2,3,2,2,1,2,3,7,10,3]
    for source in counts:
        output=io.StringIO()
        writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
        for year in range(2020,2026):
            for month in range(1,13):
                count=int(seasonal[month-1]*(7 if source=='MODIS_SP' else 11)*(0.8+0.1*(year-2020)))
                if year==2023 and month==11: count=int(count*2.2)
                if year==2021 and month==10: count=int(count*1.7)
                for _ in range(count):
                    day=rng.randrange(1,29)
                    writer.writerow({'latitude':round(rng.uniform(28.2,32.0),6),
                        'longitude':round(rng.uniform(74.0,77.3),6),'acq_date':date(year,month,day).isoformat(),
                        'acq_time':f'{rng.randrange(0,24):02d}{rng.randrange(0,60):02d}',
                        'satellite':'Terra' if source=='MODIS_SP' else 'N',
                        'instrument':'MODIS' if source=='MODIS_SP' else 'VIIRS',
                        'confidence':'80' if source=='MODIS_SP' else 'n',
                        'frp':round(rng.uniform(2,80),2),'version':'DEMO-SIMULATED'})
        rows,stats=parse_csv(output.getvalue(),source,simulated=True)
        coverage=[]
        for year in range(2020,2026):
            day=date(year,1,1)
            until=date(year+1,1,1)
            while day<until:
                coverage.append(day.isoformat());day+=timedelta(days=1)
        with open_target() as db:
            result=import_rows(db,rows,source,'SYNTHETIC DEMO - NOT NASA.csv',simulated=True,coverage_dates=coverage,stats=stats)
        print(source,result['rows_added'],'new SIMULATED observations')
    target = 'Supabase PostgreSQL' if SUPABASE_DB_URL else str(DB)
    print(f'Seeded {target}. NEVER present these points as NASA data.')

if __name__=='__main__':main()
