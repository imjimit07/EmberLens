# EmberLens v0.8.6 — analysis diagnostics and Planetary Computer request fix

- Fixed Planetary Computer Data API statistics POST: the request body must be a GeoJSON Feature directly, rather than `{"geojson": feature}`. The previous shape produces an invalid request on typical TiTiler endpoints.
- Historical Coverage Audit now makes region/date zero-matches and limits of CSV-based provenance explicit.
- Harmonization Lab and Seasonal Signal Explorer show actionable insufficiency explanations without treating NRT observations as standard-processed or missing retrievals as zeroes.
- Updated application version identifier.

## Important
The supplied MODIS NRT file covers 2026-11-01 through 2026-11-14; VIIRS SNPP NRT covers 2026-11-01 through 2026-11-02. These cannot support a multi-year SP-only seasonal baseline or the vetted cross-sensor harmonization experiment. Import historical SP datasets and collect matching region-specific FIRMS retrieval provenance to unlock those scientific methods. Planetary Computer is independent of Supabase; live access requires the external service to be reachable and can be rate limited. No live cloud deployment or live Planetary Computer success is claimed.
