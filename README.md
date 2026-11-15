# EmberLens v0.8.4

**Purpose:** EmberLens explores NASA FIRMS MODIS and VIIRS active-fire detections through a geographic view, historical activity calendar, source coverage audit, seasonal signal explorer, and an experimental MODIS-to-VIIRS comparison. The project uses the official FIRMS Area API and archive CSV, CSV.GZ, and ZIP files. It does not measure unique wildfire incidents, burned area, or complete satellite acquisition coverage.

The current release supports platform-aware ingestion for MODIS and VIIRS Suomi-NPP, NOAA-20, and NOAA-21 products. It also adds optional Meteomatics weather context and Microsoft Planetary Computer MODIS NDVI statistics. Each VIIRS platform remains separately labelled, and the Harmonization Lab compares MODIS standard processing against one selected VIIRS standard-processing platform at a time. NRT is supported for data exploration but excluded from historical harmonization.

**Deployment note:** Local development and tests use SQLite. The free cloud deployment uses Vercel's Python/FastAPI runtime and Supabase PostgreSQL, so production data is not written to Vercel's ephemeral filesystem. The Supabase connection string is server-side only and should use the Transaction pooler for Vercel serverless requests.

## CSV upload limits

- On Vercel, browser CSV uploads support **up to 50 MB** through a private Supabase Storage bucket. The browser sends the file directly to Supabase using a short-lived signed token and resumable TUS chunks, with progress and retry support, bypassing Vercel's 4.5 MB Function request-body limit.
- The server then downloads, validates, and imports the uploaded CSV. The parser limits a single CSV import to 200,000 rows.
- Configure `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` as **server-only Vercel environment variables**, and apply `supabase/migrations/20261115000100_emberlens_csv_storage.sql` in the Supabase SQL Editor. Never expose the service-role key in frontend code or commit it to Git.
- Temporary CSV objects are removed after processing. Supabase Free's global Storage file-size setting must allow 50 MB, which is its Free-plan maximum.
- Local direct browser uploads remain capped at 15 MB. For larger archives or `.csv.gz` and `.zip` inputs, use `scripts/import_archive.py` from a machine with `SUPABASE_DB_URL` configured.

## Deploy for free with Vercel + Supabase

Follow [DEPLOY_VERCEL_SUPABASE.md](DEPLOY_VERCEL_SUPABASE.md). Vercel detects the root `app.py` FastAPI entrypoint, and Supabase stores persistent PostgreSQL data. The project is designed for the free Hobby/Free tiers, subject to their usage limits and inactivity rules. This is a personal or non-commercial demo deployment, not a guaranteed always-on production service.

## Environmental context integrations

The deployment update adds a Vercel FastAPI entrypoint and Supabase PostgreSQL adapter. It also supports direct-to-Supabase 50 MB CSV uploads and VIIRS brightness-field alias validation. Local tests remain SQLite-backed. See [DEPLOY_VERCEL_SUPABASE.md](DEPLOY_VERCEL_SUPABASE.md) for environment variables, schema setup, free-tier limits, and deployment steps.

- **Meteomatics Weather API:** optional six-hourly temperature, relative humidity, wind speed and direction, and six-hour precipitation series at the selected region centroid. Configure `METEOMATICS_USERNAME` and `METEOMATICS_PASSWORD` on the backend only. Queries are limited to 1-14 past days.
- **Microsoft Planetary Computer:** public STAC search for `modis-13Q1-061` and Data API area statistics for its `250m_16_days_NDVI` asset. It returns mean, minimum, maximum, P10/P90 when available, valid-pixel coverage and counts for up to eight 16-day composites locally, or three per Vercel request, in a 1-120 day range. Large-area statistics are downsampled to a maximum processing dimension of 512 pixels. No Planetary Computer credentials are required.
- The dashboard includes an **Environmental context** panel with independent load buttons and provider status.
- A 30-minute process-local cache reduces repeated partner API requests.
- Weather and NDVI remain contextual data. They are not fed into the MODIS-VIIRS harmonization model, and neither is presented as a fire-risk forecast.

## FIRMS platform coverage

- The source catalogue supports these FIRMS active-fire Area API identifiers: `MODIS_SP`, `MODIS_NRT`, `VIIRS_SNPP_SP`, `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_SP`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_SP`, and `VIIRS_NOAA21_NRT`.
- CSV validation checks that a VIIRS satellite field matches the selected platform, S-NPP, NOAA-20, or NOAA-21, instead of accepting every VIIRS row under a single source label.
- Coverage auditing uses each platform's distinct mission start date, and overlap diagnostics are reported per mission.
- The Harmonization Lab lets the user select one VIIRS standard-processing platform at a time. It never blends the three VIIRS satellite streams into a single calibration target. NRT remains excluded from the harmonization experiment.
- The NASA map key stays server-side in `FIRMS_MAP_KEY`; the browser never receives it.

## Operations and reliability

- `GET /api/health/live` and `GET /api/health/ready` keep process liveness and database readiness separate.
- `GET /api/profile` reports loaded positive records by source, positive dates, date extent, and FRP availability. It does not claim complete satellite acquisition coverage.
- `GET /api/export/detections.csv` streams active, filter-matched observations to CSV, with a maximum of 100,000 records locally and 20,000 on Vercel.
- SQLite workload indexes cover active data mode, date, sensor, and source lookups. Request handlers do not rerun schema creation on each connection.
- The application uses a tuned SQLite busy timeout, WAL checkpoint policy, synchronous mode, temporary storage, and page cache.
- GZip response compression, security headers, and no-store API responses are enabled for supported responses.
- Production mode, `EMBERLENS_ENV=production`, hides interactive OpenAPI/Swagger documentation.
- In production, CSV upload, FIRMS retrieval, range backfill, and availability refresh require an `X-EmberLens-Admin` token. If no token of at least 24 characters is configured, writes fail closed with HTTP 503; missing or incorrect tokens receive HTTP 401. Public map, calendar, and analysis routes remain read-only. The dashboard shows the token field only in production and does not persist it.
- `scripts/db_maintenance.py` creates consistent online backups and supports integrity checks, optimize, WAL checkpoint, and optional VACUUM.
- `.github/workflows/ci.yml` runs Python tests and compilation, plus frontend JavaScript syntax validation, on pushes and pull requests.
- `scripts/benchmark_queries.py` measures the actual local database and query plan. Timing is machine- and dataset-specific.
- `Dockerfile` and `compose.yaml` run as a non-root user and persist the SQLite database in a named volume.

## Run locally

```bash
python -m pip install -r requirements.txt
python -m pytest -q
python -m uvicorn backend.main:app --reload
```

Open `http://127.0.0.1:8000`. Development mode exposes `/docs`; production mode disables it. The UI sets its default end date to the browser's current local date.

## Docker deployment

1. Install Docker Engine/Desktop and the Compose plugin.
2. Copy `.env.example` to `.env`. Generate a strong administrator token with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and put the result in `EMBERLENS_ADMIN_TOKEN`. Set `FIRMS_MAP_KEY` only if FIRMS API imports are required. If Meteomatics access is configured, set `METEOMATICS_USERNAME` and `METEOMATICS_PASSWORD` too. Compose passes secrets to the backend container, never to the browser.
3. Build and start:

```bash
docker compose up --build -d
docker compose ps
```

4. Verify readiness:

```bash
curl http://localhost:8000/api/health/ready
```

5. Stop without deleting imported data:

```bash
docker compose down
```

The named `emberlens-data` volume survives container replacement. **Do not run `docker compose down -v` unless intentionally deleting the database.** Use your hosting provider's persistent disk or volume mount at `/app/data` and set `EMBERLENS_DB=/app/data/emberlens.sqlite`. The image defaults to one worker because SQLite is the selected embedded database; scaling horizontally requires moving the database and write workload to a server database and adding coordinated job/backfill execution.

**Production security:** Serve the application behind HTTPS. The admin token is a shared operator credential, not a multi-user identity system. Keep it in your host's secret manager, never commit it to Git, and rotate it if exposed. Enter it in the dashboard's administrator-token field when importing data. The browser sends it only in the `X-EmberLens-Admin` header on data-changing requests. Without a configured token, production imports and retrieval are disabled. The token does not encrypt the database or replace reverse-proxy rate limiting, backups, or host access controls.

## Backup and maintenance

Run these commands from the project directory, or inside the container with an appropriate backup mount:

```bash
# Online consistent backup while the app is running
python scripts/db_maintenance.py --backup backups/emberlens-backup.sqlite

# Verify the database and update query-planner statistics
python scripts/db_maintenance.py --integrity-check --optimize

# Inspect actual performance on this database and region
python scripts/benchmark_queries.py --region punjab --start 2020-01-01 --end 2026-11-12

# Optional: reclaim free pages during a quiet period, takes a write lock
python scripts/db_maintenance.py --vacuum
```

Backups should be copied off the host and restore-tested. `VACUUM` should not be run during a live import or backfill window.

## Validation and limitations

- Run `python -m pytest -q` for the regression and acceptance suite.
- Check `/api/health/ready`, `/api/status`, and `/api/profile` after importing the intended data.
- In production, verify missing or incorrect admin tokens are rejected and a valid token permits only the intended data-changing requests.
- Confirm Docker health status is `healthy` and the volume is mounted persistently.
- Export a small filtered CSV and confirm its date, region, and sensor fields.
- Run the query benchmark against the same database before and after changes if making performance claims. Do not publish a speed-up percentage without measured before-and-after results on the same hardware and data.
- Real NASA dataset harmonization and anomaly output still require genuine NASA source records and appropriate independent scientific review. Software readiness does not equal scientific validation.

**Research and education only. Do not use FIRMS hotspots as the sole basis for emergency response, evacuations, or other safety-critical decisions.**
