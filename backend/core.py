"""NASA FIRMS import and basic data quality utilities.

Stage 2 adds auditable archive ingestion and retrieval windows. No cross-sensor calibration.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

# Official FIRMS active-fire Area API source IDs. The SP (standard-processing)
# products are preferred for historical analysis; NRT is supported for recency
# but is never mixed into the harmonization experiment.
SOURCES = {
    "MODIS_SP": "MODIS", "MODIS_NRT": "MODIS",
    "VIIRS_SNPP_SP": "VIIRS", "VIIRS_SNPP_NRT": "VIIRS",
    "VIIRS_NOAA20_SP": "VIIRS", "VIIRS_NOAA20_NRT": "VIIRS",
    "VIIRS_NOAA21_SP": "VIIRS", "VIIRS_NOAA21_NRT": "VIIRS",
}
VIIRS_SATELLITES = {
    "VIIRS_SNPP_SP": {"N", "SNPP", "SUOMI NPP", "SUOMI-NPP", "S-NPP"},
    "VIIRS_SNPP_NRT": {"N", "SNPP", "SUOMI NPP", "SUOMI-NPP", "S-NPP"},
    "VIIRS_NOAA20_SP": {"N20", "NOAA20", "NOAA-20"},
    "VIIRS_NOAA20_NRT": {"N20", "NOAA20", "NOAA-20"},
    "VIIRS_NOAA21_SP": {"N21", "NOAA21", "NOAA-21"},
    "VIIRS_NOAA21_NRT": {"N21", "NOAA21", "NOAA-21"},
}
SOURCE_LABELS = {
    "MODIS_SP": "MODIS · Standard processing",
    "MODIS_NRT": "MODIS · Near-real-time",
    "VIIRS_SNPP_SP": "VIIRS Suomi-NPP · Standard processing",
    "VIIRS_SNPP_NRT": "VIIRS Suomi-NPP · Near-real-time",
    "VIIRS_NOAA20_SP": "VIIRS NOAA-20 · Standard processing",
    "VIIRS_NOAA20_NRT": "VIIRS NOAA-20 · Near-real-time",
    "VIIRS_NOAA21_SP": "VIIRS NOAA-21 · Standard processing",
    "VIIRS_NOAA21_NRT": "VIIRS NOAA-21 · Near-real-time",
}
REGIONS = {
    "punjab": {"name": "Punjab & Haryana", "bbox": [73.7, 27.5, 77.8, 32.6], "center": [75.7, 30.1]},
    "california": {"name": "California", "bbox": [-124.5, 32.3, -114.0, 42.1], "center": [-119.5, 37.2]},
    "amazon": {"name": "Western Amazon", "bbox": [-73.0, -15.0, -57.0, 0.0], "center": [-65.0, -7.5]},
    "all": {"name": "All imported locations", "bbox": [-180, -90, 180, 90], "center": [0, 0]},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS detections (
    fingerprint TEXT PRIMARY KEY,
    sensor TEXT NOT NULL,
    source TEXT NOT NULL,
    acq_date TEXT NOT NULL,
    acq_time TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    satellite TEXT,
    confidence TEXT,
    frp REAL,
    version TEXT,
    simulated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_detections_filter ON detections(acq_date, sensor, longitude, latitude);
-- Stage 5 workload indexes: the dashboard always filters by active data mode and
-- date; calibration additionally filters by source. Keep the older index for
-- backwards-compatible query plans on imported databases.
CREATE INDEX IF NOT EXISTS idx_detections_mode_date_sensor
    ON detections(simulated, acq_date, sensor);
CREATE INDEX IF NOT EXISTS idx_detections_mode_source_date
    ON detections(simulated, source, acq_date);
CREATE TABLE IF NOT EXISTS coverage (
    source TEXT NOT NULL,
    acq_date TEXT NOT NULL,
    status TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(source, acq_date, simulated)
);
CREATE TABLE IF NOT EXISTS query_windows (
    region TEXT NOT NULL,
    source TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('retrieved')),
    records_returned INTEGER NOT NULL DEFAULT 0,
    retrieved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(region, source, start_date, end_date)
);
CREATE INDEX IF NOT EXISTS idx_windows_region_dates ON query_windows(region, source, start_date, end_date);
CREATE TABLE IF NOT EXISTS product_availability (
    source TEXT PRIMARY KEY,
    min_date TEXT NOT NULL,
    max_date TEXT NOT NULL,
    refreshed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS archive_manifests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    filename TEXT NOT NULL,
    sha256 TEXT,
    rows_seen INTEGER NOT NULL,
    rows_invalid INTEGER NOT NULL,
    rows_accepted INTEGER NOT NULL,
    rows_added INTEGER NOT NULL,
    first_date TEXT,
    last_date TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_manifest_hash ON archive_manifests(source,sha256);
CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    filename TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    rows_seen INTEGER NOT NULL,
    rows_accepted INTEGER NOT NULL,
    rows_added INTEGER NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0
);
"""


def connect(path: str | Path, *, initialize: bool = True) -> sqlite3.Connection:
    """Open a tuned SQLite connection.

    `initialize=False` is intended for request handlers after application startup
    has applied the idempotent schema. CLI tools and tests keep the safe default
    of initializing/migrating schema on connection.
    """
    db_path = str(path)
    if db_path != ':memory:':
        Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=15000')
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA synchronous=NORMAL')
    db.execute('PRAGMA temp_store=MEMORY')
    db.execute('PRAGMA cache_size=-20000')  # 20 MiB page cache per connection
    db.execute('PRAGMA wal_autocheckpoint=1000')
    if db_path != ':memory:':
        mode = db.execute('PRAGMA journal_mode').fetchone()[0]
        if str(mode).lower() != 'wal':
            db.execute('PRAGMA journal_mode=WAL')
    if initialize:
        db.executescript(SCHEMA)
    return db


def iter_firms_csv(csv_file, source: str, stats: dict, *, simulated: bool = False,
                   max_rows: int | None = None):
    """Yield normalized detections without buffering archive files in memory.

    Invalid *rows* are counted. Invalid headers / wrong source are fatal.
    `stats` is mutated while the iterator is consumed.
    """
    if source not in SOURCES:
        raise ValueError("Unsupported FIRMS source")
    reader = csv.DictReader(csv_file)
    if not reader.fieldnames:
        raise ValueError("CSV is empty")
    reader.fieldnames = [str(f).strip().lower().lstrip('\ufeff') for f in reader.fieldnames]
    fields = set(reader.fieldnames)
    if not {"latitude", "longitude", "acq_date", "acq_time"}.issubset(fields):
        raise ValueError("Required FIRMS CSV columns: latitude, longitude, acq_date, acq_time")
    sensor = SOURCES[source]
    # FIRMS uses sensor-specific names in its standard CSV products, but the
    # web-services schema also exposes aliases shared across products. In
    # particular, VIIRS I-4 is named `Brightness` and I-5 is `Brightness_2`
    # in web services; `brightness` alone therefore does NOT identify MODIS.
    # `bright_t31` is also present in some platform-labelled VIIRS exports.
    # Validate sensor identity from instrument/platform values instead.
    # `bright_ti4`/`bright_ti5` and `brightness_2` are VIIRS-specific.
    # The row-level instrument/satellite
    # checks below provide the remaining source identity validation.
    viirs_only_fields = {"bright_ti4", "bright_ti5", "brightness_2"}
    if sensor == "MODIS" and viirs_only_fields & fields:
        raise ValueError("VIIRS-specific brightness fields found in a MODIS import")
    if not ({"instrument", "satellite", "brightness", "bright_t31",
             "bright_ti4", "bright_ti5", "brightness_2"} & fields):
        raise ValueError("Cannot identify sensor: include instrument, satellite, or recognized brightness fields")
    for obj in reader:
        stats['rows_seen'] += 1
        if max_rows is not None and stats['rows_seen'] > max_rows:
            raise ValueError(f"Import limited to {max_rows:,} CSV rows per request")
        try:
            lat, lon = float(obj['latitude']), float(obj['longitude'])
            when = date.fromisoformat(obj['acq_date'].strip())
            if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError("Invalid coordinates")
            hhmm = str(obj['acq_time']).strip().zfill(4)
            if len(hhmm) != 4 or not hhmm.isdigit() or int(hhmm[:2]) > 23 or int(hhmm[2:]) > 59:
                raise ValueError("Invalid HHMM")
            instrument = str(obj.get('instrument') or '').upper().strip()
            if instrument:
                if sensor == "MODIS" and "MODIS" not in instrument:
                    raise ValueError("Unexpected instrument for source")
                if sensor == "VIIRS" and "VIIRS" not in instrument and instrument not in VIIRS_SATELLITES[source]:
                    raise ValueError("Unexpected instrument for source")
            sat = str(obj.get('satellite') or '').strip().upper()
            if sensor == "VIIRS" and sat and sat not in VIIRS_SATELLITES[source]:
                expected = source.split('_')[1]
                raise ValueError(f"Satellite field does not match {expected}")
            confidence = str(obj.get('confidence') or '').strip()
            frp_text = str(obj.get('frp') or '').strip()
            frp = float(frp_text) if frp_text else None
            if frp is not None and (not math.isfinite(frp) or frp < 0):
                raise ValueError("Invalid FRP")
            version = str(obj.get('version') or '').strip()
            key = f"{source}|{when}|{hhmm}|{lat:.6f}|{lon:.6f}|{sat}|{version}|{int(simulated)}"
            fingerprint = hashlib.sha256(key.encode()).hexdigest()
            stats['rows_accepted'] += 1
            if stats.get('first_date') is None or when.isoformat() < stats['first_date']:
                stats['first_date'] = when.isoformat()
            if stats.get('last_date') is None or when.isoformat() > stats['last_date']:
                stats['last_date'] = when.isoformat()
            yield (fingerprint, sensor, source, when.isoformat(), hhmm, lat, lon, sat,
                   confidence, frp, version, int(simulated))
        except (ValueError, TypeError, KeyError, AttributeError):
            stats['rows_invalid'] += 1


def parse_csv(csv_text: str, source: str, *, simulated: bool = False) -> tuple[list[tuple], dict]:
    stats = {"rows_seen": 0, "rows_accepted": 0, "rows_invalid": 0}
    rows = list(iter_firms_csv(io.StringIO(csv_text.lstrip('\ufeff')), source, stats,
                               simulated=simulated, max_rows=200_000))
    if stats['rows_seen'] and not stats['rows_accepted']:
        raise ValueError("No valid detection rows in CSV")
    return rows, stats


def record_query_window(db: sqlite3.Connection, region: str, source: str,
                        start_date: date, end_date: date, rows_returned: int) -> None:
    """A successful HTTP retrieval, not proof of complete satellite observations."""
    if region not in REGIONS or region == 'all' or source not in SOURCES or end_date < start_date:
        raise ValueError('Invalid query window')
    db.execute("""INSERT INTO query_windows(region,source,start_date,end_date,status,records_returned)
                  VALUES (?,?,?,?,'retrieved',?)
                  ON CONFLICT(region,source,start_date,end_date) DO UPDATE SET
                    status='retrieved',records_returned=excluded.records_returned,
                    retrieved_at=CURRENT_TIMESTAMP""",
               (region, source, start_date.isoformat(), end_date.isoformat(), rows_returned))


def import_rows(db: sqlite3.Connection, rows: list[tuple], source: str, filename: str,
                *, simulated: bool = False, coverage_dates: list[str] | None = None,
                stats: dict | None = None, query_window: tuple | None = None) -> dict:
    with db:
        insert_cursor = db.executemany("""INSERT OR IGNORE INTO detections
            (fingerprint,sensor,source,acq_date,acq_time,latitude,longitude,satellite,confidence,frp,version,simulated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        inserted = max(insert_cursor.rowcount, 0)
        for d in coverage_dates or []:
            date.fromisoformat(d)
            db.execute("""INSERT INTO coverage (source,acq_date,status,simulated)
                       VALUES (?,?,'queried',?)
                       ON CONFLICT(source,acq_date,simulated) DO UPDATE SET status=excluded.status""",
                       (source, d, int(simulated)))
        if query_window is not None:
            region, first, last = query_window
            record_query_window(db, region, source, first, last, len(rows))
        st = stats or {"rows_seen": len(rows), "rows_accepted": len(rows), "rows_invalid": 0}
        db.execute("INSERT INTO imports(source,filename,rows_seen,rows_accepted,rows_added,simulated) VALUES(?,?,?,?,?,?)",
                   (source, filename, st['rows_seen'], st['rows_accepted'], inserted, int(simulated)))
    return {**st, "rows_added": inserted, "source": source, "coverage_days": len(coverage_dates or [])}


def month_range(start: str, end: str) -> Iterator[tuple[int,int]]:
    day1, day2 = date.fromisoformat(start), date.fromisoformat(end)
    if day1 > day2:
        raise ValueError("Start date must be before end date")
    y, m = day1.year, day1.month
    while (y, m) <= (day2.year, day2.month):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def region_where(region_id: str) -> tuple[str, list]:
    if region_id not in REGIONS:
        raise ValueError("Unknown region")
    west, south, east, north = REGIONS[region_id]["bbox"]
    return "longitude BETWEEN ? AND ? AND latitude BETWEEN ? AND ?", [west, east, south, north]


def days_inclusive(start: date, days: int) -> list[str]:
    if not 1 <= days <= 5:
        raise ValueError("FIRMS Area API supports only 1–5 days per request")
    return [(start + timedelta(days=i)).isoformat() for i in range(days)]
