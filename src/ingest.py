"""Load and validate AEMO PRICE_AND_DEMAND CSVs into a clean 5-minute series.

The auto-download monthly file ships columns:
    REGION, SETTLEMENTDATE, TOTALDEMAND, RRP, PERIODTYPE
    NSW1, 2026/06/01 00:05:00, 7490.39, 43.09, TRADE

Note the real file uses ``YYYY/MM/DD HH:MM:SS`` (slashes + seconds), which differs
from the spec's illustrative ``2026-06-08 00:05``; both are handled here.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = ["REGION", "SETTLEMENTDATE", "TOTALDEMAND", "RRP", "PERIODTYPE"]
REGIONS = ["NSW", "QLD", "VIC", "SA"]


def _region_code(region: str) -> str:
    """'NSW1' -> 'NSW'. AEMO region ids carry a trailing '1'."""
    region = str(region).strip().upper()
    return region[:-1] if region.endswith("1") else region


def load_raw_csv(path: str | Path) -> pd.DataFrame:
    """Load one AEMO CSV, validate columns, parse timestamps.

    Returns a DataFrame with columns: region, settlementdate (datetime64),
    totaldemand (float), rrp (float), periodtype (str), sorted by time.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"AEMO CSV not found: {path}")
    # AEMO uses 'YYYY/MM/DD HH:MM:SS' (slashes + seconds), not the spec's
    # illustrative '2026-06-08 00:05'; _normalise handles both via format="mixed".
    return _normalise(pd.read_csv(path), path.name)


def _normalise(df: pd.DataFrame, source: str) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(
            f"{source} is missing required column(s): {missing}. "
            f"Found columns: {list(df.columns)}. Is this an AEMO "
            f"PRICE_AND_DEMAND export?"
        )
    out = pd.DataFrame()
    out["region"] = df["REGION"].map(_region_code)
    out["settlementdate"] = pd.to_datetime(df["SETTLEMENTDATE"], format="mixed")
    if out["settlementdate"].isna().any():
        bad = df.loc[out["settlementdate"].isna(), "SETTLEMENTDATE"].head(3).tolist()
        raise ValueError(f"Could not parse SETTLEMENTDATE values like: {bad}")
    out["totaldemand"] = pd.to_numeric(df["TOTALDEMAND"], errors="coerce")
    out["rrp"] = pd.to_numeric(df["RRP"], errors="coerce")
    out["periodtype"] = df["PERIODTYPE"].astype(str)
    return out.sort_values("settlementdate").reset_index(drop=True)


def load_raw_csv_from_buffer(buf, source: str = "uploaded CSV") -> pd.DataFrame:
    """Load an AEMO CSV from a file-like object (Streamlit upload)."""
    return _normalise(pd.read_csv(buf), source)


def combine(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate per-region frames, dropping duplicate timestamps."""
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["region", "settlementdate"])
    return combined.sort_values(["region", "settlementdate"]).reset_index(drop=True)


def load_many(paths: list[str | Path]) -> pd.DataFrame:
    """Load and concatenate multiple CSVs (e.g. a window spanning two months)."""
    return combine([load_raw_csv(p) for p in paths])


def detect_resolution(df: pd.DataFrame) -> dict:
    """Inspect interval spacing to decide 5-min vs 30-min resolution.

    Returns a dict with the modal gap in minutes, intervals-per-day, and a
    boolean ``is_5min``. Spec 2-1 requires this check before trusting the file:
    the user's Excel assumes 5-minute (288 intervals/day) data.
    """
    region = df["region"].iloc[0]
    times = df.loc[df["region"] == region, "settlementdate"].sort_values()
    gaps_min = times.diff().dropna().dt.total_seconds() / 60.0
    modal_gap = int(gaps_min.mode().iloc[0]) if not gaps_min.empty else None

    # Intervals on a representative full day.
    by_day = times.dt.normalize().value_counts()
    full_days = by_day[by_day >= 200]  # ignore partial first/last days
    typical_per_day = int(full_days.mode().iloc[0]) if not full_days.empty else None

    return {
        "region": region,
        "modal_gap_minutes": modal_gap,
        "intervals_per_day": typical_per_day,
        "is_5min": modal_gap == 5 and typical_per_day == 288,
        "is_30min": modal_gap == 30,
    }
