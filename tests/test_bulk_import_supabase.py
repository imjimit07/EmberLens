"""No network required: verify the Supabase bulk importer with the SQLite DB-API."""
import io
import sqlite3

from backend.core import SCHEMA
from scripts.bulk_import_supabase import import_member

CSV = '''latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,frp,version\n30.1,75.5,2024-11-01,0123,N,VIIRS,n,5.4,2.0\n30.2,75.6,2024-11-02,0124,N,VIIRS,h,7.4,2.0\n30.3,75.7,2024-11-03,0125,N,VIIRS,h,8.4,2.0\n'''


def test_chunk_and_completed_skip():
    db = sqlite3.connect(':memory:')
    db.executescript(SCHEMA)
    a = import_member(db, io.StringIO(CSV), 'VIIRS_SNPP_SP', 'test.csv', 'digest1', batch_size=2)
    assert a['rows_seen'] == 3
    assert a['rows_added'] == 3
    assert db.execute('select count(*) from detections').fetchone()[0] == 3
    b = import_member(db, io.StringIO(CSV), 'VIIRS_SNPP_SP', 'test.csv', 'digest1', batch_size=2)
    assert b['status'] == 'already_completed'
    c = import_member(db, io.StringIO(CSV), 'VIIRS_SNPP_SP', 'copy.csv', 'digest2', batch_size=2)
    assert c['rows_added'] == 0
    assert db.execute('select count(*) from detections').fetchone()[0] == 3
    assert db.execute('select count(*) from archive_manifests').fetchone()[0] == 2


def test_wrong_product_does_not_create_manifest():
    db = sqlite3.connect(':memory:')
    db.executescript(SCHEMA)
    try:
        import_member(db, io.StringIO(CSV), 'VIIRS_NOAA21_SP', 'wrong.csv', 'wrong', batch_size=2)
    except ValueError as e:
        assert 'No valid detections' in str(e)
    else:
        raise AssertionError('Expected wrong-source validation error')
    assert db.execute('select count(*) from archive_manifests').fetchone()[0] == 0
