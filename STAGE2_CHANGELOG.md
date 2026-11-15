# Stage 2 completion notes

**Version:** 0.2.0

## Delivered

1. Historical NASA FIRMS archive importer (`backend/archive.py`, `scripts/import_archive.py`): streams `*.csv`, `*.csv.gz`, `*.zip` archives, atomic chunked SQLite ingestion, fingerprints/deduplication and detailed per-file provenance in `archive_manifests`.
2. Resumable NASA Area API backfill (`POST /api/import/firms-range`): windows of up to five days each, 90 days per invocation, skip completed windows, safe failure reporting, key never sent to browser. Original one-window endpoint now records region-scoped retrieval status.
3. NASA advertised product date availability (`POST /api/availability/refresh`) and cached per-source min/max dates.
4. Coverage audit (`GET /api/coverage`): by region, product, and month; separately reports positive days, successful API retrieval days, queried-without-positive days, unknown days and outside-product dates. Explicit NRT/SP same-date overlap warnings.
5. Updated browser UI: coverage audit cards and archive manifest list; historical backfill date controls; API dataset availability refresh; distinguish retrieved/no-positive calendar months from unknown.
6. Automated tests for atomic rollback, archive formats, idempotent reimports, source validation, retrieval isolation, empty valid responses, date boundaries, resuming/forcing backfills, failed windows, NRT/SP overlaps and Stage 1 regressions.

## Verification

- `python -m pytest -q`: **16 passed**.
- `node --check frontend/app.js`: passed.
- `python -m compileall -q backend scripts`: passed.
- Headless Chromium offline-render smoke test: rendered 4 coverage cards, original map/calendar, no JavaScript exceptions.

## Not included

- Actual NASA historical archive data or a secret FIRMS MAP_KEY; users need to obtain real observations from NASA or connect their key.
- Satellite orbital/granule acquisition completeness; API request success does *not* assert acquisition completeness.
- A cross-sensor harmonization model; planned for Stage 3.

**Caution:** Verify applicable project and competition requirements before deploying or submitting the software.
