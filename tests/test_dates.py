"""Date / window logic tests (spec section 4)."""
from datetime import date, datetime

import pytest

from src import dates


def test_window_is_exactly_one_week():
    start, end = dates.analysis_window(date(2026, 6, 15))
    assert start == datetime(2026, 6, 8, 0, 5)
    assert end == datetime(2026, 6, 15, 0, 0)
    # Inclusive 5-min intervals from start to end.
    n = int((end - start).total_seconds() // 300) + 1
    assert n == dates.INTERVALS_PER_WEEK == 2016


def test_run_date_must_be_monday():
    with pytest.raises(ValueError, match="Monday"):
        dates.analysis_window(date(2026, 6, 16))  # Tuesday


def test_label_to_day_back_shifts_midnight():
    # 00:05 belongs to its own day; 00:00 belongs to the previous day.
    assert dates.label_to_day(datetime(2026, 6, 8, 0, 5)) == date(2026, 6, 8)
    assert dates.label_to_day(datetime(2026, 6, 15, 0, 0)) == date(2026, 6, 14)
    assert dates.label_to_day(datetime(2026, 6, 8, 12, 0)) == date(2026, 6, 8)


def test_week_start_is_previous_monday():
    assert dates.week_start(date(2026, 6, 15)) == date(2026, 6, 8)


def test_assert_full_week_rejects_short_data():
    with pytest.raises(AssertionError, match="2016"):
        dates.assert_full_week(2015)
    dates.assert_full_week(2016)  # no raise
