"""Build a side-by-side computed-vs-Excel comparison for the verified week.

Writes an .xlsx the user can open next to their original Excel to eyeball each
cell. Flags every discrepancy and its likely cause.
"""
from datetime import date
from pathlib import Path

import pandas as pd

from src import dates, ingest, spreads

ROOT = Path(__file__).resolve().parent
FIX = ROOT / "tests" / "fixtures"
OUT = ROOT.parent / "대조표_2026-06-08.xlsx"  # next to the user's files

start, end = dates.analysis_window(date(2026, 6, 15))
fw = spreads.load_fixed_windows()
gold = pd.read_csv(ROOT / "tests" / "golden" / "verification.csv")

rows = []
for region in ingest.REGIONS:
    p = FIX / f"PRICE_AND_DEMAND_202606_{region}1.csv"
    if not p.exists():
        continue
    df = ingest.load_raw_csv(p)
    rows.extend(spreads.compute_region(df, region, start, end, fw))
comp = pd.DataFrame(rows)

m = comp.merge(gold, on=["region", "method", "battery"], suffixes=("_calc", "_excel"))
out = pd.DataFrame({
    "region": m.region, "method": m.method, "battery": m.battery,
    "charge_calc": m.charge_calc.round(1), "charge_excel": m.charge_excel,
    "discharge_calc": m.discharge_calc.round(1), "discharge_excel": m.discharge_excel,
    "spread_calc": m.spread_calc.round(1), "spread_excel": m.spread_excel,
})
out["spread_diff"] = (out.spread_calc.round() - out.spread_excel).astype("Int64")


def flag(r):
    if r.region == "VIC" and r.method == "fixed_time":
        return "VIC fixed: Excel 값 신뢰불가(placeholder) — 원본 Excel과 직접 대조 필요"
    if abs(r.spread_diff) <= 1:
        return "OK (spread ±1)"
    return "CHECK"


out["flag"] = out.apply(flag, axis=1)
out.to_excel(OUT, index=False)
print(out.to_string(index=False))
print(f"\nSaved: {OUT}")
