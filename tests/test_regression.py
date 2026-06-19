"""Regression test against the user's verified Excel answer key (spec section 8.3).

Locking rules (agreed with the user after the 4-region cross-check):

* NSW  -> STRICT: every field (charge/discharge/spread) must match the answer key
  exactly after rounding to the nearest integer. NSW is the fully-trusted region
  (the user's own raw CSV + a clean answer key).

* QLD / SA / VIC -> spread within +/-1. Their answer keys contain a few
  data-entry errors in the charge/discharge cells (documented below) whose spread
  is nonetheless correct, so we lock on the headline spread with a 1-unit
  tolerance for integer-display rounding.

Known answer-key issues (the *code* is correct in each; evidence = matching spread):
  - SA  best_case 2H charge: key shows 5, should be -5 (sign dropped).
  - QLD fixed_time 4H discharge / SA fixed_time 2H discharge: key is 1 low and
    internally inconsistent with its own charge+spread.
  - VIC fixed_time (2H & 4H): EXCLUDED. The VIC-June charge window in the source
    config was an overnight outlier (attributed to a 2025 market incident) and was
    re-set to a midday window from neighbouring months. Separately, no valid window
    reproduces the key's VIC fixed charge (~48) or discharge (67) -- those cells are
    unreliable placeholders. VIC best_case still validates against the key.

Coverage extends automatically to any region whose raw CSV sits in tests/fixtures/.
"""
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src import dates, ingest, spreads

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "tests" / "fixtures"
GOLDEN = ROOT / "tests" / "golden" / "verification.csv"

RUN_DATE = date(2026, 6, 15)  # week_start 2026-06-08
YEAR_MONTH = "202606"
SPREAD_TOLERANCE = 1  # integer-display rounding for non-NSW regions

# (region, method, battery) cells excluded from regression with a reason.
EXCLUDED = {
    ("VIC", "fixed_time", "2H"): "VIC fixed answer-key values are unreliable",
    ("VIC", "fixed_time", "4H"): "VIC fixed answer-key values are unreliable",
}


def _raw_path(region: str) -> Path:
    return RAW_DIR / f"PRICE_AND_DEMAND_{YEAR_MONTH}_{region}1.csv"


def _available_regions() -> list[str]:
    return [r for r in ingest.REGIONS if _raw_path(r).exists()]


def _computed_for(region: str) -> pd.DataFrame:
    df = ingest.load_raw_csv(_raw_path(region))
    start, end = dates.analysis_window(RUN_DATE)
    fw = spreads.load_fixed_windows()
    return pd.DataFrame(spreads.compute_region(df, region, start, end, fw)).set_index(
        ["method", "battery"]
    )


def _golden() -> pd.DataFrame:
    return pd.read_csv(GOLDEN)


# --------------------------------------------------------------------------- #
# NSW: strict, every field
# --------------------------------------------------------------------------- #
def _nsw_cases():
    if "NSW" not in _available_regions():
        return []
    comp = _computed_for("NSW")
    g = _golden()
    g = g[g["region"] == "NSW"]
    cases = []
    for _, row in g.iterrows():
        key = (row["method"], row["battery"])
        for field in ("charge", "discharge", "spread"):
            if pd.isna(row[field]):
                continue
            cases.append((row["method"], row["battery"], field,
                          comp.loc[key, field], float(row[field])))
    return cases


@pytest.mark.parametrize("method,battery,field,computed,expected", _nsw_cases())
def test_nsw_matches_excel_exactly(method, battery, field, computed, expected):
    assert round(computed) == round(expected), (
        f"NSW {method} {battery} {field}: computed {computed:.3f} "
        f"(rounds to {round(computed)}) != answer key {expected}"
    )


# --------------------------------------------------------------------------- #
# QLD / SA / VIC: spread within +/-1 (VIC fixed_time excluded)
# --------------------------------------------------------------------------- #
def _other_cases():
    cases = []
    g = _golden()
    for region in _available_regions():
        if region == "NSW":
            continue
        comp = _computed_for(region)
        gr = g[g["region"] == region]
        for _, row in gr.iterrows():
            if pd.isna(row["spread"]):
                continue
            cases.append((region, row["method"], row["battery"],
                          comp.loc[(row["method"], row["battery"]), "spread"],
                          float(row["spread"])))
    return cases


@pytest.mark.parametrize("region,method,battery,computed,expected", _other_cases())
def test_other_regions_spread_within_tolerance(region, method, battery,
                                               computed, expected):
    if (region, method, battery) in EXCLUDED:
        pytest.skip(EXCLUDED[(region, method, battery)])
    assert abs(round(computed) - round(expected)) <= SPREAD_TOLERANCE, (
        f"{region} {method} {battery} spread: computed {computed:.3f} "
        f"(rounds to {round(computed)}) differs from answer key {expected} "
        f"by more than {SPREAD_TOLERANCE}"
    )


def test_at_least_nsw_is_covered():
    """Guard: the regression suite must actually be checking something."""
    assert "NSW" in _available_regions(), (
        "NSW raw CSV missing from tests/fixtures/ — regression coverage empty."
    )
    assert _nsw_cases(), "No NSW comparison cases were generated."
