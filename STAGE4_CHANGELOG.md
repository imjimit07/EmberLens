# EmberLens Stage 4 — Retrospective seasonal analysis (v0.4.0)

Stage 4 builds directly upon the Stage 3 matched-week observation and validation pipeline. The project now includes a seasonal activity explorer, empirical reference bands, sensor-disagreement visualization, critical-period grouping, API and JSON export. **Stage 4 does not create a unified calibrated fire time series or make wildfire forecasts.**

## What's included

- **Backend:** `backend/anomalies.py` provides `analyze_anomalies`, `compute_seasonal_anomalies`, and `critical_periods`.
- **API:** `GET /api/anomalies?region=punjab&start=2020-01-01&end=2025-12-31&grid_km=10&sensor=VIIRS&threshold=2.5`.
- **Dashboard:** "Seasonal anomalies" in the sidebar. Controls for sensor, weekly flag sensitivity, and focus year. Click individual plotted weeks or a critical period to inspect details; export a JSON report.
- **CLI:** `python scripts/export_anomalies.py --region punjab --start 2020-01-01 --end 2025-12-31 --output report.json`.
- **Verification:** `python -m pytest -q` (31 tests as of v0.4.0). Browser smoke test used an offline HTML render with responses from the running backend; it does not prove an externally hosted deployment.

## Method

1. Retain the Stage 3 **MODIS_SP and VIIRS_SNPP_SP** observations on the same approximate 5–50 km regional grid, in **whole ISO weeks** only. Do not silently merge NRT and SP records. In real-data mode a week is eligible only when the **same region** has successful FIRMS retrieval windows covering **each day** for **both** instruments. This tests API response coverage, NOT complete satellite acquisitions. Simulated practice data uses positive-positive paired weeks and is not evidence of real fire activity.
2. Choose one instrument's **observed occupied grid-cell counts** per week. No cross-sensor calibration is substituted into the seasonal activity series, including if Stage 3's candidate was promoted.
3. For each target week, find historical weeks in a ±2 ISO-week seasonal window, **excluding the entire target calendar year**. First take one median within each eligible reference year, then take the median, empirical P10/P90, and median absolute deviation (MAD) across those years.
4. Require at least **four distinct comparison years** before assigning a baseline or flag. The reference years can be after the target year: this is **retrospective comparison, not a prospective test or operational alert.** Missing, unverified, or partial weeks are not converted to zero observations. Sparse coverage can distort historical medians despite the minimum-year rule.
5. Calculate the descriptive robust score as `(observed - reference_median) / max(1, 1.4826×MAD, 0.15×reference_median)`. Flag an elevated week only when **observed > historical median**, **observed > empirical P90**, and **score ≥ chosen threshold** (default 2.5). This heuristic was selected for exploratory use; it has not been statistically calibrated for a false-positive rate.
6. Group only **exactly consecutive Mondays** into an elevated period. Any missing or unflagged week breaks the group. Sort by run length and maximum score.

## Interpretation and limitations

**These metrics are NOT quantities that can be interpreted as:**
- A number of unique fires, confirmed burning events, burned area, fire risk, or wildfire forecast.
- A p-value or proof of statistical significance. Weekly satellite observations may be seasonal, autocorrelated, and contaminated by coverage changes.
- A formal confidence/prediction interval. The shaded P10–P90 range describes variation across observed reference years.
- Instrument error or uncertainty in a true fire count. The signed observed MODIS/VIIRS gap illustrates **disagreement**, not a confidence interval.
- Confirmation that all individual days were cloud-free or covered by a satellite: successful API retrieval alone cannot establish that.

Where the model fails Stage 3 holdout validation, Stage 4 does not use its estimates. Even if Stage 3 calibration passes its local held-out MAE test, it is not ground-truth validation and Stage 4 continues to use raw individual-sensor data. All practice fixtures and screenshots must be labelled simulated. **Not for public emergency alerts or safety-critical decisions.**

### Future scientific work needed

Independent ground reference comparisons (such as validated fire perimeters where appropriate), multi-region evaluations, season-specific calibration diagnostics, acquisition/quality masks, more rigorous uncertainty quantification (such as time-blocked bootstrap where appropriate), and prospective out-of-time evaluation are needed before operational interpretations.

## Practical commands

```bash
# Run the application
python -m uvicorn backend.main:app --reload

# Export a complete retrospective report
python scripts/export_anomalies.py --region punjab --start 2020-01-01 --end 2025-12-31 --sensor VIIRS --threshold 2.5 --output stage4-analysis.json

# Run all regression and Stage 4 tests
python -m pytest -q
```

## Scope

This remains an exploratory research prototype. Do not use it for public emergency alerts or safety-critical decisions.
