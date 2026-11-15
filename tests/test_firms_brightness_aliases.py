"""Regression tests for NASA FIRMS MODIS/VIIRS brightness field aliases."""
import pytest

from backend.core import parse_csv

VIIRS_WEB_SERVICE = """latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,brightness_2,frp,daynight
30.100,75.100,330.0,0.4,0.4,2026-11-03,0930,N20,VIIRS,n,2.0NRT,295.0,12.3,D
"""

VIIRS_STANDARD = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight
30.100,75.100,330.0,0.4,0.4,2026-11-03,0930,N20,VIIRS,n,2.0NRT,295.0,12.3,D
"""

MODIS_STANDARD = """latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight
30.100,75.100,330.0,1.0,1.0,2026-11-03,0930,Terra,MODIS,82,6.1NRT,295.0,12.3,D
"""


def test_viirs_web_services_brightness_aliases_are_accepted():
    rows, stats = parse_csv(VIIRS_WEB_SERVICE, "VIIRS_NOAA20_NRT")
    assert len(rows) == 1
    assert stats["rows_accepted"] == 1
    assert rows[0][1] == "VIIRS"
    assert rows[0][2] == "VIIRS_NOAA20_NRT"
    assert rows[0][7] == "N20"


def test_viirs_standard_bright_ti_fields_remain_accepted():
    rows, stats = parse_csv(VIIRS_STANDARD, "VIIRS_NOAA20_NRT")
    assert len(rows) == 1
    assert stats["rows_accepted"] == 1


def test_modis_brightness_and_bright_t31_are_accepted():
    rows, stats = parse_csv(MODIS_STANDARD, "MODIS_NRT")
    assert len(rows) == 1
    assert stats["rows_accepted"] == 1
    assert rows[0][1] == "MODIS"


def test_modis_rows_are_rejected_for_viirs_even_with_shared_brightness_headers():
    with pytest.raises(ValueError, match="No valid detection rows"):
        parse_csv(MODIS_STANDARD, "VIIRS_NOAA20_NRT")

def test_snpp_platform_in_instrument_and_bright_t31_accepted():
    csv_text = MODIS_STANDARD.replace("Terra,MODIS,82", "SNPP,SNPP,n")
    rows, stats = parse_csv(csv_text, "VIIRS_SNPP_NRT")
    assert len(rows) == 1
    assert stats["rows_invalid"] == 0


def test_viirs_brightness_2_is_rejected_for_modis():
    with pytest.raises(ValueError, match="VIIRS-specific brightness fields"):
        parse_csv(VIIRS_WEB_SERVICE, "MODIS_NRT")
