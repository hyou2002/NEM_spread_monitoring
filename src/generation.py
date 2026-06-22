"""Open Electricity generation + derived interconnector net-import (spec Phase 2d).

ISOLATED / BEST-EFFORT: every public function raises only ``OEUnavailable`` on
failure. The caller (app) catches it and shows a notice; the deterministic core
(spreads/demand and sections a/b/c) must never depend on this module.

Data source: Open Electricity API v4, ``GET /v4/data/network/NEM``.
  - one call returns daily energy (MWh) for every NEM region x fueltech_group.
  - the API wants timezone-NAIVE datetimes in network (AEST) time.
  - the API key is COMMUNITY-tier; we keep calls to a minimum and the app caches
    results with @st.cache_data.

Interconnector flow is NOT exposed as a fueltech, so we derive a clearly-labelled
ESTIMATE: net_import = regional demand_energy - regional local generation.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

DEFAULT_BASE_URL = "https://api.openelectricity.org.au/v4"
TARGET_REGIONS = ["NSW", "QLD", "VIC", "SA"]  # TAS excluded (spec)

# fueltech_group classification (battery_charging/_discharging are sub-components
# of the net "battery" group and are dropped to avoid double counting).
RENEWABLE = ["solar", "wind", "hydro", "bioenergy", "battery"]
NON_RENEWABLE = ["coal", "gas", "distillate"]
LOAD_GROUPS = ["pumps"]  # negative (consumption)
SUPPLY_GROUPS = RENEWABLE + NON_RENEWABLE + LOAD_GROUPS
DROP_GROUPS = ["battery_charging", "battery_discharging"]


class OEUnavailable(RuntimeError):
    """Raised when Open Electricity data cannot be fetched (key missing/network)."""


# --------------------------------------------------------------------------- #
# Key handling — src stays framework-agnostic; key is injected or read from env.
# --------------------------------------------------------------------------- #
def load_api_key(explicit: str | None = None) -> str | None:
    """Resolve the API key: explicit arg > env var > local .env (dev only)."""
    if explicit:
        return explicit
    key = os.environ.get("OPENELECTRICITY_API_KEY")
    if key:
        return key
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENELECTRICITY_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def _region_code(name: str) -> str:
    """'NSW1' -> 'NSW'."""
    name = name.upper()
    return name[:-1] if name.endswith("1") else name


# --------------------------------------------------------------------------- #
# Raw fetch
# --------------------------------------------------------------------------- #
def _fetch_network(metrics: list[str], date_start: date, date_end: date,
                   *, api_key: str, base_url: str, secondary_grouping: str | None,
                   path: str = "data"):
    """One GET /{path}/network/NEM call -> parsed JSON 'data' list.

    ``path`` is 'data' for generation/energy (fueltech-groupable) or 'market' for
    demand metrics (demand_energy lives on the market endpoint).
    """
    params = {
        "metrics": metrics,
        "interval": "1d",
        "primary_grouping": "network_region",
        # API requires tz-naive network-time datetimes.
        "date_start": datetime.combine(date_start, datetime.min.time()).isoformat(),
        "date_end": datetime.combine(date_end, datetime.min.time()).isoformat(),
    }
    if secondary_grouping:
        params["secondary_grouping"] = secondary_grouping
    try:
        resp = requests.get(
            f"{base_url}/{path}/network/NEM",
            headers={"Authorization": f"Bearer {api_key}",
                     "Accept": "application/json"},
            params=params, timeout=60,
        )
    except requests.RequestException as exc:
        raise OEUnavailable(f"Open Electricity request failed: {exc}") from exc
    if resp.status_code != 200:
        raise OEUnavailable(
            f"Open Electricity returned {resp.status_code}: {resp.text[:200]}")
    payload = resp.json()
    if not payload.get("success") or "data" not in payload:
        raise OEUnavailable(f"Unexpected Open Electricity response: {payload}")
    return payload["data"]


def _series_to_long(data_entry: dict, value_name: str) -> pd.DataFrame:
    """Flatten one metric's series list into long [date, region, group, value]."""
    rows = []
    for s in data_entry.get("results", []):
        name = s["name"]                      # 'energy_NSW1|battery' or 'demand_energy_NSW1'
        left, _, group = name.partition("|")  # left holds metric..._REGION; group=fueltech
        region = _region_code(left.split("_")[-1])  # last token is the region code
        for ts, val in s["data"]:
            rows.append({
                "date": pd.to_datetime(ts).tz_localize(None).normalize(),
                "region": region,
                "group": group or value_name,
                value_name: val,
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Public: 30-day daily generation + net import
# --------------------------------------------------------------------------- #
def fetch_generation(run_date: date, *, days: int = 30, api_key: str | None = None,
                     base_url: str = DEFAULT_BASE_URL) -> dict:
    """Daily generation by fueltech + derived net-import for the last ``days``.

    Returns a dict of tidy DataFrames (all restricted to NSW/QLD/VIC/SA):
        generation : [date, region, group, energy_mwh]   (supply groups only)
        net_import : [date, region, net_import_mwh]       (= demand - local gen, est.)
        weekly     : [region, period, renewable_mwh, total_mwh, renewable_pct,
                      net_import_mwh] for this week vs last week
    Raises OEUnavailable on any failure.
    """
    key = load_api_key(api_key)
    if not key:
        raise OEUnavailable(
            "OPENELECTRICITY_API_KEY 가 설정되지 않았습니다. "
            "로컬은 .env, 배포는 Streamlit Secrets 에 키를 넣으세요.")

    end = run_date
    start = run_date - timedelta(days=days)

    energy = _fetch_network(["energy"], start, end, api_key=key, base_url=base_url,
                            secondary_grouping="fueltech_group")
    # demand_energy lives on the market endpoint and is reported in GWh, while
    # generation energy is in MWh — rescale demand to MWh before differencing.
    demand = _fetch_network(["demand_energy"], start, end, api_key=key,
                            base_url=base_url, secondary_grouping=None,
                            path="market")

    gen = _series_to_long(energy[0], "energy_mwh")
    gen = gen[gen["region"].isin(TARGET_REGIONS) & ~gen["group"].isin(DROP_GROUPS)]
    supply = gen[gen["group"].isin(SUPPLY_GROUPS)].copy()

    dem = _series_to_long(demand[0], "demand_mwh")
    dem = dem[dem["region"].isin(TARGET_REGIONS)][["date", "region", "demand_mwh"]]
    dem["demand_mwh"] = dem["demand_mwh"] * 1000.0  # GWh -> MWh

    local = (supply.groupby(["date", "region"], as_index=False)["energy_mwh"].sum()
             .rename(columns={"energy_mwh": "local_mwh"}))
    net = local.merge(dem, on=["date", "region"], how="left")
    net["net_import_mwh"] = net["demand_mwh"] - net["local_mwh"]
    net_import = net[["date", "region", "net_import_mwh"]]

    weekly = _weekly_compare(supply, net_import, run_date)
    return {"generation": supply.reset_index(drop=True),
            "net_import": net_import.reset_index(drop=True),
            "weekly": weekly}


def _weekly_compare(supply: pd.DataFrame, net_import: pd.DataFrame,
                    run_date: date) -> pd.DataFrame:
    """This 7-day window vs the prior 7-day window: renewable share + net import."""
    this_start = pd.Timestamp(run_date - timedelta(days=7))
    prev_start = pd.Timestamp(run_date - timedelta(days=14))
    this_end = pd.Timestamp(run_date)

    def agg(s_lo, s_hi, label):
        g = supply[(supply["date"] >= s_lo) & (supply["date"] < s_hi)]
        ni = net_import[(net_import["date"] >= s_lo) & (net_import["date"] < s_hi)]
        out = []
        for region in TARGET_REGIONS:
            gr = g[g["region"] == region]
            renew = gr[gr["group"].isin(RENEWABLE)]["energy_mwh"].sum()
            total = gr[gr["group"].isin(RENEWABLE + NON_RENEWABLE)]["energy_mwh"].sum()
            out.append({
                "region": region, "period": label,
                "renewable_mwh": renew, "total_mwh": total,
                "renewable_pct": (100 * renew / total) if total else float("nan"),
                "net_import_mwh": ni[ni["region"] == region]["net_import_mwh"].sum(),
            })
        return pd.DataFrame(out)

    return pd.concat([agg(prev_start, this_start, "지난주"),
                      agg(this_start, this_end, "이번주")], ignore_index=True)
