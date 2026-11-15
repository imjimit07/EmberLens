# Stage 6 changelog — FIRMS platform-aware ingestion (v0.6.0)

## Why this update

The NASA FIRMS Area API currently exposes active-fire products for MODIS and VIIRS on Suomi-NPP, NOAA-20 and NOAA-21. The previous project version supported only MODIS and Suomi-NPP VIIRS. This release adds the other FIRMS VIIRS streams while preserving sensor/platform provenance.

## Changes

- Added the current FIRMS source IDs for VIIRS NOAA-20 and NOAA-21, standard-processing and near-real-time.
- Added source-specific satellite identity checks to prevent NOAA-20 / NOAA-21 / Suomi-NPP records being imported under the wrong product.
- Updated coverage audit mission-start dates for each VIIRS platform.
- Expanded the dashboard import selector to all eight supported source IDs.
- Added a VIIRS platform selector to the Harmonization Lab. The model compares MODIS standard-processing with one selected VIIRS standard-processing mission at a time.
- Updated source labels and README guidance.

## Scientific guardrails

- Harmonization only uses standard-processing sources; NRT is still excluded because archive/NRT records can be reprocessed and replaced by standard products.
- VIIRS platforms are not pooled as one calibration target because their overpass schedules and coverage differ.
- Occupied grid cells are a spatial activity proxy, not unique fires or burned area.
- A completed API request is retrieval provenance, not proof of cloud-free or complete satellite observation. Missing data are never silently converted to zero detections.
- No genuine NASA data or calibration results are bundled. A user must provide a valid FIRMS MAP_KEY or download FIRMS archive files through Earthdata.

## Verified sources

- NASA FIRMS Area API: https://firms.modaps.eosdis.nasa.gov/api/area/
- NASA FIRMS Archive Download: https://firms.modaps.eosdis.nasa.gov/download/
- NASA Earthdata FIRMS overview: https://www.earthdata.nasa.gov/data/tools/firms
