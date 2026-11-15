"""EmberLens practice MVP API and single-page frontend.

Run: uvicorn backend.main:app --reload
"""
from __future__ import annotations
import os
import csv
import io
import asyncio
from contextlib import closing, contextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit, parse_qs
from threading import Lock
import sqlite3
import secrets
import uuid
import re

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from backend.core import (SOURCES, REGIONS, connect, days_inclusive, import_rows,
                          month_range, parse_csv, region_where)
from backend.postgres import connect_postgres, initialize_postgres_schema
from backend.coverage import report_coverage
from backend.change_explanation import analyze_change_explanation
from backend.harmonization import analyze_harmonization
from backend.anomalies import analyze_anomalies
from backend.integrations import (PC_COLLECTION, get_power_weather_context,
                                  get_weather_context, get_vegetation_context)

ROOT = Path(__file__).resolve().parent.parent
# Read the project-root .env before accessing configuration. Explicit environment
# variables (e.g. Vercel settings) always take precedence.
load_dotenv(dotenv_path=ROOT / ".env", override=False)
APP_VERSION = "0.8.6"
IS_VERCEL = os.getenv("VERCEL", "").strip() == "1"
APP_ENV = os.getenv("EMBERLENS_ENV", "production" if IS_VERCEL else "development").strip().lower()
PRODUCTION = APP_ENV == "production"
SUPABASE_DB_URL = (os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL") or "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
CSV_STORAGE_BUCKET = "emberlens-csv-imports"
MAX_CSV_UPLOAD_BYTES = 50_000_000  # Supabase Free global file-size ceiling.
DB_PATH = os.getenv("EMBERLENS_DB", str(ROOT / "data" / "emberlens.sqlite"))
_POSTGRES_SCHEMA_READY = False
_POSTGRES_SCHEMA_LOCK = Lock()

# Vercel's filesystem is ephemeral. Fail closed rather than silently creating a
# SQLite database that would lose imported observations after a cold start.
if IS_VERCEL and not SUPABASE_DB_URL:
    raise RuntimeError("Set SUPABASE_DB_URL to the Supabase transaction-pooler connection string")

if not SUPABASE_DB_URL:
    Path(DB_PATH).expanduser().parent.mkdir(parents=True, exist_ok=True)
    # Local development and the regression suite keep the original SQLite DB.
    with closing(connect(DB_PATH, initialize=True)):
        pass

app = FastAPI(
    title="EmberLens",
    version=APP_VERSION,
    description="NASA FIRMS active-fire observation explorer. Not an emergency alert system.",
    docs_url=None if PRODUCTION else "/docs",
    redoc_url=None if PRODUCTION else "/redoc",
    openapi_url=None if PRODUCTION else "/openapi.json",
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@contextmanager
def open_db():
    """Yield a request-scoped database connection and close it deterministically."""
    global _POSTGRES_SCHEMA_READY
    if SUPABASE_DB_URL:
        db = connect_postgres(SUPABASE_DB_URL)
    else:
        db = connect(DB_PATH, initialize=False)
    try:
        if SUPABASE_DB_URL and not _POSTGRES_SCHEMA_READY:
            with _POSTGRES_SCHEMA_LOCK:
                if not _POSTGRES_SCHEMA_READY:
                    initialize_postgres_schema(db)
                    _POSTGRES_SCHEMA_READY = True
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


@app.middleware("http")
async def security_and_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.endswith((".js", ".css")) or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache"
    return response


class RangeFetchSpec(BaseModel):
    region: str = 'punjab'
    source: str = 'MODIS_SP'
    start_date: date
    end_date: date
    force: bool = False


class FetchSpec(BaseModel):
    region: str = "punjab"
    source: str = "MODIS_SP"
    start_date: date
    days: int = 1


class CsvUploadTicketRequest(BaseModel):
    source: str
    filename: str
    size: int


class CsvUploadCompleteRequest(BaseModel):
    source: str
    filename: str
    path: str


class FreeContextImportSpec(BaseModel):
    region: str
    start_date: date
    end_date: date


def check_range(start: str, end: str):
    try:
        a, b = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if a > b or (b - a).days > 366*30:
        raise HTTPException(422, "Invalid date range (maximum 30 years)")


def filter_sql(region: str, start: str, end: str, sensor: str):
    if sensor not in ("ALL", "MODIS", "VIIRS"):
        raise HTTPException(422, "Invalid sensor")
    try:
        where, box_params = region_where(region)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    sql = f"{where} AND acq_date BETWEEN ? AND ?"
    params = [*box_params, start, end]
    if sensor != "ALL":
        sql += " AND sensor=?"
        params.append(sensor)
    # Never blend synthetic and real data. Real data takes precedence whenever present.
    return sql, params


def active_mode(db):
    """Prefer real observations; use simulated fixtures only when no real rows exist."""
    if db.execute("SELECT 1 FROM detections WHERE simulated=0 LIMIT 1").fetchone():
        return 0
    if db.execute("SELECT 1 FROM detections WHERE simulated=1 LIMIT 1").fetchone():
        return 1
    return 0


def active_label(db, mode: int | None = None) -> str:
    """Return 'real', 'simulated', or 'empty' without mislabelling an empty DB."""
    if mode is None:
        mode = active_mode(db)
    if mode:
        return 'simulated'
    return 'real' if db.execute("SELECT 1 FROM detections WHERE simulated=0 LIMIT 1").fetchone() else 'empty'


def stored_weather_context(region: str, start_date: date, end_date: date) -> dict | None:
    """Return a complete stored NASA POWER interval, never a silently partial one."""
    expected_days = (end_date - start_date).days + 1
    with open_db() as db:
        rows = [dict(row) for row in db.execute("""SELECT observation_date, temperature_c,
                relative_humidity_pct, wind_speed_m_s, wind_direction_deg, precipitation_mm
                FROM weather_daily WHERE region=? AND observation_date BETWEEN ? AND ?
                ORDER BY observation_date""", (region, start_date.isoformat(), end_date.isoformat()))]
    if len(rows) != expected_days:
        return None
    return {
        "provider": "NASA POWER Daily API",
        "region": region,
        "location": {"longitude": REGIONS[region]["center"][0], "latitude": REGIONS[region]["center"][1]},
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "time_step": "1 day",
        "parameters": ["t_2m:C", "relative_humidity_2m:p", "wind_speed_10m:ms", "wind_dir_10m:d", "precipitation:mm/day"],
        "observations": [{
            "datetime": f"{row['observation_date']}T00:00:00Z",
            "t_2m:C": row["temperature_c"],
            "relative_humidity_2m:p": row["relative_humidity_pct"],
            "wind_speed_10m:ms": row["wind_speed_m_s"],
            "wind_dir_10m:d": row["wind_direction_deg"],
            "precipitation:mm/day": row["precipitation_mm"],
        } for row in rows],
        "cached": True,
        "stored": True,
        "interpretation": "Stored NASA POWER daily regional-centroid context. It is not a fire-danger forecast or evidence of causation.",
    }


def stored_vegetation_context(region: str, start_date: date, end_date: date) -> dict | None:
    """Return stored NDVI summaries when a historical backfill has populated the interval."""
    with open_db() as db:
        rows = [dict(row) for row in db.execute("""SELECT item_id, observation_date, mean_ndvi,
                p10_ndvi, p90_ndvi, valid_percent, valid_pixels, min_ndvi, max_ndvi
                FROM ndvi_composites WHERE region=? AND observation_date BETWEEN ? AND ?
                ORDER BY observation_date""", (region, start_date.isoformat(), end_date.isoformat()))]
    if not rows:
        return None
    return {
        "provider": "Microsoft Planetary Computer",
        "collection": PC_COLLECTION,
        "dataset": "MODIS Vegetation Indices 16-Day (250 m), NDVI",
        "region": region,
        "bbox": REGIONS[region]["bbox"],
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "composites": [{
            "item_id": row["item_id"], "date": row["observation_date"],
            "datetime": f"{row['observation_date']}T00:00:00Z" if row["observation_date"] else None,
            "mean_ndvi": row["mean_ndvi"], "p10_ndvi": row["p10_ndvi"],
            "p90_ndvi": row["p90_ndvi"], "valid_percent": row["valid_percent"],
            "valid_pixels": row["valid_pixels"], "min_ndvi": row["min_ndvi"], "max_ndvi": row["max_ndvi"],
        } for row in rows],
        "statistics_max_size": 512,
        "cached": True,
        "stored": True,
        "interpretation": "Stored NDVI records summarize individual MODIS tiles intersecting the study region. They are not a complete regional mosaic, fuel-moisture measurement, burned-area estimate, or fire-risk score.",
    }


def store_weather_context(db, region: str, payload: dict) -> int:
    rows = [(region, item["datetime"][:10], item.get("t_2m:C"),
             item.get("relative_humidity_2m:p"), item.get("wind_speed_10m:ms"),
             item.get("wind_dir_10m:d"), item.get("precipitation:mm/day"), payload["provider"])
            for item in payload["observations"]]
    db.executemany("""INSERT INTO weather_daily(region,observation_date,temperature_c,
            relative_humidity_pct,wind_speed_m_s,wind_direction_deg,precipitation_mm,provider)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(region,observation_date) DO UPDATE SET
            temperature_c=excluded.temperature_c, relative_humidity_pct=excluded.relative_humidity_pct,
            wind_speed_m_s=excluded.wind_speed_m_s, wind_direction_deg=excluded.wind_direction_deg,
            precipitation_mm=excluded.precipitation_mm,
            provider=excluded.provider, imported_at=CURRENT_TIMESTAMP""", rows)
    return len(rows)


def store_vegetation_context(db, region: str, payload: dict) -> int:
    rows = [(region, item["item_id"], item.get("date"), item.get("mean_ndvi"),
             item.get("p10_ndvi"), item.get("p90_ndvi"), item.get("valid_percent"),
             item.get("valid_pixels"), item.get("min_ndvi"), item.get("max_ndvi"),
             payload["provider"]) for item in payload["composites"]]
    db.executemany("""INSERT INTO ndvi_composites(region,item_id,observation_date,mean_ndvi,
            p10_ndvi,p90_ndvi,valid_percent,valid_pixels,min_ndvi,max_ndvi,provider)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(region,item_id) DO UPDATE SET
            observation_date=excluded.observation_date, mean_ndvi=excluded.mean_ndvi,
            p10_ndvi=excluded.p10_ndvi, p90_ndvi=excluded.p90_ndvi,
            valid_percent=excluded.valid_percent, valid_pixels=excluded.valid_pixels,
            min_ndvi=excluded.min_ndvi, max_ndvi=excluded.max_ndvi,
            provider=excluded.provider, imported_at=CURRENT_TIMESTAMP""", rows)
    return len(rows)


def require_admin(request: Request) -> None:
    """Protect data-changing endpoints on public production deployments."""
    if not PRODUCTION:
        return
    expected = os.getenv('EMBERLENS_ADMIN_TOKEN', '')
    if len(expected) < 24:
        raise HTTPException(503, 'Production data imports are disabled until EMBERLENS_ADMIN_TOKEN is configured')
    supplied = request.headers.get('x-emberlens-admin', '')
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, 'Administrator token required for data-changing operations')


@app.get('/api/health')
@app.get('/api/health/live')
def health():
    """Liveness: confirms the ASGI process can answer requests."""
    return {"status": "ok", "version": APP_VERSION, "environment": APP_ENV}


@app.get('/api/health/ready')
def readiness():
    """Readiness: confirms the configured database can be queried."""
    try:
        with open_db() as db:
            db.execute("SELECT 1").fetchone()
            if getattr(db, "is_postgres", False):
                journal_mode = "postgresql"
                database_bytes = None
            else:
                journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]
                db_file = Path(DB_PATH)
                database_bytes = db_file.stat().st_size if db_file.exists() else 0
        return {"status": "ready", "database": "ok", "journal_mode": journal_mode,
                "database_bytes": database_bytes, "version": APP_VERSION}
    except Exception:
        raise HTTPException(503, "Database is not ready") from None


@app.get('/api/integrations/status')
def integrations_status():
    """Expose provider readiness without revealing credentials."""
    return {
        "meteomatics_configured": bool(os.getenv('METEOMATICS_USERNAME') and os.getenv('METEOMATICS_PASSWORD')),
        "nasa_power_available": True,
        "planetary_computer_available": True,
        "planetary_computer_auth_required": False,
        "weather_provider": "NASA POWER Daily API",
        "vegetation_provider": "Microsoft Planetary Computer · MODIS 13Q1.061",
        "free_context_backfill": {"start": "2020-01-01", "end": "2024-12-31", "regions": ["punjab", "california", "amazon"]},
    }


@app.get('/api/context/weather')
async def weather_context(
    region: str = 'punjab',
    start_date: date = Query(...),
    end_date: date = Query(...),
    provider: str = 'power',
):
    """Return free NASA POWER daily context or the optional Meteomatics live integration."""
    if region not in REGIONS or region == 'all':
        raise HTTPException(422, 'Choose a bounded study region')
    days = (end_date - start_date).days + 1
    if provider not in ('power', 'meteomatics'):
        raise HTTPException(422, 'Unsupported weather provider')
    if days < 1 or end_date > date.today():
        raise HTTPException(422, 'Choose a valid past interval')
    if provider == 'power':
        if days > 3660:
            raise HTTPException(422, 'NASA POWER requests are limited to ten years at a time')
        stored = stored_weather_context(region, start_date, end_date)
        if stored is not None:
            return stored
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0),
                follow_redirects=False,
                headers={'User-Agent': f'EmberLens/{APP_VERSION} research-prototype'},
            ) as client:
                return await get_power_weather_context(
                    client, region=region, region_meta=REGIONS[region],
                    start_date=start_date, end_date=end_date,
                )
        except httpx.HTTPStatusError as exc:
            detail = 'NASA POWER rate limit reached. Wait before retrying.' if exc.response.status_code == 429 else f'NASA POWER request failed (HTTP {exc.response.status_code}).'
            raise HTTPException(502, detail) from None
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, 'NASA POWER weather context is temporarily unavailable.') from None

    if days > 14:
        raise HTTPException(422, 'Meteomatics requests are limited to 14 days; use NASA POWER for free historical context')
    username = os.getenv('METEOMATICS_USERNAME', '').strip()
    password = os.getenv('METEOMATICS_PASSWORD', '')
    if not username or not password:
        raise HTTPException(503, 'Meteomatics is not configured. Set METEOMATICS_USERNAME and METEOMATICS_PASSWORD on the backend.')
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=10.0),
            follow_redirects=False,
            headers={'User-Agent': f'EmberLens/{APP_VERSION} research-prototype'},
        ) as client:
            return await get_weather_context(
                client, region=region, region_meta=REGIONS[region],
                start_date=start_date, end_date=end_date,
                username=username, password=password,
            )
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            detail = 'Meteomatics rejected the credentials or this offer does not include the requested data access.'
        elif code == 429:
            detail = 'Meteomatics rate limit reached. Wait before trying again.'
        else:
            detail = f'Meteomatics request failed (HTTP {code}); check date availability and offer permissions.'
        raise HTTPException(502, detail) from None
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'Meteomatics weather context is temporarily unavailable for this period.') from None


@app.get('/api/context/vegetation')
async def vegetation_context(
    region: str = 'punjab',
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """Optional MODIS NDVI context via the public Planetary Computer STAC/Data APIs."""
    if region not in REGIONS or region == 'all':
        raise HTTPException(422, 'Choose a bounded study region')
    days = (end_date - start_date).days + 1
    if days < 1 or days > 3660:
        raise HTTPException(422, 'Choose a date interval of up to ten years')
    stored = stored_vegetation_context(region, start_date, end_date)
    if stored is not None:
        return stored
    if days > 120:
        raise HTTPException(422, 'Backfill this historical interval first, or choose a 1–120 day live request')
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0 if IS_VERCEL else 60.0, connect=8.0 if IS_VERCEL else 10.0),
            follow_redirects=False,
            headers={'User-Agent': f'EmberLens/{APP_VERSION} research-prototype'},
        ) as client:
            request = get_vegetation_context(
                client, region=region, region_meta=REGIONS[region],
                start_date=start_date, end_date=end_date,
                max_composites=3 if IS_VERCEL else 8,
            )
            return await asyncio.wait_for(request, timeout=50.0) if IS_VERCEL else await request
    except asyncio.TimeoutError:
        raise HTTPException(504, 'Planetary Computer request exceeded the hosted function time budget. Shorten the date range and retry.') from None
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 429:
            detail = 'Planetary Computer rate limit reached. Wait before trying again.'
        else:
            detail = f'Planetary Computer request failed (HTTP {code}). Try again later or shorten the date range.'
        raise HTTPException(502, detail) from None
    except httpx.HTTPError:
        raise HTTPException(502, 'Planetary Computer vegetation data is temporarily unavailable.') from None


@app.post('/api/import/free-context')
async def import_free_context(spec: FreeContextImportSpec, request: Request):
    """Persist one bounded, free NASA POWER and Planetary Computer context batch."""
    require_admin(request)
    if spec.region not in REGIONS or spec.region == 'all':
        raise HTTPException(422, 'Choose a bounded study region')
    days = (spec.end_date - spec.start_date).days + 1
    if days < 1 or days > 93 or spec.end_date > date.today():
        raise HTTPException(422, 'Choose a past interval of 1–93 days per free-context batch')
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0 if IS_VERCEL else 60.0, connect=8.0 if IS_VERCEL else 10.0),
            follow_redirects=False,
            headers={'User-Agent': f'EmberLens/{APP_VERSION} research-prototype'},
        ) as client:
            weather = await get_power_weather_context(
                client, region=spec.region, region_meta=REGIONS[spec.region],
                start_date=spec.start_date, end_date=spec.end_date,
            )
            vegetation_request = get_vegetation_context(
                client, region=spec.region, region_meta=REGIONS[spec.region],
                start_date=spec.start_date, end_date=spec.end_date,
                max_composites=8,
                stats_delay_seconds=0.15,
            )
            vegetation = await asyncio.wait_for(vegetation_request, timeout=50.0) if IS_VERCEL else await vegetation_request
    except asyncio.TimeoutError:
        raise HTTPException(504, 'This free-context batch timed out. Retry a shorter date range.') from None
    except httpx.HTTPStatusError as exc:
        detail = 'Provider rate limit reached. Wait before retrying.' if exc.response.status_code == 429 else f'Free context provider failed (HTTP {exc.response.status_code}).'
        raise HTTPException(502, detail) from None
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'A free-context provider is temporarily unavailable. Retry this same batch later.') from None
    with open_db() as db:
        weather_rows = store_weather_context(db, spec.region, weather)
        vegetation_rows = store_vegetation_context(db, spec.region, vegetation)
    return {
        "status": "completed", "region": spec.region,
        "start": spec.start_date.isoformat(), "end": spec.end_date.isoformat(),
        "weather_rows": weather_rows, "vegetation_rows": vegetation_rows,
        "providers": [weather["provider"], vegetation["provider"]],
        "note": "Rows are regional context, not local weather stations, fire perimeters, or fire-risk predictions.",
    }


@app.get('/api/regions')
def regions():
    return [{"id": k, **v} for k,v in REGIONS.items()]


def supabase_storage_config() -> tuple[str, dict[str, str]]:
    """Return the private Storage API base URL and server-only credentials."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(
            503,
            "Large CSV uploads need SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY configured on the server.",
        )
    if not SUPABASE_URL.startswith("https://"):
        raise HTTPException(503, "SUPABASE_URL must be an HTTPS project URL")
    base = f"{SUPABASE_URL}/storage/v1"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }
    return base, headers


def clean_csv_filename(filename: str) -> str:
    """Keep a display filename only; never use it as the Storage object path."""
    name = str(filename or "uploaded.csv").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in "\r\n\t")
    name = name.strip()[:120] or "uploaded.csv"
    if not name.lower().endswith(".csv"):
        raise HTTPException(422, "CSV extension required")
    return name


async def create_supabase_signed_upload_url(path: str) -> dict[str, str]:
    """Create a two-hour signed upload token for direct/resumable Storage uploads."""
    base, headers = supabase_storage_config()
    endpoint = f"{base}/object/upload/sign/{CSV_STORAGE_BUCKET}/{quote(path, safe='/')}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
            response = await client.post(endpoint, headers=headers, json={})
        if response.status_code >= 400:
            raise HTTPException(502, "Supabase Storage could not create an upload URL. Check that the CSV bucket migration has been applied.")
        try:
            payload = response.json()
        except ValueError:
            raise HTTPException(502, "Supabase Storage returned an unreadable signed upload response") from None
        signed_path = payload.get("url") or payload.get("signedURL")
        if not isinstance(signed_path, str) or not signed_path:
            raise HTTPException(502, "Supabase Storage returned an invalid signed upload URL")
        signed_url = signed_path if signed_path.startswith("https://") else f"{base}{signed_path if signed_path.startswith('/') else '/' + signed_path}"
        token = (parse_qs(urlsplit(signed_url).query).get("token") or [payload.get("token")])[0]
        if not isinstance(token, str) or not token:
            raise HTTPException(502, "Supabase Storage did not return a usable signed upload token")

        # Supabase recommends its direct Storage hostname for resumable uploads.
        parsed = urlsplit(SUPABASE_URL)
        host = parsed.hostname or ""
        if host.endswith(".supabase.co"):
            project_ref = host[:-len(".supabase.co")]
            storage_origin = f"{parsed.scheme}://{project_ref}.storage.supabase.co"
        else:
            storage_origin = SUPABASE_URL
        tus_endpoint = f"{storage_origin}/storage/v1/upload/resumable/sign"
        return {"upload_url": signed_url, "upload_token": token, "tus_endpoint": tus_endpoint}
    except httpx.HTTPError:
        raise HTTPException(502, "Supabase Storage is temporarily unavailable while creating the upload URL") from None


async def download_supabase_csv(path: str) -> bytes:
    """Download a private pending CSV using server-side Storage credentials."""
    base, headers = supabase_storage_config()
    endpoint = f"{base}/object/authenticated/{CSV_STORAGE_BUCKET}/{quote(path, safe='/')}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
            response = await client.get(endpoint, headers=headers)
        if response.status_code == 404:
            raise HTTPException(404, "Uploaded CSV was not found in private Supabase Storage")
        if response.status_code >= 400:
            raise HTTPException(502, "Supabase Storage could not retrieve the uploaded CSV")
        raw = response.content
        if len(raw) > MAX_CSV_UPLOAD_BYTES:
            raise HTTPException(413, "CSV exceeds the 50 MB upload limit")
        return raw
    except httpx.HTTPError:
        raise HTTPException(502, "Supabase Storage is temporarily unavailable while retrieving the CSV") from None


async def delete_supabase_csv(path: str) -> None:
    """Best-effort cleanup so completed imports do not consume Storage quota."""
    base, headers = supabase_storage_config()
    endpoint = f"{base}/object/{CSV_STORAGE_BUCKET}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
            await client.request("DELETE", endpoint, headers=headers, json={"prefixes": [path]})
    except httpx.HTTPError:
        # The import result is more important than best-effort temporary-file cleanup.
        return


@app.get('/api/status')
def status():
    with open_db() as db:
        real = db.execute("SELECT COUNT(*) FROM detections WHERE simulated=0").fetchone()[0]
        simulated = db.execute("SELECT COUNT(*) FROM detections WHERE simulated=1").fetchone()[0]
        mode = active_mode(db)
        by_sensor = [dict(r) for r in db.execute("SELECT sensor,source,COUNT(*) AS count,MIN(acq_date) AS first_date,MAX(acq_date) AS last_date FROM detections WHERE simulated=? GROUP BY sensor,source ORDER BY sensor,source",(mode,))]
        return {"mode": "real" if real else ("simulated" if simulated else "empty"), "real_rows": real,
                "simulated_rows": simulated, "by_sensor": by_sensor,
                "api_key_configured": bool(os.getenv('FIRMS_MAP_KEY')),
                "admin_required": PRODUCTION,
                "admin_token_configured": len(os.getenv('EMBERLENS_ADMIN_TOKEN', '')) >= 24,
                "docs_enabled": not PRODUCTION, "version": APP_VERSION,
                "deployment_platform": "vercel" if IS_VERCEL else "self_hosted",
                "large_csv_upload_ready": bool(SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY),
                "max_csv_upload_bytes": (MAX_CSV_UPLOAD_BYTES if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY else 4_000_000) if IS_VERCEL else 15_000_000,
                "max_backfill_days": 5 if IS_VERCEL else 90,
                "max_export_rows": 20_000 if IS_VERCEL else 100_000,
                "max_vegetation_composites": 3 if IS_VERCEL else 8}


@app.get('/api/profile')
def data_profile():
    """Compact provenance profile for the currently active real/practice dataset."""
    with open_db() as db:
        mode = active_mode(db)
        rows = [dict(r) for r in db.execute("""SELECT source,sensor,COUNT(*) AS observations,
            COUNT(DISTINCT acq_date) AS positive_days,MIN(acq_date) AS first_detection,
            MAX(acq_date) AS last_detection,
            SUM(CASE WHEN frp IS NOT NULL THEN 1 ELSE 0 END) AS records_with_frp
            FROM detections WHERE simulated=? GROUP BY source,sensor ORDER BY source""", (mode,))]
        total = sum(r['observations'] for r in rows)
        label = active_label(db, mode)
    return {"data_mode": label, "total_observations": total,
            "sources": rows,
            "interpretation": "Profile of loaded positive hotspot records only. It does not establish satellite acquisition coverage, unique fires, or burned area."}


@app.get('/api/calendar')
def calendar(region: str = 'punjab', start: str = '2020-01-01', end: str = '2026-12-31', sensor: str = 'ALL'):
    check_range(start,end)
    where,params = filter_sql(region,start,end,sensor)
    with open_db() as db:
        mode = active_mode(db)
        data_mode = active_label(db, mode)
        frp_sum = ("ROUND(SUM(COALESCE(frp,0))::numeric,2)" if getattr(db, "is_postgres", False)
                   else "ROUND(SUM(COALESCE(frp,0)),2)")
        rows = [dict(r) for r in db.execute(f"""SELECT substr(acq_date,1,7) AS month,sensor,COUNT(*) AS detections,
                COUNT(DISTINCT acq_date) AS active_days,{frp_sum} AS frp_total
                FROM detections WHERE {where} AND simulated=?
                GROUP BY month,sensor ORDER BY month,sensor""", [*params,mode])]
        # Legacy coverage table was not region-specific. Never use it for coverage claims.
        dates = []
    return {"rows": rows, "coverage": dates, "region":region,"start":start,"end":end,
            "data_mode":data_mode, "note":"No zero-detection inference is made from imported hotspot-only CSV files. Calendar blanks mean unavailable or not established, not necessarily zero fires."}


@app.get('/api/summary')
def summary(region: str = 'punjab', start: str = '2020-01-01', end: str = '2026-12-31', sensor: str = 'ALL'):
    check_range(start,end)
    where,params = filter_sql(region,start,end,sensor)
    with open_db() as db:
        mode = active_mode(db)
        data_mode = active_label(db, mode)
        total = db.execute(f"SELECT COUNT(*) n, COUNT(DISTINCT acq_date) d, COALESCE(SUM(frp),0) frp FROM detections WHERE {where} AND simulated=?", [*params,mode]).fetchone()
        by_sensor = [dict(r) for r in db.execute(f"SELECT sensor,COUNT(*) AS detections,COUNT(DISTINCT acq_date) AS days FROM detections WHERE {where} AND simulated=? GROUP BY sensor",[*params,mode])]
    return {"total_detections":total['n'],"observed_positive_days":total['d'],
            "total_reported_frp":round(total['frp'],2),"by_sensor":by_sensor,
            "data_mode":data_mode}


@app.get('/api/daily-readings')
def daily_readings(region: str = 'punjab', start: str = '2020-01-01',
                   end: str = '2026-12-31', sensor: str = 'ALL'):
    """Return observed daily MODIS and VIIRS detection totals without filling missing dates."""
    check_range(start, end)
    where, params = filter_sql(region, start, end, sensor)
    with open_db() as db:
        mode = active_mode(db)
        rows = [dict(row) for row in db.execute(f"""SELECT acq_date AS date, sensor,
                COUNT(*) AS detections FROM detections WHERE {where} AND simulated=?
                GROUP BY acq_date, sensor ORDER BY acq_date, sensor""", [*params, mode])]
        data_mode = active_label(db, mode)
    return {"rows": rows, "region": region, "start": start, "end": end,
            "sensor": sensor, "data_mode": data_mode,
            "note": "Each point is the number of loaded active-fire detection records on that date. Dates without a point are not inferred to be zero."}


@app.get('/api/change-explanation')
def change_explanation(region: str = 'punjab', before_start: str = '2020-01-01',
                       before_end: str = '2020-12-31', after_start: str = '2021-01-01',
                       after_end: str = '2021-12-31', sensor: str = 'ALL'):
    """Explain whether a period-to-period count change survives a source match."""
    check_range(before_start, before_end)
    check_range(after_start, after_end)
    try:
        with open_db() as db:
            mode = active_mode(db)
            result = analyze_change_explanation(
                db, region, before_start, before_end, after_start, after_end, mode, sensor)
            result['data_mode'] = active_label(db, mode)
            return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/api/points')
def points(region: str = 'punjab', start: str = '2020-01-01', end: str = '2026-12-31', sensor: str = 'ALL', limit: int = Query(1200,ge=1,le=4000)):
    check_range(start,end)
    where,params = filter_sql(region,start,end,sensor)
    with open_db() as db:
        mode = active_mode(db)
        data_mode = active_label(db, mode)
        rows = [dict(r) for r in db.execute(f"SELECT sensor,source,acq_date,latitude,longitude,frp,confidence,satellite FROM detections WHERE {where} AND simulated=? ORDER BY acq_date DESC LIMIT ?",[*params,mode,limit])]
        total = db.execute(f"SELECT COUNT(*) FROM detections WHERE {where} AND simulated=?",[*params,mode]).fetchone()[0]
    return {"points":rows,"total":total,"returned":len(rows),"truncated":total>len(rows),
            "data_mode":data_mode}


@app.get('/api/export/detections.csv')
def export_detections(region: str = 'punjab', start: str = '2020-01-01',
                      end: str = '2026-12-31', sensor: str = 'ALL',
                      limit: int = Query(10000 if IS_VERCEL else 50000, ge=1,
                                          le=20000 if IS_VERCEL else 100000)):
    """Stream a bounded, filter-matched CSV export without materializing all rows."""
    check_range(start, end)
    where, params = filter_sql(region, start, end, sensor)
    mode = 1
    with open_db() as db:
        mode = active_mode(db)
        mode_name = active_label(db, mode)
    sql = f"""SELECT sensor,source,acq_date,acq_time,latitude,longitude,satellite,
               confidence,frp,version FROM detections
               WHERE {where} AND simulated=? ORDER BY acq_date,acq_time LIMIT ?"""

    def generate():
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator='\n')
        writer.writerow(['sensor','source','acq_date','acq_time','latitude','longitude',
                         'satellite','confidence','frp','version'])
        yield buffer.getvalue()
        with open_db() as db:
            cursor = db.execute(sql, [*params, mode, limit])
            while True:
                batch = cursor.fetchmany(1000)
                if not batch:
                    break
                for row in batch:
                    buffer.seek(0); buffer.truncate(0)
                    writer.writerow(tuple(row))
                    yield buffer.getvalue()

    headers = {"Content-Disposition": 'attachment; filename="emberlens-observations.csv"',
               "X-Data-Mode": mode_name,
               "X-Export-Limit": str(limit)}
    return StreamingResponse(generate(), media_type='text/csv; charset=utf-8', headers=headers)


@app.get('/api/month-detail')
def detail(region: str = 'punjab', month: str = '2025-10'):
    try:
        d = date.fromisoformat(month+'-01')
    except ValueError:
        raise HTTPException(422, 'Invalid month (YYYY-MM)')
    where,params = filter_sql(region,month+'-01',month+'-31','ALL')
    with open_db() as db:
        mode = active_mode(db)
        current = [dict(r) for r in db.execute(f"SELECT sensor,COUNT(*) n FROM detections WHERE {where} AND simulated=? GROUP BY sensor",[*params,mode])]
        # Historic comparison only within the same calendar month; do not invent missing month zeroes.
        historical_where,bp=region_where(region)
        baseline = [dict(r) for r in db.execute(f"""SELECT sensor,substr(acq_date,1,4) AS year,COUNT(*) n
              FROM detections WHERE {historical_where} AND substr(acq_date,6,2)=? AND substr(acq_date,1,4)<>?
              AND simulated=? GROUP BY sensor,year ORDER BY year""", [*bp,month[-2:],month[:4],mode])]
    bysensor = {}
    import statistics
    for s in ('MODIS','VIIRS'):
        values = [r['n'] for r in baseline if r['sensor']==s]
        n = next((r['n'] for r in current if r['sensor']==s),0)
        # Baselines from positive-only records are not scientifically valid activity baselines.
        bysensor[s] = {"detections":n,"historical_positive_months":len(values),
                       "median_of_positive_months":statistics.median(values) if values else None,
                       "historical_min":min(values) if values else None,
                       "historical_max":max(values) if values else None}
    return {"month":month,"by_sensor":bysensor,"warning":"Historical statistics exclude absent months, which may be missing data or no detections. No anomaly significance is asserted."}


@app.get('/api/harmonization')
def harmonization(region: str = 'punjab', start: str = '2020-01-01',
                  end: str = '2026-12-31', grid_km: float = Query(10,ge=5,le=50),
                  viirs_source: str = 'VIIRS_SNPP_SP'):
    """Compare MODIS-SP with one selected VIIRS-SP platform; NRT is excluded."""
    check_range(start,end)
    try:
        with open_db() as db:
            mode = active_mode(db)
            result = analyze_harmonization(db, region, start, end, mode, grid_km, viirs_source)
            result['data_mode'] = active_label(db, mode)
            return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/api/anomalies')
def anomalies(region: str = 'punjab', start: str = '2020-01-01',
              end: str = '2026-12-31', grid_km: float = Query(10, ge=5, le=50),
              sensor: str = 'VIIRS', threshold: float = Query(2.5, ge=1.5, le=5),
              viirs_source: str = 'VIIRS_SNPP_SP',
              analysis_mode: str = 'strict'):
    """Retrospective seasonal flags; never a wildfire alert or prediction."""
    check_range(start, end)
    try:
        with open_db() as db:
            mode = active_mode(db)
            result = analyze_anomalies(db, region, start, end, mode, grid_km, sensor, threshold, viirs_source, analysis_mode)
            result['data_mode'] = active_label(db, mode)
            return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/import/csv/upload-url')
async def create_csv_upload_ticket(payload: CsvUploadTicketRequest, request: Request):
    """Issue a short-lived signed URL for a direct browser-to-Supabase upload."""
    require_admin(request)
    if payload.source not in SOURCES:
        raise HTTPException(422, "Invalid source")
    clean_csv_filename(payload.filename)
    if payload.size < 1 or payload.size > MAX_CSV_UPLOAD_BYTES:
        raise HTTPException(413, "CSV upload limit is 50 MB")
    # Storage configuration is checked before returning a ticket. The service
    # role key is used only here and is never returned to the browser.
    supabase_storage_config()
    path = f"pending/{uuid.uuid4().hex}.csv"
    upload = await create_supabase_signed_upload_url(path)
    return {"path": path, **upload, "bucket": CSV_STORAGE_BUCKET,
            "max_bytes": MAX_CSV_UPLOAD_BYTES, "expires_in_seconds": 7200}


@app.post('/api/import/csv/complete')
async def complete_csv_upload(payload: CsvUploadCompleteRequest, request: Request):
    """Validate and import a CSV that was uploaded directly to private Storage."""
    require_admin(request)
    if payload.source not in SOURCES:
        raise HTTPException(422, "Invalid source")
    filename = clean_csv_filename(payload.filename)
    if not re.fullmatch(r"pending/[0-9a-f]{32}\.csv", payload.path or ""):
        raise HTTPException(422, "Invalid temporary upload path")

    try:
        raw = await download_supabase_csv(payload.path)
        if len(raw) > MAX_CSV_UPLOAD_BYTES:
            raise HTTPException(413, "CSV exceeds the 50 MB upload limit")
        try:
            rows, stats = parse_csv(raw.decode("utf-8-sig"), payload.source)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        with open_db() as db:
            result = import_rows(db, rows, payload.source, filename, stats=stats)
        return result
    finally:
        await delete_supabase_csv(payload.path)


@app.post('/api/import/csv')
async def upload_csv(request: Request, source: str = 'MODIS_SP', filename: str = 'uploaded.csv'):
    """Small direct-body upload retained for local use and Vercel fallback."""
    require_admin(request)
    if source not in SOURCES:
        raise HTTPException(422,"Invalid source")
    filename = clean_csv_filename(filename)
    max_bytes = 4_000_000 if IS_VERCEL else 15_000_000
    content_length = request.headers.get('content-length')
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(413, f"Maximum direct CSV size is {max_bytes // 1_000_000} MB")
        except ValueError:
            raise HTTPException(400, "Invalid Content-Length header") from None
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(413, f"Maximum direct CSV size is {max_bytes // 1_000_000} MB")
        chunks.append(chunk)
    raw = b''.join(chunks)
    try:
        rows,stats=parse_csv(raw.decode('utf-8-sig'),source)
    except (ValueError,UnicodeError) as e:
        raise HTTPException(422,str(e)) from e
    with open_db() as db:
        result=import_rows(db,rows,source,filename,stats=stats)
    return result


@app.get('/api/coverage')
def coverage(region: str = 'punjab', start: str = '2020-01-01', end: str = '2026-12-31'):
    check_range(start, end)
    if region not in REGIONS:
        raise HTTPException(422, 'Invalid region')
    with open_db() as db:
        return report_coverage(db, region, start, end, active_mode(db))


async def fetch_csv_window(client: httpx.AsyncClient, key: str, region: str,
                           source: str, first: date, last: date) -> tuple[list, dict]:
    days = (last - first).days + 1
    if not 1 <= days <= 5:
        raise ValueError('API window exceeds five days')
    bbox = ','.join(map(str, REGIONS[region]['bbox']))
    url = (f'https://firms.modaps.eosdis.nasa.gov/api/area/csv/'
           f'{quote(key, safe="")}/{source}/{bbox}/{days}/{first.isoformat()}')
    # Bounded retries for transient 429 and 5xx responses, no key exposed to client.
    attempts = 2 if IS_VERCEL else 3
    for attempt in range(attempts):
        try:
            response = await client.get(url)
            response.raise_for_status()
            return parse_csv(response.text, source)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise ValueError(f'FIRMS HTTP {exc.response.status_code}') from None
        except httpx.HTTPError:
            if attempt == attempts - 1:
                raise ValueError('FIRMS network error') from None
        if attempt < attempts - 1:
            await asyncio.sleep(0.3 * (attempt + 1))
    raise ValueError('FIRMS retrieval failed')


@app.post('/api/availability/refresh')
async def refresh_availability(request: Request):
    require_admin(request)
    key = os.getenv('FIRMS_MAP_KEY')
    if not key:
        raise HTTPException(400, 'Set FIRMS_MAP_KEY on the backend')
    url = 'https://firms.modaps.eosdis.nasa.gov/api/data_availability/csv/' + quote(key, safe='') + '/all'
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(22.0 if IS_VERCEL else 35.0, connect=8.0 if IS_VERCEL else 10.0), follow_redirects=False, headers={'User-Agent': f'EmberLens/{APP_VERSION} research-prototype'}) as client:
            response = await client.get(url)
            response.raise_for_status()
        reader = csv.DictReader(io.StringIO(response.text.lstrip('\ufeff')))
        if not reader.fieldnames or not {'data_id', 'min_date', 'max_date'}.issubset(reader.fieldnames):
            raise ValueError('Unexpected availability response')
        rows = []
        for item in reader:
            source = item.get('data_id', '').upper()
            if source not in SOURCES:
                continue
            first, last = date.fromisoformat(item['min_date']), date.fromisoformat(item['max_date'])
            if first > last:
                raise ValueError('Invalid advertised date range')
            rows.append((source, first.isoformat(), last.isoformat()))
        if not rows:
            raise ValueError('No supported datasets reported')
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise HTTPException(502, 'Could not retrieve valid NASA dataset availability') from None
    with open_db() as db:
        with db:
            db.executemany('''INSERT INTO product_availability(source,min_date,max_date)
                VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET
                min_date=excluded.min_date,max_date=excluded.max_date,
                refreshed_at=CURRENT_TIMESTAMP''', rows)
    return {'datasets_updated': len(rows), 'availability': [dict(source=s,min_date=a,max_date=b) for s,a,b in rows],
            'warning': 'Advertised product dates are not confirmation of full observation coverage in any region.'}


@app.post('/api/import/firms')
async def import_firms(spec: FetchSpec, request: Request):
    require_admin(request)
    try:
        dates = days_inclusive(spec.start_date, spec.days)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if spec.region not in REGIONS or spec.region == 'all' or spec.source not in SOURCES:
        raise HTTPException(422, 'Choose a bounded region and supported source')
    key = os.getenv('FIRMS_MAP_KEY')
    if not key:
        raise HTTPException(400, 'Set FIRMS_MAP_KEY on the backend, or use archive/CSV import')
    if spec.start_date + timedelta(days=spec.days - 1) > date.today():
        raise HTTPException(422, 'Request dates cannot extend into the future')
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(22.0 if IS_VERCEL else 35.0, connect=8.0 if IS_VERCEL else 10.0), follow_redirects=False, headers={'User-Agent': f'EmberLens/{APP_VERSION} research-prototype'}) as client:
            rows, stats = await fetch_csv_window(client, key, spec.region, spec.source,
                                                 spec.start_date, date.fromisoformat(dates[-1]))
    except ValueError as e:
        raise HTTPException(502, str(e)) from None
    with open_db() as db:
        result = import_rows(db, rows, spec.source, 'FIRMS area API',
                             stats=stats, query_window=(spec.region, spec.start_date,
                                                        date.fromisoformat(dates[-1])))
    return {**result, 'retrieval_days': len(dates), 'coverage_claim': 'api_retrieval_only'}


@app.post('/api/import/firms-range')
async def import_firms_range(spec: RangeFetchSpec, request: Request):
    """Resumable backfill; serverless deployments cap a call at one five-day FIRMS window."""
    require_admin(request)
    if spec.region not in REGIONS or spec.region == 'all' or spec.source not in SOURCES:
        raise HTTPException(422, 'Choose a bounded region and supported source')
    days = (spec.end_date - spec.start_date).days + 1
    max_days = 5 if IS_VERCEL else 90
    if days < 1 or days > max_days or spec.end_date > date.today():
        raise HTTPException(422, f'Choose a past date interval of 1–{max_days} days')
    key = os.getenv('FIRMS_MAP_KEY')
    if not key:
        raise HTTPException(400, 'Set FIRMS_MAP_KEY on the backend, or use archive CSV import')
    first = spec.start_date
    imported = []
    skipped = []
    failed = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(22.0 if IS_VERCEL else 35.0, connect=8.0 if IS_VERCEL else 10.0), follow_redirects=False, headers={'User-Agent': f'EmberLens/{APP_VERSION} research-prototype'}) as client:
        while first <= spec.end_date:
            last = min(spec.end_date, first + timedelta(days=4))
            with open_db() as db:
                done = db.execute('''SELECT 1 FROM query_windows
                        WHERE region=? AND source=? AND start_date=? AND end_date=? AND status='retrieved' ''',
                        (spec.region, spec.source, first.isoformat(), last.isoformat())).fetchone()
            period = {'start': first.isoformat(), 'end': last.isoformat()}
            if done and not spec.force:
                skipped.append(period)
            else:
                try:
                    rows, stats = await fetch_csv_window(client, key, spec.region, spec.source, first, last)
                    with open_db() as db:
                        out = import_rows(db, rows, spec.source, 'FIRMS area API backfill',
                                          stats=stats, query_window=(spec.region, first, last))
                    imported.append({**period, 'rows_added': out['rows_added'],
                                     'rows_returned': len(rows)})
                except ValueError as e:
                    failed.append({**period, 'error': str(e)})
                    # Stop after a failure to avoid flooding the API when access is broken.
                    break
            first = last + timedelta(days=1)
    if failed and not imported and not skipped:
        raise HTTPException(502, {'message':'FIRMS backfill failed; no window recorded',
                                  'failed':failed})
    return {'status': 'partial' if failed else 'completed', 'region': spec.region,
            'source': spec.source, 'requested_days': days,
            'windows_imported': imported, 'windows_skipped': skipped,
            'windows_failed': failed, 'total_rows_added': sum(x['rows_added'] for x in imported),
            'note': 'Successful retrieval windows do not establish uninterrupted satellite coverage. Resume by repeating the same request.'}


app.mount('/',StaticFiles(directory=str(ROOT/'frontend'),html=True),name='frontend')
