# Deploy EmberLens on Vercel + Supabase (free tiers)

This deployment keeps the existing FastAPI backend and browser dashboard. Vercel runs the FastAPI application; Supabase PostgreSQL stores persistent observations and provenance. Local development and automated tests continue to use SQLite.

**Cost target:** US$0/month while both accounts remain on their free tiers and usage stays within their quotas. Vercel Hobby is intended for personal, non-commercial projects. Use this setup for a student/research demo, not a commercial or mission-critical service.

## 1. Create the Supabase database

1. Create a project on [Supabase](https://supabase.com/dashboard) and select the Free plan.
2. Choose a region near the expected audience if it is available (for India, Singapore is a reasonable first choice).
3. Save the database password somewhere private.
4. Open **SQL Editor** and run the complete contents of `supabase/migrations/20261114000100_emberlens_init.sql`.
   - The API also creates these tables idempotently on first use, but applying the migration explicitly makes the schema visible before deployment.
5. Open **Connect** and select **Transaction pooler**. Copy its connection string (normally port `6543`). Replace the password placeholder and URL-encode reserved characters in the password.
   - Do not use the direct connection string for this Vercel serverless deployment.
   - The backend enables SSL and disables prepared statements for Supabase's shared transaction pooler.

The connection string is a database secret. Never put it in `frontend/app.js`, HTML, a public repository, or a client-side environment variable.

## 2. Push the project to GitHub

Create a new repository for EmberLens and push the contents of this folder to its root. The root must contain at least:

- `app.py`
- `backend/`
- `frontend/`
- `requirements.txt`
- `vercel.json`

Do not commit `.env`, database passwords, API keys, `data/`, or SQLite files. `.gitignore` and `.vercelignore` already exclude the local database and common development artifacts.

## 3. Import the repository into Vercel

1. Open [Vercel New Project](https://vercel.com/new).
2. Import the EmberLens GitHub repository.
3. Keep the project root as the repository root. Vercel should detect the FastAPI app from the root `app.py` entrypoint.
4. Before deploying, add these Production environment variables:

| Variable | Value |
|---|---|
| `SUPABASE_DB_URL` | Supabase **Transaction pooler** connection string |
| `SUPABASE_URL` | Supabase project URL from Project Settings → API |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service-role key from Project Settings → API Keys (server-only) |
| `EMBERLENS_ENV` | `production` |
| `EMBERLENS_ADMIN_TOKEN` | A new random secret of at least 32 characters |

Optional variables:

| Variable | When needed |
|---|---|
| `FIRMS_MAP_KEY` | Server-side NASA FIRMS API imports and availability refresh |
| `METEOMATICS_USERNAME` | If the Meteomatics Global Offer is claimed and enabled |
| `METEOMATICS_PASSWORD` | If the Meteomatics Global Offer is claimed and enabled |

Generate an admin token locally with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Keep the generated value private. It is entered in the dashboard's **ADMIN TOKEN** field when using protected import/retrieval features. The browser sends it only as the `X-EmberLens-Admin` request header; it is not saved by the app.

**Credential hygiene:** a Meteomatics password was previously shared in chat. Rotate it with the provider before using Meteomatics on the public deployment, then enter only the replacement in Vercel Environment Variables. Do not reuse the exposed password.

5. Deploy. Vercel builds the Python runtime from `requirements.txt` and serves the FastAPI static frontend through the app's `StaticFiles` mount.

## 4. Verify the deployment

Replace `YOUR-VERCEL-DOMAIN` with the generated `.vercel.app` domain and open:

- `/api/health` — API process responds.
- `/api/health/ready` — Supabase PostgreSQL connection and schema respond.
- `/api/status` — version, data mode, provider-key readiness and deployment request limits.
- `/api/integrations/status` — optional provider configuration status (does not reveal credentials).

The dashboard should load at `/`. The first database-backed request creates the schema if it does not already exist.

## 5. Optional: add clearly labelled demo data

The seed data is simulated and must never be described as NASA observations. To seed the Supabase project from your computer, set the transaction-pooler URL in your local shell (not in source code) and run:

```bash
# macOS/Linux
export SUPABASE_DB_URL='your-private-transaction-pooler-url'
python scripts/seed_demo.py
```

```powershell
# Windows PowerShell
$env:SUPABASE_DB_URL = 'your-private-transaction-pooler-url'
python scripts/seed_demo.py
```

The script writes `simulated=1` records. Genuine imported FIRMS records take precedence in the dashboard.

## 6. Optional: import large historical FIRMS archives directly to Supabase

The browser CSV uploader supports **up to 50 MB on Vercel**. The browser first requests a short-lived signed upload token from the API, then sends the file directly to a private Supabase Storage bucket using the signed TUS endpoint (`/storage/v1/upload/resumable/sign`) with 6 MB chunks, progress updates and retry support; the file bytes do not pass through a Vercel Function. A signed standard-upload fallback is used if the TUS browser library is unavailable. The API validates and imports the object into PostgreSQL, then attempts to delete the temporary object. This avoids Vercel's 4.5 MB Function request-body limit.

The signed TUS route correction is included in **v0.8.3**. If an earlier deployment returns `Invalid Compact JWS`, deploy v0.8.3 or later and start a new upload so the app requests a fresh signed token. **v0.8.4** also corrects VIIRS brightness aliases used by FIRMS web-service exports; no database migration is needed for that parser fix.

Before using large browser uploads:

1. Apply `supabase/migrations/20261115000100_emberlens_csv_storage.sql` in the Supabase SQL Editor. It creates the private `emberlens-csv-imports` bucket with a 50,000,000-byte limit.
2. Add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` to the Vercel project's Environment Variables. Keep both server-side; never use the service-role key in browser JavaScript.
3. Confirm Supabase Storage's global file-size limit is set to 50 MB (the Free plan maximum).
4. Ensure **Fluid Compute** is enabled for the Vercel project (it is enabled by default for new projects). The project config allows up to 300 seconds for the FastAPI function on Vercel Hobby, which gives larger imports more time to finish.
5. Redeploy Vercel. `/api/status` should report `large_csv_upload_ready: true` and `max_csv_upload_bytes: 50000000`.

A single browser import is still limited to **200,000 CSV rows** by the FIRMS parser. Imports that exceed this row limit, or `.csv.gz` / `.zip` archive inputs, should use the local archive importer with `SUPABASE_DB_URL` set:

```bash
python scripts/import_archive.py --source MODIS_SP --input downloads/modis/
python scripts/import_archive.py --source VIIRS_SNPP_SP --input downloads/viirs-snpp/
```

The script streams archive rows in bounded batches into Supabase and records archive provenance. Match the source ID to the actual FIRMS product; do not mix different VIIRS platforms under one source.

## Free-tier operating limits and trade-offs

- **Vercel Hobby:** free for personal, non-commercial use. Functions have platform limits, including a 4.5 MB request/response payload cap. EmberLens routes browser CSV file bytes directly to Supabase Storage (up to 50 MB), caps one CSV import at 200,000 rows, CSV export at 20,000 rows, and a single FIRMS range-backfill request at five days on Vercel.
- **Supabase Free:** includes 500 MB database size per project and 5 GB egress. This is enough for a small demonstration dataset, but a large multi-year FIRMS archive can exceed the database quota. Monitor the project's usage page.
- **Supabase inactivity pause:** Free projects with too little database activity over a seven-day period may be paused. Data is retained and the project can be resumed from the dashboard, but the site will not be able to query it until resumed.
- **No production SLA:** free tiers are appropriate for a prototype/demo, not emergency response, guaranteed uptime, or critical operational workflows.
- **Environmental context:** on Vercel, NDVI retrieval is limited to three composites per request to keep the request bounded; local development can request up to eight.
- **Backfills:** on Vercel, submit at most five days per range request. Repeat the request for subsequent date windows. Large archives should be imported directly from a local machine using the archive script.

## Troubleshooting

- `SUPABASE_DB_URL` missing: add it to Vercel's Production environment and redeploy.
- `large_csv_upload_ready: false`: add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` to Vercel, apply the private Storage bucket migration, and redeploy.
- TUS returns `Invalid Compact JWS`: deploy v0.8.3 or later; the signed-upload token must be sent to `/storage/v1/upload/resumable/sign`, not the ordinary `/upload/resumable` route. Then retry with a newly generated upload ticket.
- `MODIS brightness fields found in a VIIRS import`: deploy v0.8.4 or later. NASA FIRMS web services use `Brightness` and `Brightness_2` as valid VIIRS I-4/I-5 aliases; `brightness` alone is not evidence of a MODIS file. The parser now rejects the MODIS-specific `bright_t31` field for VIIRS and uses row-level `instrument`/platform checks. No SQL migration is required.
- Supabase Storage rejects a file near 50 MB: check the global Storage file-size limit; Free projects cannot set it above 50 MB.
- Database authentication/connection failure: re-copy the **Transaction pooler** URL from Supabase Connect; check the password and percent-encode special characters.
- `Database is not ready`: confirm the Supabase project is active and the migration ran; inspect Vercel Function logs without printing the connection string.
- Imports return 503: set a valid `EMBERLENS_ADMIN_TOKEN` (at least 24 characters) in Vercel and redeploy.
- Meteomatics returns 503: set both backend credentials. A valid offer/account is still required; this integration is optional.
- Planetary Computer errors: retry later or shorten the requested period; the public provider can rate-limit or temporarily fail.

## What is and is not complete

The repository is prepared for Vercel + Supabase, and the local regression suite covers the SQLite path plus the PostgreSQL SQL/row adapter. A real Supabase project, Vercel project, live credentials, public URL, and live database/provider checks still have to be created and verified. Do not claim the app is online until the deployed health and readiness endpoints pass.
