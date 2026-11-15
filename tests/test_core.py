from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import pytest
from backend.core import (connect,parse_csv,import_rows,days_inclusive,month_range)

SAMPLE="""latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,frp,version
30.1,75.1,2025-11-03,930,Terra,MODIS,80,13.3,6.1
30.2,75.2,2025-11-03,0945,Aqua,MODIS,70,10.0,6.1
"""

def test_import_deduplicates():
    rows,stats=parse_csv(SAMPLE,'MODIS_SP')
    assert stats['rows_accepted']==2
    with TemporaryDirectory() as folder:
        with connect(Path(folder)/'test.sqlite') as db:
            a=import_rows(db,rows,'MODIS_SP','test.csv',stats=stats)
            b=import_rows(db,rows,'MODIS_SP','test.csv',stats=stats)
            assert a['rows_added']==2 and b['rows_added']==0
            assert db.execute('SELECT COUNT(*) FROM detections').fetchone()[0]==2


def test_sensor_isolation():
    with pytest.raises(ValueError,match='No valid'):
        parse_csv(SAMPLE.replace('MODIS','VIIRS'),'MODIS_SP')
    with pytest.raises(ValueError,match='No valid'):
        parse_csv(SAMPLE,'VIIRS_SNPP_SP')


def test_invalid_coordinates_skipped():
    rows,stats=parse_csv(SAMPLE.replace('30.1,75.1','130.1,75.1'),'MODIS_SP')
    assert len(rows)==1 and stats['rows_invalid']==1


def test_days_limit():
    assert len(days_inclusive(date(2025,1,1),5))==5
    with pytest.raises(ValueError):days_inclusive(date(2025,1,1),6)


def test_month_range():
    assert list(month_range('2024-12-01','2025-02-12'))==[(2024,12),(2025,1),(2025,2)]
