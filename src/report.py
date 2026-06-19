"""Assemble the dashboard tables and end-to-end pipeline (spec sections 5, 7).

This is the single entry point both the CLI (run.py) and the Streamlit app
(app.py) call, so they always produce identical numbers.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import pandas as pd

from . import dates, demand, download, ingest, spreads

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"


def build_region_frames(
    run_date: date,
    *,
    raw_dir: Path = RAW_DIR,
    auto_download: bool = True,
    uploaded: dict[str, pd.DataFrame] | None = None,
) -> dict[str, pd.DataFrame]:
    """Return {region: cleaned 5-min series} for the analysis week.

    Order of precedence per region: an uploaded frame (manual fallback) wins;
    otherwise auto-download the month files into the cache and load them; if that
    fails and a cached file already exists, fall back to the cache.
    """
    start, end = dates.analysis_window(run_date)
    uploaded = uploaded or {}
    frames: dict[str, pd.DataFrame] = {}

    if auto_download:
        try:
            download.ensure_window_files(start.date(), end.date(), raw_dir=raw_dir)
        except RuntimeError as exc:  # network/URL failure must not be fatal
            print(f"[warn] auto-download failed: {exc}")

    months = download.months_in_window(start.date(), end.date())
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


def resolution_report(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per-region resolution check (spec 2-1). is_5min must be True to trust output."""
    return pd.DataFrame(ingest.detect_resolution(f) for f in frames.values())


def build_report(
    run_date: date,
    *,
    auto_download: bool = True,
    uploaded: dict[str, pd.DataFrame] | None = None,
    raw_dir: Path = RAW_DIR,
) -> dict:
    """Run the full deterministic pipeline; return tables + metadata."""
    start, end = dates.analysis_window(run_date)
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
    return {
        "run_date": run_date,
        "week_start": dates.week_start(run_date),
        "window": (start, end),
        "regions": list(frames.keys()),
        "resolution": resolution_report(frames),
        "spreads": spreads_tbl,
        "demand": demand_tbl,
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
        report["demand"].round(1).to_excel(xw, sheet_name="demand", index=False)
        report["resolution"].to_excel(xw, sheet_name="resolution", index=False)
    return buf.getvalue()
