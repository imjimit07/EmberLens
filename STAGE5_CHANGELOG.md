# EmberLens Stage 5 — deployment, performance and final polish (v0.5.0)

## Implemented

- Added SQLite indexes for the dashboard's active-mode/date/sensor filters and the Stage 3 source/date scan.
- Replaced request-handler connection context use with an explicit context manager that commits/rolls back and always closes SQLite connections. Added a regression test proving request connections are closed.
- Tuned SQLite connections with a bounded busy timeout, WAL mode, NORMAL synchronous mode, a 20 MiB page cache, in-memory temp storage and WAL auto-checkpointing.
- Separated startup schema initialization from request connections so routine API requests no longer execute `CREATE TABLE/INDEX IF NOT EXISTS` repeatedly.
- Added liveness/readiness routes, active dataset profile, bounded CSV streaming export, response compression, production docs toggle and standard security/cache headers.
- Bounded CSV upload reads as the request stream arrives rather than reading an unbounded body before enforcing the size limit.
- Added production-only, timing-safe shared admin-token checks to all data-changing endpoints. Production writes fail closed until a token of at least 24 characters is configured; public read endpoints remain available.
- Added Docker and Compose with a non-root runtime, container healthcheck and named persistent data volume.
- Added GitHub Actions CI for Python compile/tests and frontend JavaScript syntax validation.
- Added online consistent SQLite backup, integrity/optimize/checkpoint/VACUUM utility and local query benchmark scripts.
- Updated UI versioning, hid the API docs link in production mode, added dataset profile and filtered CSV export, and made date defaults dynamic.

## Verification

- Stage 1–4 regression suite is retained.
- All 39 tests pass. Stage 5 tests cover health/readiness, production docs mode, production admin-token authorization, headers/compression, data profile, streaming export, indexes/query plans, request-connection closure and backup/maintenance behavior.
- Query benchmark reports local measurements only; no performance improvement is claimed without same-machine before/after measurements.

## Deployment scope and limits

This is a single-instance SQLite deployment, not a horizontally scalable service. Persist `/app/data`; maintain backups; use one Uvicorn worker by default. For multiple replicas, shared concurrent writes, or high sustained ingestion, migrate to a server database and coordinated ingestion workers. The admin token is a shared operator credential, not full user authentication or authorization by role; use HTTPS and a secret manager.

The app remains a retrospective research prototype. NASA FIRMS hotspot detections are not unique fires, burned area, ground truth, or live emergency warnings. Scientific harmonization/anomaly validity still depends on real archive coverage and independent validation.
