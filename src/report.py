"""Assemble the dashboard tables and end-to-end pipeline (spec sections 5, 7).

This is the single entry point both the CLI (run.py) and the Streamlit app
(app.py) call, so they always produce identical numbers.

Phase 2 additions (all deterministic, no external dependency):
  (a) week-over-week Best Case change vs the prior week, with an auto-generated
      one-line numeric restatement (no causal interpretation — that is for a human).
  (b) metric matrices: [2H/4H x charge/discharge/spread] rows x region columns,
      one matrix per method (Best Case / Fixed Time).
  (c) 2025 reference: this week's spread next to the same month in 2025 and the
      2025 annual average, from config/spread_2025_reference.csv.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import dates, demand, download, ingest, spreads

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
REFERENCE_2025 = ROOT / "config" / "spread_2025_reference.csv"


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_region_frames(
    start_date: date,
    end_date: date,
    *,
    raw_dir: Path = RAW_DIR,
    auto_download: bool = True,
    uploaded: dict[str, pd.DataFrame] | None = None,
) -> dict[str, pd.DataFrame]:
    """Return {region: cleaned 5-min series} covering [start_date, end_date].

    Order of precedence per region: an uploaded frame (manual fallback) wins;
    otherwise auto-download the month files into the cache and load them; if that
    fails and a cached file already exists, fall back to the cache.
    """
    uploaded = uploaded or {}
    frames: dict[str, pd.DataFrame] = {}

    if auto_download:
        try:
            download.ensure_window_files(start_date, end_date, raw_dir=raw_dir)
        except RuntimeError as exc:  # network/URL failure must not be fatal
            print(f"[warn] auto-download failed: {exc}")

    months = download.months_in_window(start_date, end_date)
    for region in ingest.REGIONS:
        if region in uploaded:
            frames[region] = uploaded[region]
            continue
        paths = [raw_dir / download.filename_for(ym, region) for ym in months]
        existing = [p for p in paths if p.exists()]
        if not existing:
            print(f"[warn] no data for {region}; skipping.")
            continue
        frames[region] = ingest.load_many(existing)
    return frames


def build_region_frames(
    run_date: date,
    *,
    raw_dir: Path = RAW_DIR,
    auto_download: bool = True,
    uploaded: dict[str, pd.DataFrame] | None = None,
    include_prior_week: bool = True,
) -> dict[str, pd.DataFrame]:
    """Frames covering the analysis week (and, by default, the prior week too).

    The prior week is loaded so the week-over-week comparison (a) can be computed
    from the same in-memory frames without a second download.
    """
    start, end = dates.analysis_window(run_date)
    span_start = start
    if include_prior_week:
        span_start, _ = dates.analysis_window(dates.previous_monday(run_date))
    return load_region_frames(
        span_start.date(), end.date(),
        raw_dir=raw_dir, auto_download=auto_download, uploaded=uploaded,
    )


def resolution_report(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per-region resolution check (spec 2-1). is_5min must be True to trust output."""
    return pd.DataFrame(ingest.detect_resolution(f) for f in frames.values())


# --------------------------------------------------------------------------- #
# (a) Week-over-week Best Case comparison
# --------------------------------------------------------------------------- #
def _best_case_week(frames: dict[str, pd.DataFrame], start, end) -> pd.DataFrame:
    """Best Case charge/discharge/spread per region x battery for one window."""
    rows = []
    for region, rdf in frames.items():
        week = spreads.slice_week(rdf, start, end)
        for battery in ("2H", "4H"):
            bc = spreads.compute_best_case(week, battery)
            rows.append({"region": region, "battery": battery, **bc})
    return pd.DataFrame(rows)


def _describe_change(dc: float, dd: float, ds: float) -> str:
    """One-line numeric restatement of the change. NO causal interpretation."""
    return (f"충전가 {dc:+.0f}, 방전가 {dd:+.0f}로 "
            f"스프레드 {ds:+.0f} (AUD/MWh)")


def compare_best_case(
    frames: dict[str, pd.DataFrame], run_date: date
) -> pd.DataFrame | None:
    """This analysis week vs the prior week, Best Case, per region x battery.

    Returns a DataFrame with this-week values, prior-week values, deltas, and an
    auto one-line description. Returns None (caller degrades gracefully) if the
    prior week cannot be sliced — e.g. its data is missing/incomplete.
    """
    start, end = dates.analysis_window(run_date)
    prev_start, prev_end = dates.analysis_window(dates.previous_monday(run_date))
    try:
        this = _best_case_week(frames, start, end)
        prev = _best_case_week(frames, prev_start, prev_end)
    except AssertionError as exc:  # incomplete prior week -> skip comparison
        print(f"[warn] week-over-week comparison skipped: {exc}")
        return None

    m = this.merge(prev, on=["region", "battery"], suffixes=("_this", "_prev"))
    for metric in ("charge", "discharge", "spread"):
        m[f"{metric}_delta"] = m[f"{metric}_this"] - m[f"{metric}_prev"]
    m["description"] = m.apply(
        lambda r: _describe_change(r["charge_delta"], r["discharge_delta"],
                                   r["spread_delta"]), axis=1
    )
    m["region"] = pd.Categorical(m["region"], categories=ingest.REGIONS, ordered=True)
    return m.sort_values(["region", "battery"]).reset_index(drop=True)


def _arrow(delta: float) -> str:
    if round(delta) > 0:
        return "↑"
    if round(delta) < 0:
        return "↓"
    return "→"


def format_change_table(cmp: pd.DataFrame) -> pd.DataFrame:
    """Display view of compare_best_case(): 'value  arrow|delta' cells + 설명."""
    out = pd.DataFrame()
    out["지역"] = cmp["region"].astype(str)
    out["용량"] = cmp["battery"]
    for metric, label in (("charge", "충전가"), ("discharge", "방전가"),
                          ("spread", "Spread")):
        out[label] = [
            f"{v:.0f}  {_arrow(d)}{abs(d):.0f}"
            for v, d in zip(cmp[f"{metric}_this"], cmp[f"{metric}_delta"])
        ]
    out["설명 (지난주 대비)"] = cmp["description"]
    return out


# --------------------------------------------------------------------------- #
# (b) Metric matrices: [2H/4H x charge/discharge/spread] rows x region columns
# --------------------------------------------------------------------------- #
_MATRIX_ROWS = [
    ("2H", "charge", "2H 충전"), ("2H", "discharge", "2H 방전"),
    ("2H", "spread", "2H Spread"),
    ("4H", "charge", "4H 충전"), ("4H", "discharge", "4H 방전"),
    ("4H", "spread", "4H Spread"),
]


def metric_matrix(spreads_tbl: pd.DataFrame, method: str) -> pd.DataFrame:
    """Dashboard matrix for one method: 6 metric rows x region columns (rounded)."""
    sub = spreads_tbl[spreads_tbl["method"] == method]
    regions = [r for r in ingest.REGIONS if r in set(sub["region"])]
    data = {}
    for region in regions:
        rsub = sub[sub["region"] == region]
        col = []
        for battery, metric, _ in _MATRIX_ROWS:
            cell = rsub[rsub["battery"] == battery][metric]
            col.append(round(float(cell.iloc[0])) if not cell.empty else np.nan)
        data[region] = col
    return pd.DataFrame(data, index=[label for *_, label in _MATRIX_ROWS])


# --------------------------------------------------------------------------- #
# (c) 2025 reference comparison
# --------------------------------------------------------------------------- #
def reference_2025(
    spreads_tbl: pd.DataFrame, month_num: int, *, ref_path: Path = REFERENCE_2025
) -> pd.DataFrame:
    """This week's spread next to the same month in 2025 and the 2025 average."""
    ref = pd.read_csv(ref_path)
    period = f"2025-{month_num:02d}"
    this_col = "이번 주"
    m_col = f"2025-{month_num:02d}"
    avg_col = "2025 연평균"

    cur = (spreads_tbl[["region", "method", "battery", "spread"]]
           .rename(columns={"spread": this_col}))
    cur[this_col] = cur[this_col].round(0)
    month_ref = (ref[ref["period"] == period][["region", "method", "battery", "spread"]]
                 .rename(columns={"spread": m_col}))
    avg_ref = (ref[ref["period"] == "2025-avg"][["region", "method", "battery", "spread"]]
               .rename(columns={"spread": avg_col}))

    out = cur.merge(month_ref, on=["region", "method", "battery"], how="left")
    out = out.merge(avg_ref, on=["region", "method", "battery"], how="left")
    out["region"] = pd.Categorical(out["region"], categories=ingest.REGIONS,
                                   ordered=True)
    out = out.sort_values(["method", "region", "battery"]).reset_index(drop=True)
    out = out.rename(columns={"region": "지역", "method": "방식", "battery": "용량"})
    return out


def reference_2025_tables(
    month_num: int, method: str, *, ref_path: Path = REFERENCE_2025
) -> dict:
    """2025 reference spreads in the dashboard image layout for one method.

    Returns {"annual": df, "month": df, "month_label": "MM월"} where each df has
    rows ['2H','4H'] (index name '구분') and columns NSW/QLD/VIC/SA.
    """
    ref = pd.read_csv(ref_path)
    sub = ref[ref["method"] == method]

    def _matrix(period: str) -> pd.DataFrame:
        s = sub[sub["period"] == period]
        data = {}
        for region in ingest.REGIONS:
            col = []
            for battery in ("2H", "4H"):
                cell = s[(s["region"] == region) & (s["battery"] == battery)]["spread"]
                col.append(int(cell.iloc[0]) if not cell.empty else np.nan)
            data[region] = col
        df = pd.DataFrame(data, index=["2H", "4H"])
        df.index.name = "구분"
        return df

    return {
        "annual": _matrix("2025-avg"),
        "month": _matrix(f"2025-{month_num:02d}"),
        "month_label": f"{month_num:02d}월",
    }


def demand_compare(frames: dict[str, pd.DataFrame], run_date: date) -> pd.DataFrame:
    """Average demand per region x band, this week and last week side by side.

    Columns: 지역 / 시간대 / 지난주 / 이번주 (MW). 지난주 is NaN if the prior
    week's data is incomplete (graceful — never raises).
    """
    start, end = dates.analysis_window(run_date)
    pstart, pend = dates.analysis_window(dates.previous_monday(run_date))
    this = (demand.compute_all_demand(frames, start, end)[["region", "band",
            "avg_demand_mw"]].rename(columns={"avg_demand_mw": "이번 주"}))
    try:
        prev = (demand.compute_all_demand(frames, pstart, pend)[["region", "band",
                "avg_demand_mw"]].rename(columns={"avg_demand_mw": "지난주"}))
        out = prev.merge(this, on=["region", "band"], how="right")
    except AssertionError:
        out = this.copy()
        out["지난주"] = float("nan")

    out["region"] = pd.Categorical(out["region"], categories=ingest.REGIONS,
                                   ordered=True)
    out["band"] = pd.Categorical(out["band"], categories=["24h", "daytime", "peak"],
                                 ordered=True)
    out = out.sort_values(["region", "band"]).reset_index(drop=True)
    out = out.rename(columns={"region": "지역", "band": "시간대"})
    return out[["지역", "시간대", "지난주", "이번 주"]]


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def build_report(
    run_date: date,
    *,
    auto_download: bool = True,
    uploaded: dict[str, pd.DataFrame] | None = None,
    raw_dir: Path = RAW_DIR,
    frames: dict[str, pd.DataFrame] | None = None,
) -> dict:
    """Run the full deterministic pipeline; return tables + metadata.

    ``frames`` may be supplied pre-loaded (e.g. from a cached loader) to skip the
    download/parse step; otherwise they are loaded here. Computing the tables is
    always done fresh so code changes take effect without cache invalidation.
    """
    start, end = dates.analysis_window(run_date)
    if frames is None:
        frames = build_region_frames(
            run_date, raw_dir=raw_dir, auto_download=auto_download, uploaded=uploaded
        )
    if not frames:
        raise RuntimeError(
            "No AEMO data available for any region. Auto-download failed and no "
            "cached/uploaded files were found. Upload the monthly CSV(s) manually."
        )
    spreads_tbl = spreads.compute_all(frames, start, end)
    demand_tbl = demand.compute_all_demand(frames, start, end)
    month_num = spreads.window_month(start, end)
    return {
        "run_date": run_date,
        "week_start": dates.week_start(run_date),
        "window": (start, end),
        "month_num": month_num,
        "regions": list(frames.keys()),
        "resolution": resolution_report(frames),
        "spreads": spreads_tbl,
        "demand": demand_tbl,
        "demand_compare": demand_compare(frames, run_date),
        "best_case_change": compare_best_case(frames, run_date),  # (a); may be None
        "best_case_matrix": metric_matrix(spreads_tbl, "best_case"),  # (b)
        "fixed_time_matrix": metric_matrix(spreads_tbl, "fixed_time"),  # (b)
        "reference_2025": reference_2025(spreads_tbl, month_num),  # (c)
    }


def spreads_pivot(spreads_tbl: pd.DataFrame) -> pd.DataFrame:
    """Reshape long spreads table into the dashboard layout (region x method/battery)."""
    df = spreads_tbl.copy()
    df["col"] = df["method"] + "_" + df["battery"]
    wide = df.pivot(index="region", columns="col", values="spread").round(0)
    return wide.reindex(ingest.REGIONS).dropna(how="all")


def to_excel_bytes(report: dict) -> bytes:
    """Serialise the report tables to an .xlsx workbook for download."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        report["spreads"].round(2).to_excel(xw, sheet_name="spreads", index=False)
        report["best_case_matrix"].to_excel(xw, sheet_name="best_case_matrix")
        report["fixed_time_matrix"].to_excel(xw, sheet_name="fixed_time_matrix")
        report["reference_2025"].to_excel(xw, sheet_name="vs_2025", index=False)
        if report.get("best_case_change") is not None:
            format_change_table(report["best_case_change"]).to_excel(
                xw, sheet_name="best_case_change", index=False)
        report["demand"].round(1).to_excel(xw, sheet_name="demand", index=False)
        report["resolution"].to_excel(xw, sheet_name="resolution", index=False)
    return buf.getvalue()
