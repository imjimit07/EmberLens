from datetime import date, timedelta

import pytest

from backend.core import connect
from backend.anomalies import analyze_anomalies


def test_archive_only_uses_selected_positive_weeks_without_retrievals(tmp_path):
    with connect(str(tmp_path / 'archive.db')) as db:
        records = []
        for year in range(2020, 2025):
            for week_index in range(4):
                # Same seasonal week across five calendar years; one anomalously high week
                monday = date(year, 10, 5)
                monday -= timedelta(days=monday.weekday())
                day = monday + timedelta(weeks=week_index)
                count = 9 if year == 2024 and week_index == 2 else 2
                for i in range(count):
                    records.append((f'{year}-{week_index}-{i}', 'MODIS', 'MODIS_SP', day.isoformat(),
                                    '1200', 29.0 + i*.11, 75.0 + i*.12, 'Terra', 'n', 2., '6.1', 0))
        db.executemany('''INSERT INTO detections (fingerprint,sensor,source,acq_date,acq_time,
             latitude,longitude,satellite,confidence,frp,version,simulated)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', records)
        result = analyze_anomalies(db, 'punjab', '2020-01-01', '2024-12-31', 0,
                                   sensor='MODIS', analysis_mode='archive_positive_only')
        assert result['status'] == 'archive_exploratory'
        assert result['analysis_mode'] == 'archive_positive_only'
        assert result['comparable_weeks'] > 0
        assert result['calibration_promoted'] is False
        assert len(result['series']) == 20
        assert all(r['other_sensor_cells'] is None for r in result['series'])
        assert all(r['sensor_difference_cells'] is None for r in result['series'])
        assert result['excluded_weeks']['no_positive_detection_in_selected_source'] > 0
        assert 'selection' in result['message'].lower() or 'selection' in ' '.join(result['notes']).lower()
        strict = analyze_anomalies(db, 'punjab', '2020-01-01', '2024-12-31', 0,
                                   sensor='MODIS', analysis_mode='strict')
        assert strict['status'] == 'insufficient_history'
        assert strict['paired_weeks'] == 0


def test_archive_mode_rejects_simulated_and_invalid_mode(tmp_path):
    with connect(str(tmp_path / 'archive.db')) as db:
        with pytest.raises(ValueError):
            analyze_anomalies(db, 'punjab', '2020-01-01', '2024-12-31', 1,
                              analysis_mode='archive_positive_only')
        with pytest.raises(ValueError):
            analyze_anomalies(db, 'punjab', '2020-01-01', '2024-12-31', 0,
                              analysis_mode='not-a-mode')
