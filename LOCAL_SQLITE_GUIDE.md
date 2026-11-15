# EmberLens local SQLite setup (Windows)

Your local EmberLens application already supports SQLite. This version adds a **resumable bulk importer** that accepts original NASA FIRMS CSV/ZIP files without the website's 50 MB / 200,000-row per-request limits. **No Supabase password or network connection is needed to import.**

## 1. Extract the project and arrange your NASA downloads

Example layout:

```text
C:\Users\imjim\Desktop\EmberLens\
  backend\
  scripts\bulk_import_sqlite.py
  start_local_sqlite.bat
  fire_archive_M-C61_850719.csv
  DL_FIRE_SV-C2_850722.zip
  DL_FIRE_J1V-C2_850720.zip
  DL_FIRE_J2V-C2_850721.zip
```

The data files do not have to be in the project directory; use their actual paths.

## 2. Open PowerShell in the project folder

You can type `powershell` in File Explorer's address bar, then run:

```powershell
py scripts/bulk_import_sqlite.py --dataset MODIS_SP "C:\Users\imjim\Desktop\EmberLens\fire_archive_M-C61_850719.csv"
```

For all four original archives in one command:

```powershell
py scripts/bulk_import_sqlite.py `
  --dataset MODIS_SP "C:\Users\imjim\Desktop\EmberLens\fire_archive_M-C61_850719.csv" `
  --dataset VIIRS_SNPP_SP "C:\Users\imjim\Desktop\EmberLens\DL_FIRE_SV-C2_850722.zip" `
  --dataset VIIRS_NOAA20_SP "C:\Users\imjim\Desktop\EmberLens\DL_FIRE_J1V-C2_850720.zip" `
  --dataset VIIRS_NOAA21_SP "C:\Users\imjim\Desktop\EmberLens\DL_FIRE_J2V-C2_850721.zip"
```

The database is created at `data\emberlens.sqlite`. SQLite is built into Python; no `pip install sqlite3` is needed. The importer commits every 2,000 rows, reports progress every 50,000 rows, and safely skips previously completed archive members on restart. The same normalized fingerprints prevent duplicate detections after an interrupted import.

## 3. Run EmberLens locally

Double-click `start_local_sqlite.bat` and visit <http://127.0.0.1:8000>. The launcher explicitly disables any Supabase DB setting in its own process. **Do not run the Vercel-hosted site to view local SQLite data**: the hosted site cannot reach the `.sqlite` file on your laptop.

Run the local server while importing only if needed; for best write throughput, import first then start the website.

## 4. Verify and back up

Run in PowerShell:

```powershell
py -c "import sqlite3; db=sqlite3.connect('data/emberlens.sqlite'); print(db.execute('SELECT source,COUNT(*) FROM detections GROUP BY source ORDER BY source').fetchall())"
py scripts/db_maintenance.py --integrity-check --optimize
py scripts/db_maintenance.py --backup backups/emberlens-backup.sqlite
```

Expect millions of records to occupy **considerably more disk space than compressed NASA ZIPs**. Have several GB free and monitor disk space. Never commit or publish the `.sqlite` database; it is excluded by `.gitignore`.

### Scientific limitations

FIRMS active-fire archives contain detected hotspots, not proof that every place/date was observed. This importer does not create fabricated coverage or query-window records. Harmonization and seasonal comparisons still require appropriate historical data and overlapping dates. The local application uses the same database schema and analysis routes as before, but this is **not** a deployment migration to Vercel: Vercel's ephemeral filesystem cannot host a persistent SQLite database.


## NASA FIRMS MAP_KEY in local development

Create a file named `.env` directly beside `start_local_sqlite.bat` (not
`backend/.env` or `.env.txt`) containing:

```dotenv
FIRMS_MAP_KEY=your_actual_nasa_firms_map_key
```

The local server now reads this file on startup. Close and restart the
launcher after editing `.env`. Do not commit your key. The existing operating
process must be restarted; a page refresh is not enough.

To check without exposing the key, visit `http://127.0.0.1:8000/api/profile`
and look for the `api_key_configured` field. If it is `false`, inspect the
`.env` filename and location and verify you started the updated server.

On Vercel, place `FIRMS_MAP_KEY` in project Environment Variables and redeploy.
The local `.env` on your PC does not configure Vercel.
