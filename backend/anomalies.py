"""Stage 4: retrospective seasonal anomaly exploration, never fire prediction.

Uses ONLY the paired, full-week occupied-cell observations admitted by Stage 3.
Each target week is compared to a leave-one-CALENDAR-YEAR-out baseline, with
one seasonal aggregate per reference year to avoid overweighting dense years.
This is a heuristic, NOT a p-value, predictive interval, or confidence level.

The visual envelope describes empirical BETWEEN-YEAR seasonal variation only.
Different sensors' measurements remain separate; their differences are not
an uncertainty/confidence interval around true fire activity.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import math
import statistics

from backend.harmonization import analyze_harmonization, spatial_weekly_counts, candidate_weeks, VIIRS_SP_SOURCES
from backend.core import REGIONS

ANOMALY_VERSION = 'loyo-seasonal-occupied-cells-v1'
MIN_REFERENCE_YEARS = 4
SEASON_HALF_WINDOW_WEEKS = 2


def quantile(values: list[float], fraction: float) -> float:
    """Linear interpolation on sorted empirical values (not a confidence bound)."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError('Cannot calculate quantile from empty values')
    pos = (len(ordered)-1)*fraction
    i = math.floor(pos)
    j = math.ceil(pos)
    return ordered[i] + (ordered[j]-ordered[i])*(pos-i)


def seasonal_distance(a: int, b: int) -> int:
    """ISO week 53 is kept but not wrapped with the regular 52-week calendar."""
    if a == 53 or b == 53:
        return abs(a-b)
    return min(abs(a-b), 52-abs(a-b))


def compute_seasonal_anomalies(pairs: list[dict], sensor: str, threshold: float = 2.5,
                               min_reference_years: int = MIN_REFERENCE_YEARS) -> dict:
    if sensor not in ('VIIRS', 'MODIS'):
        raise ValueError('Choose MODIS or VIIRS')
    if not math.isfinite(threshold) or not 1.5 <= threshold <= 5:
        raise ValueError('Threshold must be 1.5–5')
    key = 'viirs_cells' if sensor == 'VIIRS' else 'modis_cells'
    other = 'modis_cells' if sensor == 'VIIRS' else 'viirs_cells'
    indexed = []
    for record in pairs:
        week = date.fromisoformat(record['week'])
        iso_year, iso_week, _ = week.isocalendar()
        indexed.append({**record, '_calendar_year': week.year, '_iso_year':iso_year,
                        '_iso_week':iso_week, '_value':record[key], '_other':record[other]})
    rows = []
    alerts = []
    eligible = 0
    for target in indexed:
        # This is a retrospective comparison: years after the target year may
        # participate, but all observations from the TARGET calendar year are excluded.
        seasonal_years = defaultdict(list)
        for item in indexed:
            if item['_calendar_year'] == target['_calendar_year']:
                continue
            if seasonal_distance(item['_iso_week'], target['_iso_week']) <= SEASON_HALF_WINDOW_WEEKS:
                seasonal_years[item['_calendar_year']].append(item['_value'])
        # Per-year median means a year with five matching weeks is not weighted
        # five times more than a year with just one retrieved week.
        samples = [statistics.median(values) for values in seasonal_years.values()]
        year_count = len(samples)
        row = {'week':target['week'], 'year':target['_calendar_year'],
               'iso_week':target['_iso_week'], 'observed_cells':target['_value'],
               'other_sensor_cells':target['_other'],
               'sensor_difference_cells':(target['_value']-target['_other']) if target['_other'] is not None else None,
               'reference_years':year_count, 'baseline_median':None,
               'seasonal_p10':None, 'seasonal_p90':None,
               'robust_score':None, 'ratio_to_baseline':None,
               'elevated':False, 'status':'insufficient_reference'}
        if year_count >= min_reference_years:
            eligible += 1
            median = float(statistics.median(samples))
            q10, q90 = quantile(samples,.1), quantile(samples,.9)
            mad = float(statistics.median(abs(v-median) for v in samples))
            # A minimum scale guards the degenerate zero-MAD case but does not
            # manufacture a confidence interval or numerical significance.
            scale = max(1., 1.4826*mad, 0.15*median)
            score = (target['_value']-median)/scale
            elevated = bool(target['_value'] > median and
                            target['_value'] > q90 and score >= threshold)
            row.update(baseline_median=round(median,2),seasonal_p10=round(q10,2),
                       seasonal_p90=round(q90,2),robust_score=round(score,2),
                       ratio_to_baseline=round(target['_value']/median,2) if median else None,
                       elevated=elevated,status='elevated' if elevated else 'compared')
            if elevated:
                alerts.append(row)
        rows.append(row)
    return {'series':rows, 'alert_weeks':alerts, 'comparable_weeks':eligible,
            'insufficient_reference_weeks':len(rows)-eligible}


def critical_periods(rows: list[dict]) -> list[dict]:
    """Only truly adjacent Mondays form a continuous elevated period."""
    periods, run = [], []
    def emit():
        if run:
            periods.append({'start':run[0]['week'], 'end':run[-1]['week'],
                            'weeks':len(run), 'peak_score':max(r['robust_score'] for r in run),
                            'peak_week':max(run,key=lambda x:x['robust_score'])['week'],
                            'max_observed_cells':max(r['observed_cells'] for r in run)})
    for row in rows:
        if row['elevated']:
            if run and date.fromisoformat(row['week']) != date.fromisoformat(run[-1]['week'])+timedelta(days=7):
                emit(); run=[]
            run.append(row)
        else:
            emit(); run=[]
    emit()
    return sorted(periods, key=lambda p:(-p['weeks'],-p['peak_score'],p['start']))


def analyze_anomalies(db, region: str, start: str, end: str,
                      mode: int, grid_km: float = 10.0,
                      sensor: str = 'VIIRS', threshold: float = 2.5,
                      viirs_source: str = 'VIIRS_SNPP_SP',
                      analysis_mode: str = 'strict') -> dict:
    if sensor not in ('MODIS','VIIRS'):
        raise ValueError('Sensor must be MODIS or VIIRS')
    if not math.isfinite(threshold) or not 1.5 <= threshold <= 5:
        raise ValueError('Threshold must be 1.5–5')
    if analysis_mode not in ('strict', 'archive_positive_only'):
        raise ValueError('Choose strict or archive_positive_only analysis mode')
    if analysis_mode == 'archive_positive_only':
        if mode:
            raise ValueError('Archive-only analysis requires actual, non-simulated SP records')
        if region not in REGIONS or region == 'all':
            raise ValueError('Choose a bounded study region')
        if viirs_source not in VIIRS_SP_SOURCES:
            raise ValueError('Choose a supported VIIRS SP source')
        from datetime import date as _date
        a, b = _date.fromisoformat(start), _date.fromisoformat(end)
        if a > b or (b-a).days > 366*30 or not 5 <= grid_km <= 50:
            raise ValueError('Invalid date range or grid size')
        weeks = list(candidate_weeks(a, b))
        if len(weeks) > 1600:
            raise ValueError('Too many calendar weeks')
        weekly = spatial_weekly_counts(db, region, a, b, mode, grid_km, viirs_source)
        chosen = 'MODIS_SP' if sensor == 'MODIS' else viirs_source
        comparison = viirs_source if sensor == 'MODIS' else 'MODIS_SP'
        series = []
        for week in weeks:
            wk = week.isoformat()
            observed = weekly.get((wk, chosen))
            if not observed or observed['detections'] == 0:
                continue  # Zero and missing weeks are NEVER imputed from positive-only archives.
            other = weekly.get((wk, comparison))
            selected_cells = observed['occupied_cells']
            other_cells = other['occupied_cells'] if other and other['detections'] > 0 else None
            series.append({'week': wk,
                           'modis_cells': selected_cells if sensor == 'MODIS' else other_cells,
                           'viirs_cells': selected_cells if sensor == 'VIIRS' else other_cells,
                           'modis_detections': observed['detections'] if sensor == 'MODIS' else (other['detections'] if other else None),
                           'viirs_detections': observed['detections'] if sensor == 'VIIRS' else (other['detections'] if other else None)})
        overlap = {'series': series, 'data_mode': 'real', 'weeks_in_range': len(weeks),
                   'eligible_weeks': len(series),
                   'excluded_weeks': {'no_positive_detection_in_selected_source': len(weeks)-len(series)},
                   'status': 'not_calibrated', 'validation': None}
    else:
        overlap = analyze_harmonization(db,region,start,end,mode,grid_km,viirs_source)
    result = compute_seasonal_anomalies(overlap['series'],sensor,threshold)
    years = len({date.fromisoformat(p['week']).year for p in overlap['series']})
    periods = critical_periods(result['series'])
    status = ('insufficient_history' if result['comparable_weeks']==0 else
              'archive_exploratory' if analysis_mode == 'archive_positive_only' else
              'practice_only' if mode else 'retrospective_comparison')
    return {
        'region':region, 'start':start, 'end':end, 'data_mode':overlap['data_mode'],
        'analysis_mode':analysis_mode,
        'status':status, 'version':ANOMALY_VERSION, 'sensor':sensor,
        'viirs_source':viirs_source,
        'metric':'distinct approximately %.0f km grid cells with >=1 detection in an eligible full week' % grid_km,
        'grid_km':grid_km, 'threshold':threshold,
        'seasonal_window_weeks':SEASON_HALF_WINDOW_WEEKS,
        'minimum_reference_years':MIN_REFERENCE_YEARS,
        'years_with_paired_observations':years,
        'weeks_in_range':overlap['weeks_in_range'],
        'paired_weeks':overlap['eligible_weeks'],
        'excluded_weeks':overlap['excluded_weeks'],
        'comparable_weeks':result['comparable_weeks'],
        'insufficient_reference_weeks':result['insufficient_reference_weeks'],
        'elevated_weeks':len(result['alert_weeks']),
        'critical_periods':periods[:15],
        'series':result['series'],
        'calibration_status':overlap['status'],
        'calibration_promoted':bool(overlap['validation'] and overlap['validation']['promoted'] and mode==0 and analysis_mode=='strict'),
        'visual_uncertainty':{
            'seasonal_envelope':'Empirical 10th–90th percentile across eligible reference years; not a predictive/confidence interval.',
            'sensor_difference':'Signed difference between observed occupied-cell counts from separate sensors; not measurement error or a confidence interval.',
            'data_gaps':('Archive-only: weeks without positive detections are excluded and cannot be interpreted as zeros; apparent seasonal baselines are conditioned on detection.' if analysis_mode=='archive_positive_only' else 'Missing/partial retrieval weeks are EXCLUDED, never interpreted as zero activity.'),
        },
        'method':'Leave-one-calendar-year-out seasonal reference; ISO-week +/-2 window; one median observation per comparison year; median, MAD-based heuristic, and 10th–90th empirical percentiles.',
        'notes':([
            'ARCHIVE-ONLY MODE: Positive-detection weeks only; absence of a detection never proves a zero-fire week.',
            'Selection on detected weeks can exaggerate typical activity and bias seasonal comparisons; no calibration or complete-coverage claim.',
        ] if analysis_mode=='archive_positive_only' else []) + [
            'Retrospective exploratory flags are NOT statistically significant anomalies, forecasts, or wildfire alerts.',
            'The seasonal reference may use years after the inspected year, so it must not be used as a live detection/forecasting backtest.',
            'A successful API query does not prove cloud-free satellite coverage or that no fire occurred.',
            'This analysis uses observed STANDARD-PROCESSED occupied grid cells separately for each sensor. Rejected or unvalidated cross-sensor conversion is NEVER applied.',
            'Values from MODIS and VIIRS have different detection sensitivities. Historical variation and cross-sensor disagreement must not be labelled formal uncertainty bounds.',
        ],
        'message':(
            ('Not enough archive-positive reference years for the selected source and region; choose at least five years with positive detections and a full-year date range.' if analysis_mode=='archive_positive_only' else 'Not enough eligible historical reference years for a seasonal comparison; load multiple overlapping years with both sources and successful regional retrieval provenance.')
            if status=='insufficient_history' else
            'Archive-only positive-detection exploratory comparison. Unobserved weeks are omitted, not zero; seasonal statistics are selection-biased and NOT proof of comprehensive coverage or a calibration.'
            if analysis_mode=='archive_positive_only' else
            'SIMULATED PRACTICE ONLY: seasonal flags illustrate the interface, not NASA or actual fire behavior.'
            if mode else
            'Retrospective descriptive flags generated from retrieved paired observations; not independent validation of fires or statistical significance.'),
    }
