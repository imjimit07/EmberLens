"""Scientific integrity + endpoint behavior for the Stage 3 overlap calibrator."""
from __future__ import annotations

from datetime import date, timedelta
import importlib
import os

from fastapi.testclient import TestClient
import pytest

from backend.core import connect, record_query_window
from backend.harmonization import (analyze_harmonization, candidate_weeks,
                                   fit_loglinear, metrics, estimate)

START = date(2024, 1, 1)  # ISO week boundary (Monday)
N = 40
END = START + timedelta(days=7*N - 1)


def rows_for(source, week_index, count, simulated=False):
    """Unique coordinates in distinct approx 10 km cells, with unique fingerprints."""
    day = START + timedelta(days=week_index * 7)
    sensor = 'MODIS' if source.startswith('MODIS') else 'VIIRS'
    return [(f'{source}:{week_index}:{i}:{simulated}', sensor, source,
             day.isoformat(), '0830',
             28.2 + (i // 20) * 0.14, 74.3 + (i % 20) * 0.15,
             'Terra' if sensor == 'MODIS' else 'N', '80' if sensor == 'MODIS' else 'n',
             1.0, '2.0', int(simulated)) for i in range(count)]


def populate(db, *, weeks=N, retrieval=True, simulated=False, pattern='nonlinear', missing=None):
    records=[]
    for i in range(weeks):
        x=3+(i*7)%17
        if pattern == 'nonlinear':
            y=round(1.3*x+0.17*x*x)
        elif pattern == 'shift':
            y=2*x if i < weeks-10 else 8*x
        elif pattern == 'flat':
            x,y=4,6
        else:
            y=2*x
        records.extend(rows_for('MODIS_SP',i,x,simulated))
        records.extend(rows_for('VIIRS_SNPP_SP',i,y,simulated))
    with db:
        db.executemany('''INSERT OR IGNORE INTO detections
          (fingerprint,sensor,source,acq_date,acq_time,latitude,longitude,satellite,
           confidence,frp,version,simulated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',records)
        if retrieval:
            for i in range(weeks):
                w = START + timedelta(days=7*i)
                for s in ('MODIS_SP','VIIRS_SNPP_SP'):
                    # Actual API windows are at most five days.
                    record_query_window(db,'punjab',s,w,w+timedelta(days=4),0)
                    if (i,s) != missing:
                        record_query_window(db,'punjab',s,w+timedelta(days=5),w+timedelta(days=6),0)


def check(db, mode=0, region='punjab', weeks=N):
    return analyze_harmonization(db,region,START.isoformat(),
                                 (START+timedelta(days=7*weeks-1)).isoformat(),mode)


def test_archive_positive_only_is_not_sufficient_for_real_calibration(tmp_path):
    with connect(tmp_path/'db.sqlite') as db:
        populate(db,retrieval=False)
        r=check(db)
    assert r['status']=='insufficient_pairs'
    assert r['eligible_weeks']==0
    assert r['excluded_weeks']['unverified_retrieval']==N
    assert r['validation'] is None


def test_chronological_holdout_metrics_and_paired_grid(tmp_path):
    with connect(tmp_path/'db.sqlite') as db:
        populate(db)
        r=check(db)
    assert r['eligible_weeks']==N
    assert r['calibration']['training_weeks']+r['calibration']['holdout_weeks']==N
    assert r['calibration']['training_end']<r['calibration']['holdout_start']
    assert r['validation']['candidate']['mae']>=0
    assert r['validation']['scaled_baseline']['mae']>=0
    assert all('calibrated_estimate' not in row for row in r['series'] if row['partition']=='training')
    assert all('calibrated_estimate' in row for row in r['series'] if row['partition']=='holdout')
    assert r['status'] in ('validated_on_holdout','rejected_on_holdout')
    assert all(x['modis_cells']>0 and x['viirs_cells']>0 for x in r['series'])
    # This data is a controlled synthetic test, not actual NASA validation.


def test_one_missing_api_day_excludes_exactly_one_week(tmp_path):
    with connect(tmp_path/'db.sqlite') as db:
        populate(db,missing=(4,'VIIRS_SNPP_SP'))
        r=check(db)
    assert r['eligible_weeks']==N-1
    assert r['excluded_weeks']['unverified_retrieval']==1
    assert r['series'][4]['week'] == (START+timedelta(weeks=5)).isoformat()


def test_region_isolation_and_nrt_exclusion(tmp_path):
    with connect(tmp_path/'db.sqlite') as db:
        populate(db)
        before=check(db)
        with db:
            db.executemany('''INSERT INTO detections(fingerprint,sensor,source,acq_date,
              acq_time,latitude,longitude,satellite,confidence,frp,version,simulated)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
              rows_for('MODIS_NRT',3,80)+rows_for('VIIRS_SNPP_NRT',3,100))
        after=check(db)
        other=check(db,region='california')
    assert before['series']==after['series']
    assert other['eligible_weeks']==0


def test_candidate_can_be_rejected(tmp_path):
    with connect(tmp_path/'db.sqlite') as db:
        populate(db,pattern='shift')
        r=check(db)
    assert r['status']=='rejected_on_holdout'
    assert r['validation']['promoted'] is False


def test_flat_modis_predictor_refuses_to_fit(tmp_path):
    with connect(tmp_path/'db.sqlite') as db:
        populate(db,pattern='flat')
        r=check(db)
    assert r['status']=='unidentifiable'
    assert r['calibration'] is None


def test_simulated_data_is_never_called_validated(tmp_path):
    with connect(tmp_path/'db.sqlite') as db:
        populate(db,retrieval=False,simulated=True)
        r=check(db,mode=1)
    assert r['eligible_weeks']==N
    assert r['status']=='practice_only'
    assert r['data_mode']=='simulated'
    assert r['validation'] is not None


def test_endpoint_reports_provenance_and_validation(tmp_path, monkeypatch):
    monkeypatch.setenv('EMBERLENS_DB',str(tmp_path/'db.sqlite'))
    with connect(tmp_path/'db.sqlite') as db:
        populate(db)
    from backend import main
    importlib.reload(main)
    client=TestClient(main.app)
    try:
        resp=client.get('/api/harmonization',params={
            'region':'punjab','start':START.isoformat(),'end':END.isoformat()})
        assert resp.status_code==200,resp.text
        result=resp.json()
        assert result['source_policy'].startswith('MODIS_SP')
        assert result['calibration']['holdout_weeks']>0
        assert client.get('/api/harmonization?region=all').status_code==422
        assert client.get('/api/harmonization?grid_km=1').status_code==422
        assert client.get('/api/health').json()['version']=='0.8.6'
    finally:
        monkeypatch.delenv('EMBERLENS_DB',raising=False)
