"""Auto-download AEMO monthly PRICE_AND_DEMAND CSVs (spec 2-1).

URL pattern (fixed, no manual dropdown needed):
    https://www.aemo.com.au/aemo/data/nem/priceanddemand/PRICE_AND_DEMAND_{YYYYMM}_{REGION}1.csv

Files are cached under data/raw/; an existing cached file is reused instead of
re-downloading. If the analysis window straddles two months, both months are
fetched and the caller concatenates them.
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import requests

from .ingest import REGIONS

BASE_URL = "https://www.aemo.com.au/aemo/data/nem/priceanddemand"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "NEM-Weekly-Spread-Monitor/1.0 (+internal tool)"
)
REQUEST_DELAY_SECONDS = 1.0  # be polite between requests
DEFAULT_RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def filename_for(year_month: str, region: str) -> str:
    return f"PRICE_AND_DEMAND_{year_month}_{region}1.csv"


def url_for(year_month: str, region: str) -> str:
    return f"{BASE_URL}/{filename_for(year_month, region)}"


def months_in_window(start: date, end: date) -> list[str]:
    """Distinct YYYYMM strings the [start, end] window touches (max 2 in practice)."""
    months: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append(f"{y}{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return months


def download_one(
    year_month: str,
    region: str,
    raw_dir: Path = DEFAULT_RAW_DIR,
    *,
    force: bool = False,
    session: requests.Session | None = None,
) -> Path:
    """Download a single region-month CSV, using the cache unless ``force``."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    dest = raw_dir / filename_for(year_month, region)
    if dest.exists() and not force:
        return dest

    sess = session or requests.Session()
    url = url_for(year_month, region)
    try:
        resp = sess.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Failed to download AEMO data for {region} {year_month} from {url}: "
            f"{exc}. Check connectivity, or upload the CSV manually in the app."
        ) from exc

    if not resp.text.startswith("REGION"):
        raise RuntimeError(
            f"Downloaded {url} but it does not look like an AEMO CSV "
            f"(first bytes: {resp.text[:60]!r}). The URL pattern may have changed."
        )

    dest.write_bytes(resp.content)
    return dest


def ensure_window_files(
    start: date,
    end: date,
    regions: list[str] = REGIONS,
    raw_dir: Path = DEFAULT_RAW_DIR,
    *,
    force: bool = False,
) -> dict[str, list[Path]]:
    """Download every (region x month) file the window needs.

    Returns {region: [paths...]} so the caller can load+concat per region.
    """
    months = months_in_window(start, end)
    session = requests.Session()
    result: dict[str, list[Path]] = {}
    for region in regions:
        paths: list[Path] = []
        for ym in months:
            cached = (raw_dir / filename_for(ym, region)).exists()
            paths.append(
                download_one(ym, region, raw_dir, force=force, session=session)
            )
            if not cached:
                time.sleep(REQUEST_DELAY_SECONDS)
        result[region] = paths
    return result
