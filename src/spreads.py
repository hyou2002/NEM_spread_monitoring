"""Spread calculations — the deterministic core (spec section 3).

Two methods, both in AUD/MWh:

Best Case (time-free):
    For each day, charge = mean of the N cheapest 5-min RRPs, discharge = mean of
    the N most expensive, spread = discharge - charge. Weekly = mean of daily values.
    N per battery: 2H -> 24, 4H -> 48 (24 * 5min = 2h).

Fixed Time:
    Charge/discharge windows come from config/fixed_windows.csv (per region, month,
    battery). Daily charge/discharge = mean RRP inside each clock-time window;
    spread = discharge - charge. Weekly = mean of daily values, empty days excluded.

Interval labels are END times (see dates.label_to_day). A window (start, end] of
D hours therefore contains exactly D*12 intervals — e.g. 11:00..13:00 -> the 24
labels 11:05..13:00, matching N=24 for a 2H battery.
"""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

import pandas as pd

from . import dates

BATTERY_N = {"2H": 24, "4H": 48}
DEFAULT_FIXED_WINDOWS = (
    Path(__file__).resolve().parent.parent / "config" / "fixed_windows.csv"
)


# --------------------------------------------------------------------------- #
# Window slicing
# --------------------------------------------------------------------------- #
def slice_week(region_df: pd.DataFrame, start: datetime, end: datetime) -> pd.DataFrame:
    """Slice one region's series to the inclusive analysis window and verify size."""
    mask = (region_df["settlementdate"] >= start) & (region_df["settlementdate"] <= end)
    week = region_df.loc[mask].copy()
    dates.assert_full_week(len(week))
    week["day"] = week["settlementdate"].map(dates.label_to_day)
    return week


# --------------------------------------------------------------------------- #
# Best case
# --------------------------------------------------------------------------- #
def best_case_daily(day_rrp: pd.Series, n: int) -> tuple[float, float, float]:
    rrp_sorted = day_rrp.sort_values()
    charge = rrp_sorted.iloc[:n].mean()
    discharge = rrp_sorted.iloc[-n:].mean()
    return charge, discharge, discharge - charge


def compute_best_case(week: pd.DataFrame, battery: str) -> dict:
    n = BATTERY_N[battery]
    rows = [best_case_daily(g["rrp"], n) for _, g in week.groupby("day")]
    daily = pd.DataFrame(rows, columns=["charge", "discharge", "spread"])
    return {
        "charge": daily["charge"].mean(),
        "discharge": daily["discharge"].mean(),
        "spread": daily["spread"].mean(),
    }


# --------------------------------------------------------------------------- #
# Fixed time
# --------------------------------------------------------------------------- #
def load_fixed_windows(path: str | Path = DEFAULT_FIXED_WINDOWS) -> pd.DataFrame:
    """Load fixed-window config; key columns parsed to month-number and time."""
    fw = pd.read_csv(path)
    required = {
        "region", "month", "battery",
        "charge_start", "charge_end", "discharge_start", "discharge_end",
    }
    missing = required - set(fw.columns)
    if missing:
        raise ValueError(f"fixed_windows.csv missing columns: {sorted(missing)}")
    fw["region"] = fw["region"].str.upper().str.strip()
    fw["month_num"] = pd.to_datetime(fw["month"], format="%Y-%m").dt.month
    for col in ("charge_start", "charge_end", "discharge_start", "discharge_end"):
        fw[col] = pd.to_datetime(fw[col], format="%H:%M").dt.time
    return fw


def _window_mean(day_group: pd.DataFrame, start: time, end: time) -> float:
    """Mean RRP for interval labels t with start <= t < end.

    The [start, end) convention was chosen empirically: it is the one of four
    boundary rules that reproduces the user's Excel fixed-time numbers (see
    tests/test_regression.py / the answer key). A D-hour window thus contains
    exactly D*12 intervals, labelled start .. (end - 5min).
    """
    t = day_group["settlementdate"].dt.time
    sel = day_group.loc[(t >= start) & (t < end), "rrp"]
    return sel.mean() if not sel.empty else float("nan")


def fixed_lookup(fw: pd.DataFrame, region: str, month_num: int, battery: str) -> dict:
    row = fw[
        (fw["region"] == region)
        & (fw["month_num"] == month_num)
        & (fw["battery"] == battery)
    ]
    if row.empty:
        raise KeyError(
            f"No fixed window for region={region} month={month_num:02d} "
            f"battery={battery} in fixed_windows.csv"
        )
    r = row.iloc[0]
    return {
        "charge_start": r["charge_start"], "charge_end": r["charge_end"],
        "discharge_start": r["discharge_start"], "discharge_end": r["discharge_end"],
    }


def compute_fixed(week: pd.DataFrame, region: str, battery: str, fw: pd.DataFrame,
                  month_num: int) -> dict:
    w = fixed_lookup(fw, region, month_num, battery)
    charges, discharges, spreads = [], [], []
    for _, g in week.groupby("day"):
        c = _window_mean(g, w["charge_start"], w["charge_end"])
        d = _window_mean(g, w["discharge_start"], w["discharge_end"])
        if pd.isna(c) or pd.isna(d):  # empty day -> excluded
            continue
        charges.append(c)
        discharges.append(d)
        spreads.append(d - c)
    return {
        "charge": pd.Series(charges).mean(),
        "discharge": pd.Series(discharges).mean(),
        "spread": pd.Series(spreads).mean(),
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def window_month(start: datetime, end: datetime) -> int:
    """Month-of-year used to pick fixed windows: the majority of the 7 days."""
    days = pd.date_range(dates.label_to_day(start + dates.INTERVAL),
                         dates.label_to_day(end), freq="D")
    return int(pd.Series(days.month).mode().iloc[0])


def compute_region(region_df: pd.DataFrame, region: str, start: datetime,
                   end: datetime, fw: pd.DataFrame) -> list[dict]:
    """All eight rows (best_case/fixed_time x 2H/4H) for one region."""
    week = slice_week(region_df, start, end)
    month_num = window_month(start, end)
    week_start = dates.label_to_day(start + dates.INTERVAL)
    rows = []
    for battery in ("2H", "4H"):
        bc = compute_best_case(week, battery)
        rows.append({"week_start": week_start, "region": region,
                     "method": "best_case", "battery": battery, **bc})
    for battery in ("2H", "4H"):
        fx = compute_fixed(week, region, battery, fw, month_num)
        rows.append({"week_start": week_start, "region": region,
                     "method": "fixed_time", "battery": battery, **fx})
    return rows


def compute_all(region_frames: dict[str, pd.DataFrame], start: datetime,
                end: datetime, fw: pd.DataFrame | None = None) -> pd.DataFrame:
    """Compute the full dashboard table from {region: series} frames."""
    if fw is None:
        fw = load_fixed_windows()
    all_rows = []
    for region, rdf in region_frames.items():
        all_rows.extend(compute_region(rdf, region, start, end, fw))
    return pd.DataFrame(all_rows)
