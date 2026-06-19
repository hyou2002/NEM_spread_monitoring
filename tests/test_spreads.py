"""Unit tests for the spread primitives on small synthetic data."""
from datetime import date, datetime, time, timedelta

import pandas as pd

from src import spreads


def _one_day(rrps: list[float], day=date(2026, 6, 8)) -> pd.DataFrame:
    """Build a 288-interval day, end-labelled 00:05..00:00(next), with given RRPs."""
    base = datetime.combine(day, time(0, 0))
    ts = [base + timedelta(minutes=5 * (i + 1)) for i in range(288)]
    df = pd.DataFrame({"settlementdate": ts, "rrp": rrps})
    df["day"] = day
    return df


def test_best_case_picks_cheapest_and_dearest_n():
    # RRP = 0,1,2,...,287. N=24 -> cheapest mean = mean(0..23)=11.5,
    # dearest mean = mean(264..287)=275.5, spread=264.
    rrps = list(range(288))
    charge, discharge, spread = spreads.best_case_daily(pd.Series(rrps), n=24)
    assert charge == 11.5
    assert discharge == 275.5
    assert spread == 264.0


def test_window_mean_is_start_inclusive_end_exclusive():
    # Price = the interval's minute-of-day, so we can see exactly which labels
    # fall in the window. Window 11:00-12:00 -> labels 11:00..11:55 (12 of them).
    minutes = [(i + 1) * 5 for i in range(288)]  # label minute-of-day
    df = _one_day(minutes)
    got = spreads._window_mean(df, time(11, 0), time(12, 0))
    labels = list(range(11 * 60, 12 * 60, 5))  # 660..715
    assert got == sum(labels) / len(labels)
    assert len(labels) == 12  # a 1-hour window = 12 five-minute intervals


def test_fixed_excludes_empty_days():
    # Two days; day 2 has NaN in the discharge window -> excluded from the mean.
    fw = pd.DataFrame([{
        "region": "NSW", "month": "2025-06", "battery": "2H", "month_num": 6,
        "charge_start": time(11, 0), "charge_end": time(13, 0),
        "discharge_start": time(17, 0), "discharge_end": time(19, 0),
    }])
    d1 = _one_day([10.0] * 288, day=date(2026, 6, 8))
    d2 = _one_day([20.0] * 288, day=date(2026, 6, 9))
    # Blank out day 2's discharge window.
    t2 = d2["settlementdate"].dt.time
    d2.loc[(t2 >= time(17, 0)) & (t2 < time(19, 0)), "rrp"] = float("nan")
    week = pd.concat([d1, d2], ignore_index=True)
    out = spreads.compute_fixed(week, "NSW", "2H", fw, month_num=6)
    # Day 2 dropped -> result is purely day 1 (flat 10 everywhere).
    assert out["charge"] == 10.0
    assert out["discharge"] == 10.0
    assert out["spread"] == 0.0
