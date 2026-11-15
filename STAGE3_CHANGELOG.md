# Stage 3 · Overlap-period calibration and chronological validation

**Version 0.3.0 — research/practice MVP**

## Delivered

- Region/date/grid-size `GET /api/harmonization` endpoint.
- Uses **only** MODIS_SP and VIIRS_SNPP_SP to prevent accidental NRT/SP mixing.
- Computes *distinct spatial-grid cells with at least one hotspot record during each full UTC ISO week*, not number of fires or burned area. Regional equirectangular grid approximation, default about 10 km, adjustable 5–50 km.
- For real data, accepts a paired week only when **every date** in that week belongs to a completed **region-specific FIRMS API retrieval window for both sources**. A successful API retrieval does not establish complete/cloud-free satellite observations.
- **Chronological holdout:** first 75% of eligible weeks for training, final 25% (at least 6) for validation, 26 total pairs minimum.
- Candidate log1p OLS relationship between MODIS and VIIRS occupied-cell counts, with strictly positive slope and identifiable training variance.
- Baselines: (a) identity/raw MODIS, and (b) **training-fitted multiplicative baseline**. All MAEs computed on unseen weeks.
- Candidate promotion requires **at least 2% lower holdout MAE** than the fitted multiplicative baseline. A rejected model is explicitly labelled, not silently applied.
- CSV positive-only archives can still populate the explorer but cannot establish valid zero-detection days or weeks; no false assumption about complete acquisition.
- Simulated data exercises the algorithm **in practice-only mode**, never marked scientifically validated.
- Interactive dashboard lab: training/test counts, validation charts, table, provenance and method/caveats.
- Reproducible JSON exporter `scripts/export_validation.py`.
- All 16 Stage 1/2 tests retained plus 8 Stage 3 tests.

## What this DOES NOT claim

- That either sensor measures a ground-truth number of fires.
- That a regression trained on a short range generalizes to other regions, years or land-cover types.
- That completed retrieval requests prove uniform satellite overpass, cloud-free imaging or a true zero-fire period.
- That raw NRT and standard processing can be merged as independent observations.
- That a held-out VIIRS agreement test demonstrates forecast accuracy, wildfire detection accuracy or operational emergency suitability.

## Important acceptance details

Even real-data models that pass the local holdout criterion remain **research prototypes**. The algorithm only returns fitted estimates in the `holdout` rows as an evaluation; Stage 3 does not alter raw observations or overwrite the Stage 1/2 calendar. Comparing candidate MAE to a *trained* scaled baseline is essential; beating raw MODIS alone is not enough.

### Limitations to address in later stages

1. Retrieve science-quality archives and separately verify acquisition coverage / quality masks if available.
2. Use true equal-area geospatial cells for multi-region comparisons, and test several grid resolutions.
3. Conduct geographically and temporally independent validation, report uncertainty by region and season, and consider differences in overpass time/land cover.
4. Only then consider generating harmonized historical calendar values or anomaly claims with confidence intervals.

### Quick test

```sh
python -m pytest -q
python scripts/seed_demo.py
uvicorn backend.main:app --reload
```

To populate **real** paired weeks, import both SP sources using the Stage 2 region-specific FIRMS backfill endpoint over the same dates (or obtain equivalent verified retrieval provenance). Historical archive records without verified retrieval windows are intentionally excluded from real-data model training.
