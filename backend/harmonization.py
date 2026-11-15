"""Stage 3: defensible *comparison*, not a universal sensor conversion.

Reference quantity: distinct ~10 km grid cells containing at least one FIRMS active
fire detection within a UTC ISO week. MODIS-SP and exactly one selected VIIRS standard-processing platform at a time (Suomi-NPP, NOAA-20, or NOAA-21).

Real-data validation requires that every calendar day in both series belongs to a
successful, REGION-SPECIFIC FIRMS API retrieval. This verifies data retrieval, NOT
complete acquisition, absence of cloud, or truth about fires on the ground.

Chronological holdout and a training-only multiplicative baseline prevent a model
from being declared superior on training error alone. No model is promoted if it
fails held-out evaluation. No predictions are made outside observed week pairs.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import math
import statistics

from backend.core import REGIONS, region_where

METHOD_VERSION = 'log1p-ols-chronological-v1'
ARCHIVE_METHOD_VERSION = 'median-ratio-positive-week-v1'
VIIRS_SP_SOURCES = ('VIIRS_SNPP_SP', 'VIIRS_NOAA20_SP', 'VIIRS_NOAA21_SP')
SP_SOURCES = ('MODIS_SP',) + VIIRS_SP_SOURCES
MIN_PAIRS = 26
MIN_TRAIN = 18
MIN_TEST = 6


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def candidate_weeks(start: date, end: date):
    first = monday(start)
    if first < start:
        first += timedelta(days=7)
    while first + timedelta(days=6) <= end:
        yield first
        first += timedelta(days=7)


def retrieved_days(db, region: str, source: str, start: date, end: date) -> set[date]:
    rows = db.execute('''SELECT start_date,end_date FROM query_windows
                         WHERE region=? AND source=? AND status='retrieved'
                         AND end_date>=? AND start_date<=?''',
                      (region, source, start.isoformat(), end.isoformat()))
    days = set()
    for row in rows:
        day = max(start, date.fromisoformat(row['start_date']))
        finish = min(end, date.fromisoformat(row['end_date']))
        while day <= finish:
            days.add(day)
            day += timedelta(days=1)
    return days


def spatial_weekly_counts(db, region: str, start: date, end: date, mode: int, grid_km: float, viirs_source: str = 'VIIRS_SNPP_SP'):
    """SQLite aggregation keeps millions of FIRMS rows out of Python memory.

    Regional equirectangular 10 km approximation, *not* exact equal-area cells.
    Distinct cells remove repeat detections in the same cell/week only; one
    active cell is neither one fire nor a unit of burned area.
    """
    bounds = REGIONS[region]['bbox']
    west, south, east, north = bounds
    lat_step = grid_km / 111.32
    centre_lat = (south + north) / 2
    lon_step = grid_km / (111.32 * math.cos(math.radians(centre_lat)))
    where, params = region_where(region)
    if getattr(db, 'is_postgres', False):
        sql = f'''SELECT source, week_start, COUNT(*) AS detections,
                   COUNT(DISTINCT (lat_bin,lon_bin)) AS occupied_cells
             FROM (
               SELECT source,
                 to_char(date_trunc('week', acq_date::date), 'YYYY-MM-DD') AS week_start,
                 trunc((latitude-?)/?)::bigint AS lat_bin,
                 trunc((longitude-?)/?)::bigint AS lon_bin
               FROM detections
               WHERE {where} AND acq_date BETWEEN ? AND ? AND simulated=?
                     AND source IN ('MODIS_SP', ?)
             ) AS binned GROUP BY source,week_start ORDER BY week_start,source'''
    else:
        sql = f'''SELECT source, week_start, COUNT(*) AS detections,
                   COUNT(DISTINCT printf('%d:%d',lat_bin,lon_bin)) AS occupied_cells
             FROM (
               SELECT source,
                 date(acq_date, '-' || ((CAST(strftime('%w',acq_date) AS INTEGER)+6)%7) || ' days') AS week_start,
                 CAST((latitude-?)/? AS INTEGER) AS lat_bin,
                 CAST((longitude-?)/? AS INTEGER) AS lon_bin
               FROM detections
               WHERE {where} AND acq_date BETWEEN ? AND ? AND simulated=?
                     AND source IN ('MODIS_SP', ?)
             ) GROUP BY source,week_start ORDER BY week_start,source'''
    out = {}
    for row in db.execute(sql, [south, lat_step, west, lon_step, *params,
                                start.isoformat(), end.isoformat(), mode, viirs_source]):
        out[(row['week_start'], row['source'])] = {
            'occupied_cells': row['occupied_cells'], 'detections': row['detections']}
    return out


def fit_loglinear(train: list[dict]):
    """Fit log(1+VIIRS cells) ~ a + b*log(1+MODIS cells) on train only."""
    xs = [math.log1p(r['modis_cells']) for r in train]
    ys = [math.log1p(r['viirs_cells']) for r in train]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    denom = sum((x-mx)**2 for x in xs)
    if denom < 1e-8:
        return None
    slope = sum((x-mx)*(y-my) for x,y in zip(xs,ys))/denom
    if not math.isfinite(slope) or slope <= 0:
        return None
    intercept = my - slope*mx
    if not math.isfinite(intercept):
        return None
    return {'intercept': intercept, 'slope': slope}


def estimate(model, modis_cells):
    # Upper bound prevents overflow and flags pathological fits as poor performance.
    value = model['intercept'] + model['slope']*math.log1p(modis_cells)
    return max(0., math.expm1(min(value, 30.)))


def metrics(rows, predictions):
    ys = [r['viirs_cells'] for r in rows]
    err = [abs(float(pred) - y) for pred,y in zip(predictions,ys)]
    return {'mae': round(statistics.mean(err), 4),
            'wape_pct': round(100*sum(err)/sum(ys), 2) if sum(ys) > 0 else None}


def archive_positive_fallback(response: dict, pairs: list[dict], strict_weeks: int) -> dict:
    """Describe positive-pair alignment when request provenance cannot support validation."""
    ratio = statistics.median(row['viirs_cells'] / row['modis_cells'] for row in pairs)
    predictions = [ratio * row['modis_cells'] for row in pairs]
    fit = metrics(pairs, predictions)
    for row, prediction in zip(pairs, predictions):
        row['archive_scaled_estimate'] = round(prediction, 2)
        row['partition'] = 'archive_positive'
    response.update(
        model_version=ARCHIVE_METHOD_VERSION,
        method='median matched-week VIIRS/MODIS occupied-cell ratio on archive-positive pairs',
        selection_policy=('Both selected SP sources have at least one loaded positive record in the full week. '
                          'Weeks without a positive record in either source are excluded, not treated as zero.'),
        eligible_weeks=len(pairs),
        strict_eligible_weeks=strict_weeks,
        series=pairs,
        calibration={
            'method': 'median VIIRS-to-MODIS occupied-cell ratio',
            'scaling_ratio': round(ratio, 6),
            'archive_positive_paired_weeks': len(pairs),
            'fit_scope': 'all archive-positive matched weeks; no chronological holdout or promotion',
        },
        validation=None,
        archive_fit={
            'mae': fit['mae'], 'wape_pct': fit['wape_pct'],
            'interpretation': 'Descriptive fit on the same positive-pair weeks used to calculate the ratio. It is not held-out validation.',
        },
        status='archive_exploratory',
        message=(f'Only {strict_weeks} retrieval-verified paired weeks are available. Showing an archive-positive '
                 f'median-ratio alignment across {len(pairs)} matched weeks instead. This is descriptive only and '
                 'must not be used as a validated conversion or fire-activity estimate.'),
    )
    return response


def analyze_harmonization(db, region: str, start: str, end: str,
                          mode: int, grid_km: float = 10.0,
                          viirs_source: str = 'VIIRS_SNPP_SP') -> dict:
    if region not in REGIONS or region == 'all':
        raise ValueError('Choose a bounded study region for harmonization')
    a,b = date.fromisoformat(start), date.fromisoformat(end)
    if a > b or (b-a).days > 366*30:
        raise ValueError('Invalid date interval (maximum 30 years)')
    if grid_km < 5 or grid_km > 50:
        raise ValueError('Grid size must be 5–50 km')
    if viirs_source not in VIIRS_SP_SOURCES:
        raise ValueError('Choose a supported VIIRS standard-processing source')
    weeks = list(candidate_weeks(a,b))
    if len(weeks) > 1600:
        raise ValueError('Too many calendar weeks')
    weekly = spatial_weekly_counts(db,region,a,b,mode,grid_km,viirs_source)
    active_sources = ('MODIS_SP', viirs_source)
    queried = {source: retrieved_days(db,region,source,a,b) if not mode else set()
               for source in active_sources}
    pairs, archive_pairs, excluded = [], [], defaultdict(int)
    for w in weeks:
        key = w.isoformat()
        vals = [weekly.get((key,s), {'occupied_cells':0,'detections':0}) for s in active_sources]
        positive_pair = all(value['detections'] > 0 for value in vals)
        record = {'week': key, 'modis_cells': vals[0]['occupied_cells'],
                  'viirs_cells': vals[1]['occupied_cells'],
                  'modis_detections': vals[0]['detections'],
                  'viirs_detections': vals[1]['detections']}
        if positive_pair:
            archive_pairs.append(record)
        if mode:
            # Practice mode is *positive-only*, no implied zero for missing weeks.
            eligible = positive_pair
        else:
            # Strict mutual retrieval eligibility. Days absent from a positive-only
            # archive never become implicit zero-detection days.
            eligible = all(all(w+timedelta(days=k) in queried[s] for k in range(7))
                           for s in active_sources)
        if not eligible:
            excluded['unverified_retrieval' if not mode else 'not_positive_in_both'] += 1
            continue
        pairs.append(record)

    response = {
        'region': region, 'start': start, 'end': end,
        'data_mode': 'simulated' if mode else 'real',
        'model_version': METHOD_VERSION,
        'method': f'log1p OLS calibrated to {viirs_source} standard-processing 10 km weekly occupied-cell counts',
        'grid_km': grid_km,
        'grid_geometry': 'regional equirectangular approximation; not an equal-area geodesic grid',
        'source_policy': f'MODIS_SP and {viirs_source} only; NRT excluded and VIIRS platforms are not blended',
        'selection_policy': (f'Simulated positive-positive paired weeks only: practice, not scientific validation; target {viirs_source}.'
                             if mode else f'Every day in each paired week must have a completed region-specific API retrieval for MODIS_SP and {viirs_source}.'),
        'coverage_caveat': 'Successful API retrieval does not prove cloud-free satellite observation or a complete fire census. Occupied cells are not unique fires or burned area. VIIRS platforms are compared one at a time because their overpass schedules differ.',
        'weeks_in_range': len(weeks), 'eligible_weeks':len(pairs),
        'excluded_weeks':dict(excluded),
        'series':pairs,
        'calibration':None, 'validation':None,
    }
    if not mode and len(pairs) < MIN_PAIRS and archive_pairs:
        return archive_positive_fallback(response, archive_pairs, len(pairs))
    if len(pairs) < MIN_PAIRS:
        response.update(status='insufficient_pairs', message=(
            f'Only {len(pairs)} of {len(weeks)} whole weeks are eligible. '
            f'At least {MIN_PAIRS} paired weeks are required for a chronological holdout. '
            'Backfill BOTH SP sources for the same region/date windows. '
            'Archive CSVs alone do not prove missing days were queried.'))
        return response
    holdout = max(MIN_TEST, math.ceil(len(pairs)*0.25))
    train, test = pairs[:-holdout],pairs[-holdout:]
    if len(train) < MIN_TRAIN:
        response.update(status='insufficient_pairs',message='Insufficient independent training weeks.')
        return response
    if len({x['modis_cells'] for x in train}) < 3:
        response.update(status='unidentifiable', message='Too little MODIS variability to estimate a stable calibration.')
        return response
    model = fit_loglinear(train)
    if model is None:
        response.update(status='unidentifiable', message='Positive, identifiable cross-sensor trend not established in training weeks.')
        return response
    xs = [float(r['modis_cells']) for r in train]
    ys = [float(r['viirs_cells']) for r in train]
    # The baseline must learn *only* on training data, just like the proposed model.
    divisor = sum(x*x for x in xs)
    factor = sum(x*y for x,y in zip(xs,ys))/divisor if divisor else 0.
    pred = [estimate(model,r['modis_cells']) for r in test]
    baseline_pred = [factor*r['modis_cells'] for r in test]
    eval_cal = metrics(test,pred)
    eval_base = metrics(test,baseline_pred)
    eval_raw = metrics(test,[r['modis_cells'] for r in test])
    # Improvement against an actual fitted baseline, not only raw counts.
    improvement = (100*(eval_base['mae']-eval_cal['mae'])/eval_base['mae']
                   if eval_base['mae'] > 0 else None)
    accepted = improvement is not None and improvement >= 2.0
    for row,p,bp in zip(test,pred,baseline_pred):
        row['calibrated_estimate'] = round(p,2)
        row['scaled_baseline_estimate'] = round(bp,2)
        row['partition']='holdout'
    for row in train: row['partition']='training'
    response['calibration']={
        'intercept':round(model['intercept'],6), 'slope':round(model['slope'],6),
        'baseline_scale':round(factor,6),
        'training_weeks':len(train), 'holdout_weeks':len(test),
        'training_end':train[-1]['week'], 'holdout_start':test[0]['week']}
    response['validation']={
        'candidate':eval_cal, 'scaled_baseline':eval_base, 'raw_identity':eval_raw,
        'mae_improvement_vs_scaled_pct':round(improvement,2) if improvement is not None else None,
        'criterion':'candidate holdout MAE must improve over training-fitted multiplicative baseline by >=2%',
        'promoted':accepted,
        'interpretation':'Held-out agreement with VIIRS detections; NOT independent verification of actual fires.'}
    if mode:
        response.update(status='practice_only',message='Simulated data calibration demonstrated, NOT scientific or NASA validation. Do not use estimates operationally.')
    elif accepted:
        response.update(status='validated_on_holdout',message='Candidate improved on held-out paired retrieval weeks. This does NOT establish ground-truth accuracy, cross-region transferability, or full acquisition coverage.')
    else:
        response.update(status='rejected_on_holdout', message='Candidate did not outperform the fitted baseline on held-out data. Do not substitute calibrated estimates for raw observations.')
    return response
