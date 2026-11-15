"""Persist free NASA POWER weather and Planetary Computer NDVI summaries.

The script splits long windows into calendar quarters so a provider failure only
leaves one small batch to retry. Existing FIRMS detections are never read or
modified.
"""
from __future__ import annotations

import argparse
import asyncio
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.core import REGIONS
from backend.integrations import get_power_weather_context, get_vegetation_context
from backend.main import APP_VERSION, open_db, store_vegetation_context, store_weather_context


DEFAULT_REGIONS = ("punjab", "california", "amazon")


def quarter_windows(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        quarter_end_month = ((current.month - 1) // 3 + 1) * 3
        quarter_end = date(current.year, quarter_end_month, monthrange(current.year, quarter_end_month)[1])
        last = min(quarter_end, end_date)
        yield current, last
        current = last + timedelta(days=1)


async def backfill(regions: list[str], start_date: date, end_date: date, *, weather_only: bool = False) -> None:
    batches = [(region, start, end) for region in regions for start, end in quarter_windows(start_date, end_date)]
    weather_total = 0
    vegetation_total = 0
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=False,
        headers={"User-Agent": f"EmberLens/{APP_VERSION} free-context-backfill"},
    ) as client:
        for index, (region, first, last) in enumerate(batches, 1):
            weather = await get_power_weather_context(
                client, region=region, region_meta=REGIONS[region],
                start_date=first, end_date=last,
            )
            vegetation = None if weather_only else await get_vegetation_context(
                client, region=region, region_meta=REGIONS[region],
                start_date=first, end_date=last, max_composites=8,
                stats_delay_seconds=0.15,
            )
            with open_db() as db:
                weather_total += store_weather_context(db, region, weather)
                if vegetation is not None:
                    vegetation_total += store_vegetation_context(db, region, vegetation)
            print(f"{index}/{len(batches)} {region}: {first} to {last} saved", flush=True)
    print(f"Complete: {weather_total} weather rows and {vegetation_total} NDVI summaries saved.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill free environmental context into EmberLens.")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2024, 12, 31))
    parser.add_argument("--regions", nargs="+", choices=DEFAULT_REGIONS, default=list(DEFAULT_REGIONS))
    parser.add_argument("--weather-only", action="store_true", help="Refresh weather without requesting NDVI again.")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start must not be after --end")
    return args


if __name__ == "__main__":
    arguments = parse_args()
    asyncio.run(backfill(arguments.regions, arguments.start, arguments.end, weather_only=arguments.weather_only))
