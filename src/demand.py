"""Demand variation summary (spec section 5, monitoring target 3).

Splits the analysis week into three time-of-day bands and reports average
TOTALDEMAND (MW) per band, plus the week-over-week change vs the prior week.

Bands (clock time of the end-labelled interval):
    24h     : all intervals
    daytime : 10:00-16:00
    peak    : 16:00-21:00
"""

from __future__ import annotations

from datetime import datetime, time

import pandas as pd

from . import dates

BANDS = {
    "24h": None,
    "daytime": (time(10, 0), time(16, 0)),
    "peak": (time(16, 0), time(21, 0)),
}


def _band_mean(week: pd.DataFrame, band: tuple[time, time] | None) -> float:
    if band is None:
        return week["totaldemand"].mean()
    start, end = band
    t = week["settlementdate"].dt.time
    # [start, end) — same convention as the fixed-window spreads.
    return week.loc[(t >= start) & (t < end), "totaldemand"].mean()


def compute_demand(region_df: pd.DataFrame, region: str, start: datetime,
                   end: datetime) -> list[dict]:
    """Average demand per band for the analysis week of one region."""
    mask = (region_df["settlementdate"] >= start) & (region_df["settlementdate"] <= end)
    week = region_df.loc[mask]
    dates.assert_full_week(len(week))
    week_start = dates.label_to_day(start + dates.INTERVAL)
    return [
        {"week_start": week_start, "region": region, "band": band,
         "avg_demand_mw": _band_mean(week, spec)}
        for band, spec in BANDS.items()
    ]


def compute_all_demand(region_frames: dict[str, pd.DataFrame], start: datetime,
                       end: datetime) -> pd.DataFrame:
    rows = []
    for region, rdf in region_frames.items():
        rows.extend(compute_demand(rdf, region, start, end))
    return pd.DataFrame(rows)
