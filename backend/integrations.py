"""Optional environmental-context integrations for EmberLens.

Meteomatics supplies weather context; Microsoft Planetary Computer supplies
MODIS vegetation-index composites. Neither provider replaces NASA FIRMS fire
observations, and neither output is a fire-danger forecast.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Any

import httpx

METEOMATICS_BASE = "https://api.meteomatics.com"
PC_STAC_BASE = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_DATA_BASE = "https://planetarycomputer.microsoft.com/api/data/v1"
PC_COLLECTION = "modis-13Q1-061"
PC_NDVI_ASSET = "250m_16_days_NDVI"
WEATHER_PARAMETERS = (
    "t_2m:C",
    "relative_humidity_2m:p",
    "wind_speed_10m:ms",
    "wind_dir_10m:d",
    "precip_6h:mm",
)

# Small process-local caches prevent repeat button clicks from consuming API quota.
_CACHE: dict[tuple, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 30 * 60


def _cache_get(key: tuple) -> dict | None:
    entry = _CACHE.get(key)
    if not entry:
        return None
    expires_at, value = entry
    if expires_at < time.monotonic():
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: tuple, value: dict) -> None:
    _CACHE[key] = (time.monotonic() + _CACHE_TTL_SECONDS, value)


def _numeric(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # Meteomatics reserves very negative values for unavailable/invalid data.
    if number <= -900:
        return None
    return number


def parse_meteomatics_payload(payload: dict) -> list[dict]:
    """Merge Meteomatics' parameter-first JSON response into timestamp rows."""
    merged: dict[str, dict] = {}
    for series in payload.get("data", []):
        parameter = series.get("parameter")
        if not parameter:
            continue
        coordinates = series.get("coordinates") or []
        if not coordinates:
            continue
        # One regional centroid is requested; do not silently mix coordinate series.
        for sample in coordinates[0].get("dates", []):
            stamp = sample.get("date")
            if not stamp:
                continue
            merged.setdefault(stamp, {})[parameter] = _numeric(sample.get("value"))
    return [{"datetime": stamp, **values} for stamp, values in sorted(merged.items())]


def _parse_stats(payload: dict) -> dict:
    """Extract the NDVI band's statistics from the Data API GeoJSON response."""
    props = payload.get("properties") or {}
    stats = props.get("statistics") or payload.get("statistics") or {}
    if PC_NDVI_ASSET in stats and isinstance(stats[PC_NDVI_ASSET], dict):
        return stats[PC_NDVI_ASSET]
    # The service can name a single-band result by band index rather than asset.
    for value in stats.values():
        if isinstance(value, dict) and ("mean" in value or "valid_percent" in value):
            return value
    return {}


async def get_weather_context(
    client: httpx.AsyncClient,
    *,
    region: str,
    region_meta: dict,
    start_date: date,
    end_date: date,
    username: str,
    password: str,
) -> dict:
    """Fetch six-hourly weather time series at the configured region centroid."""
    lon, lat = region_meta["center"]
    cache_key = ("weather", region, start_date.isoformat(), end_date.isoformat(), username)
    cached = _cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    start = f"{start_date.isoformat()}T00:00:00Z"
    end = f"{end_date.isoformat()}T18:00:00Z"
    time_spec = f"{start}--{end}:PT6H"
    parameters = ",".join(WEATHER_PARAMETERS)
    url = f"{METEOMATICS_BASE}/{time_spec}/{parameters}/{lat:.5f},{lon:.5f}/json"
    response = await client.get(url, params={"model": "mix"}, auth=(username, password))
    response.raise_for_status()
    payload = response.json()
    observations = parse_meteomatics_payload(payload)
    if not observations:
        raise ValueError("Meteomatics returned no valid weather samples for this period")
    result = {
        "provider": "Meteomatics Weather API",
        "region": region,
        "location": {"latitude": lat, "longitude": lon},
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "time_step": "6 hours",
        "parameters": list(WEATHER_PARAMETERS),
        "observations": observations,
        "cached": False,
        "interpretation": (
            "Weather values are regional-centroid context, not conditions at every hotspot. "
            "They are not a fire-danger forecast and do not establish fire causation."
        ),
    }
    _cache_set(cache_key, result)
    return result


async def get_vegetation_context(
    client: httpx.AsyncClient,
    *,
    region: str,
    region_meta: dict,
    start_date: date,
    end_date: date,
    max_composites: int = 8,
) -> dict:
    """Search MODIS 16-day NDVI items and calculate AOI statistics per composite."""
    cache_key = ("vegetation", region, start_date.isoformat(), end_date.isoformat())
    cached = _cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    bbox = region_meta["bbox"]
    search = await client.post(
        f"{PC_STAC_BASE}/search",
        json={
            "collections": [PC_COLLECTION],
            "bbox": bbox,
            "datetime": f"{start_date.isoformat()}T00:00:00Z/{end_date.isoformat()}T23:59:59Z",
            "limit": max(1, min(int(max_composites), 8)),
            "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        },
    )
    search.raise_for_status()
    features = (search.json() or {}).get("features", [])
    features = sorted(
        features,
        key=lambda item: (item.get("properties") or {}).get("datetime") or "",
        reverse=True,
    )[:max(1, min(int(max_composites), 8))]

    west, south, east, north = bbox
    polygon = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [west, south], [east, south], [east, north],
                [west, north], [west, south],
            ]],
        },
        "properties": {},
    }
    composites = []
    for item in features:
        item_id = item.get("id")
        properties = item.get("properties") or {}
        if not item_id:
            continue
        assets = item.get("assets") or {}
        if PC_NDVI_ASSET not in assets:
            continue
        stats_response = await client.post(
            f"{PC_DATA_BASE}/item/statistics",
            params=[
                ("collection", PC_COLLECTION),
                ("item", item_id),
                ("assets", PC_NDVI_ASSET),
                ("unscale", "true"),
                ("p", "10"),
                ("p", "90"),
                ("max_size", "512"),
            ],
            json=polygon,
        )
        stats_response.raise_for_status()
        stats = _parse_stats(stats_response.json() or {})
        mean = _numeric(stats.get("mean"))
        if mean is None:
            continue
        stamp = properties.get("datetime") or properties.get("start_datetime")
        composites.append({
            "item_id": item_id,
            "datetime": stamp,
            "date": stamp[:10] if isinstance(stamp, str) else None,
            "mean_ndvi": round(mean, 4),
            "p10_ndvi": _numeric(stats.get("percentile_10")),
            "p90_ndvi": _numeric(stats.get("percentile_90")),
            "valid_percent": _numeric(stats.get("valid_percent")),
            "valid_pixels": int(stats["valid_pixels"]) if stats.get("valid_pixels") is not None else None,
            "min_ndvi": _numeric(stats.get("min")),
            "max_ndvi": _numeric(stats.get("max")),
        })

    result = {
        "provider": "Microsoft Planetary Computer",
        "collection": PC_COLLECTION,
        "dataset": "MODIS Vegetation Indices 16-Day (250 m), NDVI",
        "region": region,
        "bbox": bbox,
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "composites": composites,
        "statistics_max_size": 512,
        "cached": False,
        "interpretation": (
            "NDVI is a vegetation-greenness index from a 16-day best-pixel composite. "
            "It is not fuel moisture, biomass, burned area, or a fire-risk score. "
            "Statistics use the region bounding box and may include mixed land cover. "
            "The raster statistics request caps the maximum processing dimension at 512 pixels, "
            "so large-area summaries are sampled/downsampled rather than a full-resolution pixel census."
        ),
    }
    _cache_set(cache_key, result)
    return result
