from datetime import date, timedelta

import pytest

from backend.core import SOURCES, connect, parse_csv
from backend.coverage import report_coverage
from backend.harmonization import analyze_harmonization


VIIRS_ROW = (
    'latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,'
    'instrument,confidence,version,bright_ti5,frp,daynight\n'
    '34.500,-119.500,300.0,0.5,0.5,2025-01-01,0430,N20,VIIRS,n,2.0,280.0,1.2,N\n'
)


def test_firms_current_modis_and_viirs_platform_source_ids_are_supported():
    assert set(SOURCES) == {
        'MODIS_SP', 'MODIS_NRT',
        'VIIRS_SNPP_SP', 'VIIRS_SNPP_NRT',
        'VIIRS_NOAA20_SP', 'VIIRS_NOAA20_NRT',
        'VIIRS_NOAA21_SP', 'VIIRS_NOAA21_NRT',
    }


def test_noaa20_csv_is_accepted_under_matching_source():
    rows, stats = parse_csv(VIIRS_ROW, 'VIIRS_NOAA20_SP')
    assert len(rows) == 1
    assert stats['rows_accepted'] == 1
    assert rows[0][1] == 'VIIRS'
    assert rows[0][2] == 'VIIRS_NOAA20_SP'
    assert rows[0][7] == 'N20'


def test_noaa20_csv_cannot_be_mislabeled_as_noaa21():
    with pytest.raises(ValueError, match='No valid detection rows'):
        parse_csv(VIIRS_ROW.replace(',N20,VIIRS,', ',N21,VIIRS,'), 'VIIRS_NOAA20_SP')


def test_coverage_uses_noaa_platform_mission_starts(tmp_path):
    with connect(tmp_path / 'coverage.sqlite') as db:
        report = report_coverage(db, 'punjab', '2015-01-01', '2015-01-01', 0)
    by_source = {row['source']: row for row in report['sources']}
    assert by_source['VIIRS_SNPP_SP']['summary']['outside_product_window'] == 0
    assert by_source['VIIRS_NOAA20_SP']['summary']['outside_product_window'] == 1
    assert by_source['VIIRS_NOAA21_SP']['summary']['outside_product_window'] == 1


def test_harmonization_accepts_one_selected_viirs_platform(tmp_path):
    with connect(tmp_path / 'harmonization.sqlite') as db:
        result = analyze_harmonization(db, 'punjab', '2020-01-01', '2020-12-31',
                                       0, 10, 'VIIRS_NOAA20_SP')
    assert 'VIIRS_NOAA20_SP' in result['source_policy']
    assert 'platforms are not blended' in result['source_policy']


def test_harmonization_rejects_nrt_or_unknown_platform(tmp_path):
    with connect(tmp_path / 'harmonization.sqlite') as db:
        with pytest.raises(ValueError, match='standard-processing source'):
            analyze_harmonization(db, 'punjab', '2020-01-01', '2020-12-31',
                                  0, 10, 'VIIRS_NOAA20_NRT')


def test_api_accepts_selected_noaa21_platform(tmp_path, monkeypatch):
    monkeypatch.setenv('EMBERLENS_DB', str(tmp_path / 'api.sqlite'))
    from fastapi.testclient import TestClient
    from backend import main
    import importlib
    importlib.reload(main)
    with TestClient(main.app) as client:
        response = client.get('/api/harmonization', params={
            'region': 'punjab', 'start': '2020-01-01', 'end': '2020-12-31',
            'viirs_source': 'VIIRS_NOAA21_SP'
        })
    assert response.status_code == 200
    assert 'VIIRS_NOAA21_SP' in response.json()['source_policy']


def test_anomaly_analysis_uses_selected_viirs_platform(tmp_path):
    from backend.anomalies import analyze_anomalies
    with connect(tmp_path / 'anomaly.sqlite') as db:
        result = analyze_anomalies(db, 'punjab', '2020-01-01', '2020-12-31',
                                   0, 10, 'VIIRS', 2.5, 'VIIRS_NOAA21_SP')
    assert result['viirs_source'] == 'VIIRS_NOAA21_SP'
