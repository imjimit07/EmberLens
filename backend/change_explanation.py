"""Source-aware period comparisons for loaded FIRMS observations.

This module deliberately answers a narrow question: whether a change in the
*loaded detection records* remains after restricting both periods to sources
that have positive records in each.  It is not a fire-count or coverage model.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from backend.core import SOURCE_LABELS, SOURCES, region_where


METHOD_VERSION = "change-explanation/v1"


def _direction(before: int, after: int) -> str:
    if after > before:
        return "increase"
    if after < before:
        return "decrease"
    return "no_change"


def _count_change(before: int, after: int) -> dict:
    delta = after - before
    return {
        "before": before,
        "after": after,
        "delta": delta,
        "direction": _direction(before, after),
        "percent_change": round(delta / before * 100, 1) if before else None,
    }


def _retrieved_days(db, region: str, start: str, end: str, mode: int) -> dict[str, int]:
    """Count days overlapped by recorded successful requests, never coverage."""
    if mode or region == "all":
        return {}
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    rows = db.execute("""SELECT source,start_date,end_date FROM query_windows
        WHERE region=? AND end_date>=? AND start_date<=? AND status='retrieved'""",
                      (region, start, end)).fetchall()
    days: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        current = max(first, date.fromisoformat(row["start_date"]))
        final = min(last, date.fromisoformat(row["end_date"]))
        while current <= final:
            days[row["source"]].add(current.isoformat())
            current += timedelta(days=1)
    return {source: len(values) for source, values in days.items()}


def analyze_change_explanation(db, region: str, before_start: str, before_end: str,
                               after_start: str, after_end: str, mode: int,
                               sensor: str = "ALL") -> dict:
    """Compare two non-overlapping periods while exposing source-mix changes."""
    if sensor not in ("ALL", "MODIS", "VIIRS"):
        raise ValueError("Invalid sensor")
    before_first, before_last = date.fromisoformat(before_start), date.fromisoformat(before_end)
    after_first, after_last = date.fromisoformat(after_start), date.fromisoformat(after_end)
    if before_last >= after_first:
        raise ValueError("Earlier period must end before the later period begins")
    where, box = region_where(region)
    sensor_sql = "" if sensor == "ALL" else " AND sensor=?"
    sensor_params = [] if sensor == "ALL" else [sensor]

    def observations(start: str, end: str) -> dict[str, dict]:
        rows = db.execute(f"""SELECT source,sensor,COUNT(*) AS detections,
            COUNT(DISTINCT acq_date) AS positive_days
            FROM detections WHERE {where} AND acq_date BETWEEN ? AND ?
            AND simulated=?{sensor_sql} GROUP BY source,sensor ORDER BY source""",
                          [*box, start, end, mode, *sensor_params]).fetchall()
        return {row["source"]: dict(row) for row in rows}

    before = observations(before_start, before_end)
    after = observations(after_start, after_end)
    before_retrieved = _retrieved_days(db, region, before_start, before_end, mode)
    after_retrieved = _retrieved_days(db, region, after_start, after_end, mode)
    before_sources, after_sources = set(before), set(after)
    matching = sorted(before_sources & after_sources)
    all_sources = sorted(before_sources | after_sources)

    source_rows = []
    for source in all_sources:
        earlier, later = before.get(source, {}), after.get(source, {})
        earlier_count, later_count = earlier.get("detections", 0), later.get("detections", 0)
        source_rows.append({
            "source": source,
            "label": SOURCE_LABELS.get(source, source),
            "sensor": SOURCES.get(source),
            "before": {"detections": earlier_count, "positive_days": earlier.get("positive_days", 0),
                       "retrieved_request_days": before_retrieved.get(source, 0)},
            "after": {"detections": later_count, "positive_days": later.get("positive_days", 0),
                      "retrieved_request_days": after_retrieved.get(source, 0)},
            "change": _count_change(earlier_count, later_count),
            "positive_records_in_both_periods": source in matching,
        })

    raw = _count_change(sum(row["detections"] for row in before.values()),
                        sum(row["detections"] for row in after.values()))
    matched = _count_change(sum(before[source]["detections"] for source in matching),
                            sum(after[source]["detections"] for source in matching)) if matching else None
    sources_only_before = sorted(before_sources - after_sources)
    sources_only_after = sorted(after_sources - before_sources)
    same_positive_source_set = bool(before_sources) and before_sources == after_sources
    survived = bool(matched and raw["direction"] == matched["direction"] and raw["direction"] != "no_change")

    if raw["direction"] == "no_change":
        state = "no_raw_change"
        summary = ("The selected periods have the same total number of loaded detection records. "
                   "The source evidence is still shown because equal totals can hide a changed source mix.")
    elif not matching:
        state = "not_comparable"
        summary = ("No source has loaded positive records in both periods, so the apparent change "
                   "cannot be checked on a like-for-like source basis.")
    elif not same_positive_source_set:
        state = "source_mix_changed"
        summary = ("The combined loaded-detection count changed while the set of sources with positive "
                   "records also changed. The matched-source comparison is shown, but it does not turn "
                   "missing or unimported records into zeroes.")
    elif survived:
        state = "direction_survives_matched_sources"
        summary = ("The direction of the loaded-detection change is the same after restricting the "
                   "comparison to sources with positive records in both periods.")
    else:
        state = "direction_changes_or_is_flat_when_matched"
        summary = ("The combined change does not retain the same direction after restricting to the "
                   "sources with positive records in both periods.")

    return {
        "method_version": METHOD_VERSION,
        "region": region,
        "sensor": sensor,
        "before": {"start": before_start, "end": before_end},
        "after": {"start": after_start, "end": after_end},
        "raw_loaded_detection_change": raw,
        "matched_positive_source_change": matched,
        "source_rows": source_rows,
        "source_evidence": {
            "positive_sources_before": sorted(before_sources),
            "positive_sources_after": sorted(after_sources),
            "matching_positive_sources": matching,
            "sources_only_before": sources_only_before,
            "sources_only_after": sources_only_after,
            "same_positive_source_set": same_positive_source_set,
            "direction_survives_matched_sources": survived,
        },
        "assessment": {"state": state, "summary": summary},
        "limitations": [
            "Counts are loaded positive active-fire detection records, not unique fires, burned area, or a complete measure of fire activity.",
            "A source absent from a period may be unimported, unavailable, or have no positive records; this analysis never treats absence as zero detections.",
            "Successful request days are retrieval provenance only, not proof of a satellite overpass, cloud-free observation, or complete geographic coverage.",
            "Matching sources improves comparability of loaded records but does not calibrate MODIS and VIIRS or establish an independent real-world outcome.",
        ],
    }
