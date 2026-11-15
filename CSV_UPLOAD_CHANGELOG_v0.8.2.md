# EmberLens v0.8.2 — larger CSV uploads

## What changed

- Increased the hosted browser CSV upload ceiling from 4 MB to 50 MB when private Supabase Storage is configured.
- Added a private `emberlens-csv-imports` Storage bucket migration with a 50,000,000-byte per-object limit and CSV MIME allow-list.
- Added an admin-protected signed-upload ticket endpoint. The service-role key remains server-side; the browser receives only a short-lived, single-object signed upload token.
- Uses Supabase TUS resumable uploads with 6 MB chunks, progress reporting and retry delays; a signed standard-upload fallback is available if the TUS client script cannot load.
- Added a completion endpoint that retrieves the private object, validates the FIRMS CSV, imports the rows into PostgreSQL, and best-effort deletes the temporary Storage object.
- Changed the browser to upload file bytes directly to Supabase Storage on Vercel, bypassing Vercel's 4.5 MB Function request-body limit.
- Preserved the 4 MB direct-request fallback on Vercel and the 15 MB direct local upload route.
- Preserved the parser's 200,000-row cap. `.csv.gz` and `.zip` archive imports remain supported by the local streaming archive importer.
- Raised the configured Vercel function duration to 300 seconds (Hobby maximum with Fluid Compute) to give large imports more processing time.
- Added status reporting and clearer UI messaging when the large-upload Storage configuration is missing.

## Required deployment setup

1. Apply `supabase/migrations/20261115000100_emberlens_csv_storage.sql` in the Supabase SQL Editor.
2. Add `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` as server-only Vercel environment variables.
3. Ensure Supabase Storage's global file-size setting permits 50 MB (the Supabase Free plan maximum).
4. Redeploy and verify `/api/status` reports `large_csv_upload_ready: true` and `max_csv_upload_bytes: 50000000`.

## Verification

- Regression suite: 66 tests passed.
- Python compilation: passed.
- Frontend JavaScript syntax check: passed.
- The signed-URL, download and cleanup flows are covered with mocked tests; no live Supabase project or Vercel deployment was available for an end-to-end 50 MB transfer test.

## Limits and caveats

- 50 MB is the Storage file-size ceiling for Supabase Free, not a guarantee that every import completes within Vercel's configured function duration.
- A single browser CSV import is limited to 200,000 data rows. Larger historical archives should use `scripts/import_archive.py` from a local machine connected to Supabase PostgreSQL.
- If the bucket migration or server-only Storage credentials are missing, the app falls back to the 4 MB direct-upload limit on Vercel.
