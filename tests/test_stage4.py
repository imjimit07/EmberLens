"""Stage 4 anomaly engine: provenance, reference leakage, missingness and UI API."""
from datetime import date, timedelta
import importlib

import pytest
from fastapi.testclient import TestClient

from backend.anomalies import (
    analyze_anomalies, compute_seasonal_anomalies, critical_periods,
    quantile, seasonal_distance,
)
from backend.core import connect, record_query_window


def synthetic_pairs():
    rows=[]
    # Six calendar years; identical baseline except for an isolated high 2023 week.
    for year in range(2019, 2025):
        first=date(year, 1, 7)
        first+=timedelta(days=(7-first.weekday())%7)  # next Monday
        for offset in range(44):
            week=first+timedelta(weeks=offset)
            if week.year!=year: break
            value=100 if year==2023 and offset==20 else 10
            rows.append({'week':week.isoformat(), 'viirs_cells':value,'modis_cells':5})
    return rows


def test_historical_reference_excludes_target_year():
    rows=synthetic_pairs()
    result=compute_seasonal_anomalies(rows,'VIIRS')
    out=next(r for r in result['series'] if r['year']==2023 and r['observed_cells']==100)
    assert out['reference_years']>=4
    assert out['baseline_median']==10
    assert out['seasonal_p90']==10
    assert out['elevated']
    assert out['robust_score']>2.5
    assert all(not r['elevated'] for r in result['series'] if r['year']!=2023)


def test_missing_years_not_interpreted_as_zeros():
    rows=[r for r in synthetic_pairs() if date.fromisoformat(r['week']).year in (2021,2022,2023)]
    result=compute_seasonal_anomalies(rows,'VIIRS')
    assert result['comparable_weeks']==0
    assert result['alert_weeks']==[]
    assert all(r['baseline_median'] is None for r in result['series'])


def test_threshold_and_sensor_switch():
    rows=synthetic_pairs()
    regular=compute_seasonal_anomalies(rows,'MODIS')
    assert regular['alert_weeks']==[]
    assert compute_seasonal_anomalies(rows,'VIIRS',threshold=5)['alert_weeks']
    with pytest.raises(ValueError): compute_seasonal_anomalies(rows,'ALL')
    with pytest.raises(ValueError): compute_seasonal_anomalies(rows,'VIIRS',float('nan'))


def test_critical_periods_do_not_bridge_gaps():
    rows=[{'week':d,'elevated':True,'robust_score':3,'observed_cells':40} for d in
          ('2023-04-03','2023-04-10','2023-04-24','2023-05-01')]
    result=critical_periods(rows)
    assert [p['weeks'] for p in result]==[2,2]
    assert sorted(p['start'] for p in result)==['2023-04-03','2023-04-24']


def test_quantiles_and_week_edges():
    assert quantile([0,10,20],.1)==2
    assert seasonal_distance(1,52)==1
    assert seasonal_distance(2,51)==3
    assert seasonal_distance(53,1)==52  # boundary week handled explicitly


def fill(db, *, missing=False):
    """Several REAL-labelled, query-verifiable years of sparse weekly data."""
    records=[]
    for year in range(2019,2025):
        start=date(year,1,7)
        start+=timedelta(days=(7-start.weekday())%7)
        for index in range(9):
            w=start+timedelta(weeks=index)
            for source,sensor,count in [('MODIS_SP','MODIS',3),('VIIRS_SNPP_SP','VIIRS',5)]:
                if missing and year==2022 and index==3 and source=='VIIRS_SNPP_SP':
                    continue
                for i in range(count):
                    records.append((f'S4:{year}:{index}:{source}:{i}',sensor,source,w.isoformat(),
                                    '1030',28.4+i*.15,74.5+i*.2,'N','n',1.,'6.1',0))
                # successful retrieval windows, all seven days of matched week
                record_query_window(db,'punjab',source,w,w+timedelta(days=4),count)
                record_query_window(db,'punjab',source,w+timedelta(days=5),w+timedelta(days=6),count)
    with db:
        db.executemany('''INSERT INTO detections (fingerprint,sensor,source,acq_date,acq_time,
        latitude,longitude,satellite,confidence,frp,version,simulated)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',records)


def test_real_overlap_api_and_serialization(tmp_path, monkeypatch):
    path=str(tmp_path/'db.sqlite')
    monkeypatch.setenv('EMBERLENS_DB',path)
    with connect(path) as db: fill(db,missing=True)
    from backend import main
    importlib.reload(main)
    client=TestClient(main.app)
    try:
        args={'region':'punjab','start':'2019-01-01','end':'2024-12-31'}
        res=client.get('/api/anomalies',params=args)
        assert res.status_code==200,res.text
        data=res.json()
        assert data['data_mode']=='real'
        assert data['status']=='retrospective_comparison'
        assert data['paired_weeks']==53
        assert data['comparable_weeks']>0
        assert data['excluded_weeks']['unverified_retrieval']>0
        assert 'confidence interval' in data['visual_uncertainty']['seasonal_envelope']
        assert data['calibration_promoted'] is False
        assert any(x['baseline_median'] is not None for x in data['series'])
        assert client.get('/api/anomalies',params={**args,'region':'all'}).status_code==422
        assert client.get('/api/anomalies',params={**args,'sensor':'ALL'}).status_code==422
        assert client.get('/api/anomalies',params={**args,'threshold':9}).status_code==422
    finally:
        monkeypatch.delenv('EMBERLENS_DB',raising=False)


def test_simulated_data_not_presented_as_real(tmp_path):
    path=tmp_path/'sample.sqlite'
    with connect(path) as db:
        # Directly exercise practice mode; no NASA acquisition was performed.
        r=analyze_anomalies(db,'punjab','2020-01-01','2025-12-31',1)
    assert r['status']=='insufficient_history'
    assert r['data_mode']=='simulated'
    assert r['elevated_weeks']==0
    assert r['calibration_promoted'] is False
