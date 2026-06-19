"""Week / interval logic for the NEM Weekly Spread Monitor.

AEMO labels each 5-minute dispatch interval by its END time, e.g. the label
``00:05`` covers 00:00->00:05 and the label ``00:00`` covers the previous
day's 23:55->00:00. All date logic in this project funnels through here so the
"end-labelled interval" convention is applied in exactly one place.

Spec section 4 (확정):
    run_date = a Monday.
    Analysis window = previous Monday 00:05  ..  this Monday 00:00  (both inclusive)
                    = exactly 2016 five-minute intervals = 7 days.
    Example: run_date 2026-06-15 (Mon) -> 2026-06-08 00:05 .. 2026-06-15 00:00.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# A week of 5-minute intervals: 7 days * 24 h * 12 intervals/h.
INTERVALS_PER_WEEK = 2016
INTERVALS_PER_DAY = 288
INTERVAL = timedelta(minutes=5)


def previous_monday(run_date: date) -> date:
    """The Monday that starts the analysis week (7 days before run_date)."""
    return run_date - timedelta(days=7)


def analysis_window(run_date: date) -> tuple[datetime, datetime]:
    """Return (start_dt, end_dt) for the analysis week.

    start_dt = previous Monday at 00:05 (first interval label of that day)
    end_dt   = run_date (this Monday) at 00:00 (last interval label of Sunday)

    Both endpoints are inclusive. run_date must be a Monday.
    """
    if run_date.weekday() != 0:  # Monday == 0
        raise ValueError(
            f"run_date must be a Monday; got {run_date:%Y-%m-%d} "
            f"({run_date:%A}). The weekly job runs every Monday."
        )
    start_dt = datetime.combine(previous_monday(run_date), datetime.min.time()) + INTERVAL
    end_dt = datetime.combine(run_date, datetime.min.time())
    return start_dt, end_dt


def week_start(run_date: date) -> date:
    """The date used to label the week in outputs / the answer key (prev Monday)."""
    return previous_monday(run_date)


def label_to_day(ts: datetime) -> date:
    """Map an end-labelled interval timestamp to the calendar day it belongs to.

    Mirrors the Excel ``INT(Dates - TIME(0,5,0))`` 5-minute back-shift so that the
    00:00 midnight label is grouped with the *previous* day, and 00:05..23:55 with
    the current day. Used for both best-case daily grouping and fixed-window
    day assignment.
    """
    return (ts - INTERVAL).date()


def expected_interval_count() -> int:
    return INTERVALS_PER_WEEK


def assert_full_week(n_intervals: int) -> None:
    """Guard: the sliced window must contain exactly one full week of data."""
    if n_intervals != INTERVALS_PER_WEEK:
        raise AssertionError(
            f"Expected exactly {INTERVALS_PER_WEEK} five-minute intervals in the "
            f"analysis week but found {n_intervals}. The AEMO file is likely "
            f"incomplete (missing days), the wrong resolution (30-min instead of "
            f"5-min), or has gaps. Investigate before trusting the numbers."
        )
