"""CLI entry point (dev/verification).

    python run.py --date 2026-06-15
    python run.py --date 2026-06-15 --no-download   # use cached CSVs only
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from src import report

# Windows consoles default to cp949/cp1252 and choke on non-ASCII; force UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="NEM Weekly Spread Monitor")
    p.add_argument("--date", required=True,
                   help="run date (a Monday), YYYY-MM-DD, e.g. 2026-06-15")
    p.add_argument("--no-download", action="store_true",
                   help="do not auto-download; use cached CSVs in data/raw/")
    p.add_argument("--excel", metavar="PATH",
                   help="also write the report to an .xlsx file")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    run_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    rep = report.build_report(run_date, auto_download=not args.no_download)

    start, end = rep["window"]
    print(f"\nNEM Weekly Spread Monitor - week starting {rep['week_start']}")
    print(f"Window: {start} .. {end}   Regions: {', '.join(rep['regions'])}\n")

    print("Resolution check (must be 5-min / 288 per day):")
    print(rep["resolution"].to_string(index=False))
    if not rep["resolution"]["is_5min"].all():
        print("  ** WARNING: a region is NOT 5-minute resolution — see spec 2-1. **")

    print("\nWeekly spreads (AUD/MWh):")
    print(rep["spreads"].round(1).to_string(index=False))

    print("\nDemand by band (avg MW):")
    print(rep["demand"].round(0).to_string(index=False))

    if args.excel:
        with open(args.excel, "wb") as fh:
            fh.write(report.to_excel_bytes(rep))
        print(f"\nWrote {args.excel}")


if __name__ == "__main__":
    main()
