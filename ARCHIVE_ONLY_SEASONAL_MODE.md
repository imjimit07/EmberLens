# EmberLens v0.9.4 — Archive-only Seasonal Signal Explorer

## What changed

In **Seasonal signal explorer → DATA EVIDENCE**, select:
- **Strict · verified retrievals** (original behaviour; requires complete regional FIRMS retrieval provenance for both SP sources).
- **Archive-only · positive weeks** (new; supports historical CSV/ZIP archives stored in local SQLite without reconstructing unverifiable API retrieval evidence).

In archive-only mode, EmberLens uses **only weeks with one or more actual detections in the selected SP source**. It does not interpret missing detections as zero, and does not require another satellite source to have positive records. Missing other-sensor values appear as *Unknown*, not zero. Seasonal references still use at least **four other independent calendar years**, a ±2 ISO-week window, and a leave-one-year-out median / MAD heuristic. The selected start/end range must include the reference years (e.g. 2020-01-01 to 2024-12-31). A bounded study region is required.

## Critical scientific caveats

- Archive-only mode is conditional on positive detections; missing weeks may be unobserved, truly quiet, or excluded for other reasons. No implicit zero observations are created.
- Conditioning on positive weeks creates selection bias. Seasonal baselines and flags are exploratory and *cannot* be treated as complete fire activity, scientific detection probability, or fire-risk forecasts.
- The mode **never performs or promotes a MODIS-to-VIIRS calibration**. The original strict Harmonization Lab is unchanged.
- The original archive imports are left intact and no fake `query_windows` records are inserted.

## Run and test

Keep your existing `data/emberlens.sqlite` file when upgrading. Extract this project over your existing source files, or copy the new `backend/anomalies.py`, `backend/main.py`, `frontend/app.js`, `frontend/index.html`, and `frontend/styles.css`. Start `start_local_sqlite.bat` and choose a bounded region, 2020–2024, and the new mode.

Tested with `pytest -q`: **77 passed**. Frontend JavaScript passes `node --check`.
