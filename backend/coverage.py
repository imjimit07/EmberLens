"""Retrieval/provenance diagnostics, never inference of satellite acquisition coverage."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from backend.core import REGIONS, SOURCES, region_where

# Product mission start is distinct from FIRMS API dataset availability.
MISSION_START = {
    'MODIS_SP': date(2000, 11, 1), 'MODIS_NRT': date(2000, 11, 1),
    'VIIRS_SNPP_SP': date(2012, 1, 20), 'VIIRS_SNPP_NRT': date(2012, 1, 20),
    'VIIRS_NOAA20_SP': date(2018, 4, 1), 'VIIRS_NOAA20_NRT': date(2018, 4, 1),
    'VIIRS_NOAA21_SP': date(2023, 3, 1), 'VIIRS_NOAA21_NRT': date(2023, 3, 1),
}


def report_coverage(db, region: str, start: str, end: str, mode: int) -> dict:
    where, box = region_where(region)
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or (last - first).days > 366 * 30:
        raise ValueError('Invalid range (maximum 30 years)')

    detections = db.execute(f"""SELECT source,acq_date,COUNT(*) AS n FROM detections
        WHERE {where} AND acq_date BETWEEN ? AND ? AND simulated=?
        GROUP BY source,acq_date""", [*box, start, end, mode]).fetchall()
    positive = defaultdict(dict)
    for r in detections:
        positive[r['source']][r['acq_date']] = r['n']

    windows = db.execute("""SELECT region,source,start_date,end_date,records_returned,retrieved_at
        FROM query_windows WHERE region=? AND end_date>=? AND start_date<=?
        ORDER BY source,start_date""", (region, start, end)).fetchall() if region != 'all' and not mode else []
    queried = defaultdict(set)
    for r in windows:
        d = max(first, date.fromisoformat(r['start_date']))
        last_d = min(last, date.fromisoformat(r['end_date']))
        while d <= last_d:
            queried[r['source']].add(d.isoformat())
            d += timedelta(days=1)

    availability = {r['source']: dict(r) for r in db.execute('SELECT * FROM product_availability').fetchall()}
    days = []
    d = first
    while d <= last:
        days.append(d)
        d += timedelta(days=1)

    series = []
    for source, sensor in SOURCES.items():
        info = availability.get(source)
        min_date = max(MISSION_START[source], date.fromisoformat(info['min_date'])) if info else MISSION_START[source]
        max_date = date.fromisoformat(info['max_date']) if info else None
        counts = defaultdict(lambda: {'days_in_period': 0, 'outside_product_window': 0,
                                       'positive_days': 0, 'successful_query_days': 0,
                                       'queried_without_detections': 0, 'unverified_days': 0,
                                       'detections': 0})
        summary = {'days_in_period': 0, 'outside_product_window': 0,
                   'positive_days': 0, 'successful_query_days': 0,
                   'queried_without_detections': 0, 'unverified_days': 0, 'detections': 0}
        for d in days:
            k = d.isoformat()
            vals = (counts[k[:7]], summary)
            for bucket in vals:
                bucket['days_in_period'] += 1
                if d < min_date or (max_date is not None and d > max_date):
                    bucket['outside_product_window'] += 1
                    continue
                if k in queried[source]:
                    bucket['successful_query_days'] += 1
                n = positive[source].get(k, 0)
                if n:
                    bucket['positive_days'] += 1
                    bucket['detections'] += n
                elif k in queried[source]:
                    bucket['queried_without_detections'] += 1
                else:
                    bucket['unverified_days'] += 1
        series.append({'source': source, 'sensor': sensor, 'mission_start': MISSION_START[source].isoformat(),
                       'availability': info, 'summary': summary,
                       'months': [{'month': m, **v} for m, v in sorted(counts.items())],
                       'retrieval_windows': sum(1 for w in windows if w['source'] == source),
                       'coverage_kind': 'retrieval_only_not_satellite_observation'})

    # Expose sensor-level overlap risk instead of silently treating NRT and SP
    # as independent fire activity observations when both are imported.
    viirs_overlap = {
        'S-NPP': set(positive['VIIRS_SNPP_SP']) & set(positive['VIIRS_SNPP_NRT']),
        'NOAA-20': set(positive['VIIRS_NOAA20_SP']) & set(positive['VIIRS_NOAA20_NRT']),
        'NOAA-21': set(positive['VIIRS_NOAA21_SP']) & set(positive['VIIRS_NOAA21_NRT']),
    }
    overlaps = {
        'MODIS': len(set(positive['MODIS_SP']) & set(positive['MODIS_NRT'])),
        'VIIRS': len(set().union(*viirs_overlap.values())),
        'VIIRS_S-NPP': len(viirs_overlap['S-NPP']),
        'VIIRS_NOAA-20': len(viirs_overlap['NOAA-20']),
        'VIIRS_NOAA-21': len(viirs_overlap['NOAA-21']),
    }
    manifests = [dict(r) for r in db.execute("""SELECT source,filename,rows_seen,rows_invalid,
        rows_added,first_date,last_date,imported_at FROM archive_manifests
        ORDER BY id DESC LIMIT 30""")]
    return {'region': region, 'start': start, 'end': end,
            'mode': 'simulated' if mode else 'real', 'sources': series,
            'recent_archive_imports': manifests,
            'nrt_sp_overlap_positive_days': overlaps,
            'legend': {
                'positive_days': 'At least one loaded positive hotspot record in the selected region/date.',
                'successful_query_days': 'Days covered by completed API requests; NOT proof of a valid satellite overpass or complete observation.',
                'queried_without_detections': 'API returned no positive records for this region/date. Cannot infer zero fires.',
                'unverified_days': 'No imported positive records and no successful region-specific API retrieval.',
                'outside_product_window': 'Outside known sensor mission start or currently advertised FIRMS API dataset dates. No coverage conclusion.',
            },
            'warning': 'Hotspot records never establish full satellite coverage. Successful requests are retrieval status only; gaps, clouds, outages and observational limitations are not resolved.'}
