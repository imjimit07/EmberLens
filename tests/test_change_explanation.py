from contextlib import closing

import pytest

from backend.change_explanation import analyze_change_explanation
from backend.core import connect


def add_detection(db, fingerprint, source, day, offset):
    sensor = 'MODIS' if source.startswith('MODIS') else 'VIIRS'
    db.execute("""INSERT INTO detections
        (fingerprint,sensor,source,acq_date,acq_time,latitude,longitude,simulated)
        VALUES (?,?,?,?,?,?,?,0)""",
        (fingerprint, sensor, source, day, '1200', 30.0 + offset / 1000, 75.0 + offset / 1000))


def test_source_matching_exposes_a_changed_source_mix():
    with closing(connect(':memory:')) as db:
        for index in range(2):
            add_detection(db, f'before-{index}', 'MODIS_SP', '2020-01-03', index)
        for index in range(3):
            add_detection(db, f'after-modis-{index}', 'MODIS_SP', '2021-01-03', index)
        for index in range(4):
            add_detection(db, f'after-viirs-{index}', 'VIIRS_SNPP_SP', '2021-01-03', 10 + index)
        db.execute("""INSERT INTO query_windows
            (region,source,start_date,end_date,status,records_returned)
            VALUES ('punjab','MODIS_SP','2020-01-03','2020-01-03','retrieved',2)""")
        db.execute("""INSERT INTO query_windows
            (region,source,start_date,end_date,status,records_returned)
            VALUES ('punjab','MODIS_SP','2021-01-03','2021-01-03','retrieved',3)""")
        result = analyze_change_explanation(
            db, 'punjab', '2020-01-01', '2020-01-07', '2021-01-01', '2021-01-07', 0)

    assert result['raw_loaded_detection_change']['delta'] == 5
    assert result['matched_positive_source_change']['delta'] == 1
    assert result['source_evidence']['matching_positive_sources'] == ['MODIS_SP']
    assert result['source_evidence']['sources_only_after'] == ['VIIRS_SNPP_SP']
    assert result['source_evidence']['direction_survives_matched_sources'] is True
    assert result['assessment']['state'] == 'source_mix_changed'
    assert result['source_rows'][0]['before']['retrieved_request_days'] == 1


def test_source_matching_rejects_overlapping_periods():
    with closing(connect(':memory:')) as db:
        with pytest.raises(ValueError, match='must end before'):
            analyze_change_explanation(
                db, 'punjab', '2020-01-01', '2020-01-07', '2020-01-07', '2020-01-12', 0)
