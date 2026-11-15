# EmberLens — import original FIRMS ZIP archives into Supabase

Use this **on your Windows PC** (not in the Supabase SQL Editor, not in Vercel).
It imports the *original* CSV or ZIP files without EmberLens's HTTP upload limits.
It reuses EmberLens's FIRMS parser and detection fingerprints, commits batches of
2,000, skips completed archive members, and avoids duplicate detection rows after
an interrupted run. The importer does **not** fabricate coverage dates.

## 1. Prerequisites

- Install Python 3.11+ and run `pip install "psycopg[binary]>=3.2,<4"`.
- Have the *current EmberLens production database schema* installed in Supabase.
- In Supabase Dashboard > **Connect**, copy the **PostgreSQL connection URI**,
  preferably the **Session pooler** URI for a long-running import (direct connection
  is also fine when your network supports it). The application's existing
  transaction pooler URI also works with `prepare_threshold=None`.
- Make sure your Supabase database has enough remaining storage for millions
  of detection rows and indexes. Check project limits **before** ingesting.
- Do not publish the URI, paste it into the browser, or commit it to Git.

## 2. Download/unzip project

Place the original NASA files in `C:\FIRMS\`:

- `fire_archive_M-C61_850719.csv` (MODIS, if you have it)
- `DL_FIRE_SV-C2_850722.zip` (VIIRS S-NPP)
- `DL_FIRE_J1V-C2_850720.zip` (VIIRS NOAA-20)
- `DL_FIRE_J2V-C2_850721.zip` (VIIRS NOAA-21)

The script streams ZIP files *without extracting* them. You do not need to use
previously split archives.

## 3. PowerShell — from the EmberLens folder

```powershell
py -m pip install "psycopg[binary]>=3.2,<4"
$env:SUPABASE_DB_URL = Read-Host "Paste Supabase PostgreSQL connection URI"

py scripts/bulk_import_supabase.py `
  --dataset MODIS_SP "C:\FIRMS\fire_archive_M-C61_850719.csv" `
  --dataset VIIRS_SNPP_SP "C:\FIRMS\DL_FIRE_SV-C2_850722.zip" `
  --dataset VIIRS_NOAA20_SP "C:\FIRMS\DL_FIRE_J1V-C2_850720.zip" `
  --dataset VIIRS_NOAA21_SP "C:\FIRMS\DL_FIRE_J2V-C2_850721.zip"

Remove-Item Env:\SUPABASE_DB_URL
```

> If you already imported a dataset, running it again does not duplicate rows.
> If the connection fails, use the connection string under Supabase **Connect**
> matching your network and database password. Never use the REST API URL here.

## 4. Check results in Supabase SQL Editor

```sql
SELECT source, COUNT(*) AS detections,
       MIN(acq_date) AS first_date, MAX(acq_date) AS last_date
FROM public.detections
WHERE simulated = 0
GROUP BY source
ORDER BY source;

SELECT source, filename, rows_seen, rows_invalid, rows_added, imported_at
FROM public.archive_manifests
ORDER BY imported_at DESC
LIMIT 20;
```

## 5. Recovery and caveats

- If interrupted, run the **same command** again. Already committed rows are
  ignored via `fingerprint`; completed members are skipped via archive hash.
- The reported `rows_added` is *new* rows this attempt, not all valid rows
  already present in the table.
- An incomplete file may have inserted rows but has no complete manifest yet.
  Rerunning safely finishes it.
- A network failure after a transaction may leave rows committed; replay handles
  duplicates but could require rescanning the large original ZIP member.
- Importing 7 million records may incur database storage limits and take
  substantial time depending on Supabase plan/network. Check storage metrics.
- Historical FIRMS CSVs prove positive detections, **not** completeness of
  satellite observation coverage. No rows are added to `coverage` or
  `query_windows` because that would be scientifically misleading.
- For historical comparison, import the files as their corresponding `_SP`
  products, only when they are confirmed standard-processing archives.


## Configured project connection
The importer uses `db.dhqopulxjrlbuzhdjjyk.supabase.co:5432/postgres` with user `postgres` and TLS (`sslmode=require`). The password is prompted at runtime and not stored. You can optionally override with `SUPABASE_DB_URL`. If direct IPv6 access fails, use a Supabase Session Pooler URI via the environment override.
