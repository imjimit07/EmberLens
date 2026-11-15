"""Stage 2 acceptance tests: archives, provenance, retrieval, and honest coverage."""
from __future__ import annotations

import gzip
import io
import json
import os
import zipfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.archive import import_archive_file, import_archive_stream
from backend.core import connect, parse_csv, import_rows, record_query_window
from backend.coverage import report_coverage

MODIS = """latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,frp,version
30.1,75.1,2025-10-03,0930,Terra,MODIS,82,11,6.1
30.2,75.2,2025-10-04,1020,Aqua,MODIS,73,9,6.1
"""
VIIRS = """latitude,longitude,acq_date,acq_time,satellite,instrument,confidence,frp,version
30.1,75.1,2025-10-03,0930,N,VIIRS,n,11,2.0
"""


def test_gzip_zip_archive_and_idempotency(tmp_path):
    g = tmp_path / 'modis.csv.gz'
    with gzip.open(g, 'wt', encoding='utf8') as out:
        out.write(MODIS)
    z = tmp_path / 'viirs.zip'
    with zipfile.ZipFile(z, 'w') as out:
        out.writestr('2025/viirs.csv', VIIRS)
    with connect(tmp_path / 'db.sqlite') as db:
        first = import_archive_file(db, g, 'MODIS_SP')
        second = import_archive_file(db, g, 'MODIS_SP')
        third = import_archive_file(db, z, 'VIIRS_SNPP_SP')
        assert first[0]['rows_added'] == 2
        assert second[0]['rows_added'] == 0
        assert third[0]['rows_added'] == 1
        assert db.execute('SELECT COUNT(*) FROM detections').fetchone()[0] == 3
        # Archive detection records MUST NOT become supposed days of regional retrieval.
        assert db.execute('SELECT COUNT(*) FROM query_windows').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM archive_manifests').fetchone()[0] == 3
        assert third[0]['first_date'] == '2025-10-03'


def test_bad_archive_rolls_back(tmp_path, monkeypatch):
    import backend.archive as archive
    rows, _ = parse_csv(MODIS, 'MODIS_SP')
    def interrupted(*args, **kwargs):
        yield rows[0]
        raise RuntimeError('disk disconnected')
    monkeypatch.setattr(archive, 'iter_firms_csv', interrupted)
    with connect(tmp_path / 'db.sqlite') as db:
        with pytest.raises(RuntimeError):
            import_archive_stream(db, io.StringIO(MODIS), 'MODIS_SP', 'broken.csv', chunk_size=1)
        assert db.execute('SELECT COUNT(*) FROM detections').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM archive_manifests').fetchone()[0] == 0


def test_archive_wrong_source_fails(tmp_path):
    f = tmp_path / 'file.csv'
    f.write_text(VIIRS)
    with connect(tmp_path / 'db.sqlite') as db:
        with pytest.raises(ValueError):
            import_archive_file(db, f, 'MODIS_SP')
        assert db.execute('SELECT COUNT(*) FROM detections').fetchone()[0] == 0


def test_coverage_does_not_infer_zeros_from_archive(tmp_path):
    with connect(tmp_path / 'db.sqlite') as db:
        rows, st = parse_csv(MODIS, 'MODIS_SP')
        import_rows(db, rows, 'MODIS_SP', 'archive.csv', stats=st)
        audit = report_coverage(db, 'punjab', '2025-10-01', '2025-10-05', 0)
        summary = next(x['summary'] for x in audit['sources'] if x['source'] == 'MODIS_SP')
        assert summary['positive_days'] == 2
        assert summary['unverified_days'] == 3
        assert summary['queried_without_detections'] == 0
        # Retrieval windows must never leak across distinct geographic regions.
        with db:
            record_query_window(db, 'punjab', 'MODIS_SP', date(2025, 10, 1), date(2025, 10, 5), 2)
        pb = next(x['summary'] for x in report_coverage(db, 'punjab', '2025-10-01', '2025-10-05', 0)['sources'] if x['source'] == 'MODIS_SP')
        ca = next(x['summary'] for x in report_coverage(db, 'california', '2025-10-01', '2025-10-05', 0)['sources'] if x['source'] == 'MODIS_SP')
        assert pb['successful_query_days'] == 5
        assert pb['queried_without_detections'] == 3
        assert pb['unverified_days'] == 0
        assert ca['successful_query_days'] == 0
        assert ca['unverified_days'] == 5


def test_mission_and_product_availability_boundaries(tmp_path):
    with connect(tmp_path / 'db.sqlite') as db:
        with db:
            db.execute("INSERT INTO product_availability(source,min_date,max_date) VALUES ('MODIS_SP','2025-10-02','2025-10-04')")
        rep = report_coverage(db, 'punjab', '2025-10-01', '2025-10-05', 0)
        s = next(x['summary'] for x in rep['sources'] if x['source'] == 'MODIS_SP')
        assert s['outside_product_window'] == 2
        assert s['unverified_days'] == 3
        s2 = next(x['summary'] for x in report_coverage(db, 'punjab', '2010-01-01', '2010-01-03', 0)['sources'] if x['source'] == 'VIIRS_SNPP_SP')
        assert s2['outside_product_window'] == 3
        assert s2['unverified_days'] == 0


def test_empty_api_response_valid_but_not_sufficient_coverage(tmp_path):
    header = 'latitude,longitude,acq_date,acq_time,satellite,instrument\n'
    rows, st = parse_csv(header, 'MODIS_SP')
    assert rows == [] and st['rows_seen'] == 0
    with connect(tmp_path / 'db.sqlite') as db:
        import_rows(db, rows, 'MODIS_SP', 'empty_window', stats=st,
                    query_window=('punjab', date(2025, 10, 1), date(2025, 10, 5)))
        r = report_coverage(db, 'punjab', '2025-10-01', '2025-10-05', 0)
        m = next(x['summary'] for x in r['sources'] if x['source'] == 'MODIS_SP')
        assert m['successful_query_days'] == 5
        assert m['queried_without_detections'] == 5
        assert m['positive_days'] == 0


def test_api_backfill_resume_and_forced_refetch(tmp_path, monkeypatch):
    os.environ['EMBERLENS_DB'] = str(tmp_path / 'backend.sqlite')
    os.environ['FIRMS_MAP_KEY'] = 'not-a-real-key'
    from backend import main
    import importlib
    importlib.reload(main)
    calls = []
    async def stub(client, key, region, source, first, last):
        calls.append((region, first, last))
        return parse_csv(MODIS, 'MODIS_SP')
    monkeypatch.setattr(main, 'fetch_csv_window', stub)
    client = TestClient(main.app)
    body = {'region': 'punjab', 'source': 'MODIS_SP', 'start_date': '2025-10-01', 'end_date': '2025-10-12'}
    try:
        a = client.post('/api/import/firms-range', json=body)
        assert a.status_code == 200, a.text
        assert len(a.json()['windows_imported']) == 3
        assert a.json()['total_rows_added'] == 2
        assert [x[2] for x in calls] == [date(2025,10,5),date(2025,10,10),date(2025,10,12)]
        b = client.post('/api/import/firms-range', json=body)
        assert b.status_code == 200
        assert len(b.json()['windows_skipped']) == 3
        assert len(calls) == 3
        c = client.post('/api/import/firms-range', json={**body, 'force': True})
        assert c.status_code == 200
        assert c.json()['total_rows_added'] == 0
        assert len(calls) == 6
        cover=client.get('/api/coverage?region=punjab&start=2025-10-01&end=2025-10-12').json()
        modis=next(x['summary'] for x in cover['sources'] if x['source']=='MODIS_SP')
        assert modis['successful_query_days']==12
        assert modis['positive_days']==2
        assert modis['queried_without_detections']==10
        assert client.post('/api/import/firms-range',json={**body,'end_date':'2026-12-31'}).status_code==422
        assert client.get('/').status_code==200
    finally:
        os.environ.pop('EMBERLENS_DB',None)
        os.environ.pop('FIRMS_MAP_KEY',None)


def test_failed_backfill_not_recorded(tmp_path, monkeypatch):
    os.environ['EMBERLENS_DB']=str(tmp_path/'backend.sqlite')
    os.environ['FIRMS_MAP_KEY']='private-key'
    from backend import main
    import importlib
    importlib.reload(main)
    async def fail(*args, **kwargs):
        raise ValueError('FIRMS HTTP 503')
    monkeypatch.setattr(main, 'fetch_csv_window', fail)
    try:
        r=TestClient(main.app).post('/api/import/firms-range',json={
            'region':'punjab','source':'MODIS_SP','start_date':'2025-10-01','end_date':'2025-10-06'})
        assert r.status_code==502
        with connect(tmp_path/'backend.sqlite') as db:
            assert db.execute('SELECT COUNT(*) FROM query_windows').fetchone()[0]==0
    finally:
        os.environ.pop('EMBERLENS_DB',None)
        os.environ.pop('FIRMS_MAP_KEY',None)


def test_availability_without_key(tmp_path):
    os.environ['EMBERLENS_DB']=str(tmp_path/'backend.sqlite')
    os.environ.pop('FIRMS_MAP_KEY',None)
    from backend import main
    import importlib
    importlib.reload(main)
    r=TestClient(main.app).post('/api/availability/refresh')
    assert r.status_code==400
    os.environ.pop('EMBERLENS_DB',None)


def test_nrt_sp_overlap_warning_reported(tmp_path):
    with connect(tmp_path/'db.sqlite') as db:
        for source in ('MODIS_SP','MODIS_NRT'):
            rows, st = parse_csv(MODIS, source)
            import_rows(db, rows, source, 'src.csv', stats=st)
        rep=report_coverage(db,'punjab','2025-10-01','2025-10-05',0)
        assert rep['nrt_sp_overlap_positive_days']['MODIS']==2
        assert rep['nrt_sp_overlap_positive_days']['VIIRS']==0
