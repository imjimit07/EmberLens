# EmberLens v0.8.6

**Purpose:** EmberLens helps researchers compare NASA FIRMS MODIS and VIIRS active-fire observations without mistaking missing records or sensor differences for changes in fire activity. It provides a geographic view, historical activity calendar, source coverage audit, source-aware period comparison, seasonal signal explorer, and an experimental MODIS-to-VIIRS comparison. The project uses the official FIRMS Area API and archive CSV, CSV.GZ, and ZIP files. It does not measure unique wildfire incidents, burned area, or complete satellite acquisition coverage.

The current release supports platform-aware ingestion for MODIS and VIIRS Suomi-NPP, NOAA-20, and NOAA-21 products. It also adds free NASA POWER daily weather context and Microsoft Planetary Computer MODIS NDVI statistics. Each VIIRS platform remains separately labelled, and the Harmonization Lab compares MODIS standard processing against one selected VIIRS standard-processing platform at a time. NRT is supported for data exploration but excluded from historical harmonization.

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

- **NASA POWER Daily API:** free daily temperature, relative humidity, wind speed, and corrected precipitation at the selected study-region centroid. It requires no account or API key. The dashboard can request up to ten years; complete stored backfills are served from the database.
- **Microsoft Planetary Computer:** public STAC search for `modis-13Q1-061` and Data API statistics for its `250m_16_days_NDVI` asset. It returns mean, minimum, maximum, P10/P90 when available, valid-pixel coverage and counts for each selected MODIS tile that intersects the study region. It does not construct a complete regional mosaic. Large-area statistics are downsampled to a maximum processing dimension of 512 pixels. No Planetary Computer credentials are required.
- The **Backfill free context, 2020 to 2024** control processes each configured study region in resumable 93-day batches. It stores daily weather and 16-day NDVI summaries for Punjab & Haryana, California, and Western Amazon. In production it requires the existing administrator token. Keep the page open during this one-time operation; completed batches remain stored if a provider rate-limits a later request.
- For a local or operator-run backfill that does not rely on an open browser, run `python scripts/backfill_free_context.py`. It defaults to all three study regions and 2020-01-01 through 2024-12-31; `--regions punjab` and `--start` / `--end` narrow the scope. Completed quarters are safe to rerun because rows are upserted.
- Meteomatics remains an optional credentialed compatibility route at `GET /api/context/weather?provider=meteomatics`; it is not used by the dashboard's free workflow.
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
- `GET /api/change-explanation` compares two non-overlapping periods, reports raw loaded-record change and a subtotal restricted to sources with positive records in both periods, and returns the source-specific evidence plus limitations. The dashboard can export the exact query and result as a Markdown evidence report. It never converts an absent source into zero detections or calls retrieval provenance satellite coverage.
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
2. Copy `.env.example` to `.env`. Generate a strong administrator token with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and put the result in `EMBERLENS_ADMIN_TOKEN`. Set `FIRMS_MAP_KEY` only if FIRMS API imports are required. NASA POWER and Planetary Computer use the free workflow without credentials. Meteomatics credentials remain optional for its compatibility route. Compose passes secrets to the backend container, never to the browser.
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
- [EVALUATION_EVIDENCE.md](EVALUATION_EVIDENCE.md) is the honest record for a completed case study, a small intended-user task study, and deployment checks. Fill it only with observed outcomes and link each case study to its exported evidence report.
- [JUDGE_DEMO.md](JUDGE_DEMO.md) provides a short live demonstration built around one real comparison. Replace every bracketed field with values from the active dataset before presenting.

**Research and education only. Do not use FIRMS hotspots as the sole basis for emergency response, evacuations, or other safety-critical decisions.**
