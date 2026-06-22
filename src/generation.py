"""Open Electricity generation tracker — reproduces the OE "Energy (GWh/day)" chart.

ISOLATED / BEST-EFFORT: every public function raises only ``OEUnavailable`` on
failure. The caller (app) catches it; the deterministic core never depends on this.

Layout (like the Open Electricity tracker):
  * daily stacked bars, x = last 30 days, y = GWh/day.
  * ABOVE zero: delivered generation by fueltech (coal/gas/hydro/wind/solar incl
    rooftop/bioenergy/distillate/battery discharging) + interconnector imports.
  * BELOW zero (negative): loads — battery charging, pumping, exports.
  * Interconnector flow isn't a fueltech, so net flow is DERIVED:
    net_import = demand_energy - (delivered - loads); imports = max(net,0),
    exports = min(net,0).

The API reports charging/pumping as POSITIVE magnitudes, so loads are negated here.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

DEFAULT_BASE_URL = "https://api.openelectricity.org.au/v4"
TARGET_REGIONS = ["NSW", "QLD", "VIC", "SA"]  # TAS excluded (spec)

# Map OE detailed fueltech codes -> our display groups (gas/coal/bioenergy merged,
# solar split into utility/rooftop). The net "battery" code is dropped to avoid
# double-counting its charging/discharging components.
FUELTECH_MAP = {
    "coal_black": "coal", "coal_brown": "coal",
    "gas_ccgt": "gas", "gas_ocgt": "gas", "gas_recip": "gas",
    "gas_steam": "gas", "gas_wcmg": "gas",
    "distillate": "distillate",
    "bioenergy_biomass": "bioenergy", "bioenergy_biogas": "bioenergy",
    "hydro": "hydro", "wind": "wind",
    "solar_utility": "solar_utility", "solar_rooftop": "solar_rooftop",
    "battery_discharging": "battery_discharging",
    "battery_charging": "battery_charging", "pumps": "pumps",
}
# Delivered generation (plotted positive), stack order nearest-zero -> outward.
GENERATION_GROUPS = ["coal", "hydro", "bioenergy", "distillate", "gas",
                     "battery_discharging", "wind", "solar_utility", "solar_rooftop"]
# Loads (plotted negative; API gives positive magnitudes so we negate).
LOAD_GROUPS = ["battery_charging", "pumps"]

# OpenNEM/Open Electricity-style fueltech palette.
PALETTE = {
    "coal": "#251000", "gas": "#F48E1B", "distillate": "#E2674E",
    "bioenergy": "#1C7A3D", "hydro": "#4582B4", "wind": "#417505",
    "solar_utility": "#FDB813", "solar_rooftop": "#FFE26F",
    "battery_discharging": "#00A2FA", "imports": "#7E57C2",
    "battery_charging": "#9BD3F0", "pumps": "#88B0D8", "exports": "#CDB4E6",
}
LABELS = {
    "coal": "Coal", "gas": "Gas", "distillate": "Distillate",
    "bioenergy": "Bioenergy", "hydro": "Hydro", "wind": "Wind",
    "solar_utility": "Solar (Utility)", "solar_rooftop": "Solar (Rooftop)",
    "battery_discharging": "Battery (Discharging)", "imports": "Imports",
    "battery_charging": "Battery (Charging)", "pumps": "Pumps", "exports": "Exports",
}
# Stack order: positives nearest-zero -> outward (Imports on top), then loads.
PLOT_ORDER = GENERATION_GROUPS + ["imports", "battery_charging", "pumps", "exports"]


class OEUnavailable(RuntimeError):
    """Raised when Open Electricity data cannot be fetched (key missing/network)."""


# --------------------------------------------------------------------------- #
# Key handling — src stays framework-agnostic; key is injected or read from env.
# --------------------------------------------------------------------------- #
def load_api_key(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    key = os.environ.get("OPENELECTRICITY_API_KEY")
    if key:
        return key
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("OPENELECTRICITY_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def _region_code(name: str) -> str:
    name = name.upper()
    return name[:-1] if name.endswith("1") else name


# --------------------------------------------------------------------------- #
# Raw fetch
# --------------------------------------------------------------------------- #
def _fetch_network(metrics: list[str], date_start: date, date_end: date,
                   *, api_key: str, base_url: str, secondary_grouping: str | None,
                   path: str = "data"):
    """One GET /{path}/network/NEM call -> parsed JSON 'data' list."""
    params = {
        "metrics": metrics, "interval": "1d",
        "primary_grouping": "network_region",
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
    rows = []
    for s in data_entry.get("results", []):
        name = s["name"]                      # 'energy_NSW1|battery' or 'demand_energy_NSW1'
        left, _, group = name.partition("|")  # left ends with REGION; group=fueltech
        region = _region_code(left.split("_")[-1])
        for ts, val in s["data"]:
            rows.append({"date": pd.to_datetime(ts).tz_localize(None).normalize(),
                         "region": region, "group": group or value_name,
                         value_name: val})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Public: 30-day tracker data
# --------------------------------------------------------------------------- #
def fetch_generation(run_date: date, *, days: int = 30, api_key: str | None = None,
                     base_url: str = DEFAULT_BASE_URL) -> dict:
    """Daily signed generation (GWh) for the OE-style tracker, last ``days``.

    Returns:
        daily   : tidy [date, region, group, gwh] — generation/imports positive,
                  loads/exports negative; ready for a relative stacked bar.
        weekly  : [지역, 항목, 지난주, 이번주, 증감] (GWh) for solar/wind/gas/순수입.
        regions : list of regions present.
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
                            secondary_grouping="fueltech")
    demand = _fetch_network(["demand_energy"], start, end, api_key=key,
                            base_url=base_url, secondary_grouping=None, path="market")

    gen = _series_to_long(energy[0], "mwh")
    gen = gen[gen["region"].isin(TARGET_REGIONS)]
    gen["group"] = gen["group"].map(FUELTECH_MAP)
    gen = gen.dropna(subset=["group"])  # drops net "battery" and any unmapped code
    wide = (gen.pivot_table(index=["date", "region"], columns="group",
                            values="mwh", aggfunc="sum").fillna(0.0))
    for col in GENERATION_GROUPS + LOAD_GROUPS:
        if col not in wide.columns:
            wide[col] = 0.0

    # demand_energy is GWh on the market endpoint -> MWh
    dem = _series_to_long(demand[0], "mwh")
    dem = dem[dem["region"].isin(TARGET_REGIONS)]
    dem = dem.set_index(["date", "region"])["mwh"] * 1000.0

    delivered = wide[GENERATION_GROUPS].sum(axis=1)
    loads = wide[LOAD_GROUPS].sum(axis=1)
    net_import = dem.reindex(wide.index).fillna(0.0) - (delivered - loads)

    signed = pd.DataFrame(index=wide.index)
    for col in GENERATION_GROUPS:
        signed[col] = wide[col]
    signed["battery_charging"] = -wide["battery_charging"]
    signed["pumps"] = -wide["pumps"]
    signed["imports"] = net_import.clip(lower=0)
    signed["exports"] = net_import.clip(upper=0)
    signed = signed / 1000.0  # MWh -> GWh

    daily = (signed.reset_index()
             .melt(id_vars=["date", "region"], var_name="group", value_name="gwh"))
    regions = [r for r in TARGET_REGIONS if r in set(daily["region"])]
    weekly = _weekly_summary(wide, net_import, run_date)
    return {"daily": daily, "weekly": weekly, "regions": regions}


def _weekly_summary(wide: pd.DataFrame, net_import: pd.Series,
                    run_date: date) -> pd.DataFrame:
    """solar / wind / gas / 순수입(net import) — this week vs last week (GWh)."""
    this_lo = pd.Timestamp(run_date - timedelta(days=7))
    prev_lo = pd.Timestamp(run_date - timedelta(days=14))
    this_hi = pd.Timestamp(run_date)

    w = wide.copy()
    w["solar"] = w.get("solar_utility", 0.0) + w.get("solar_rooftop", 0.0)
    w["순수입"] = net_import
    dates = w.index.get_level_values("date")

    def window(lo, hi):
        m = (dates >= lo) & (dates < hi)
        return w[m].groupby(level="region").sum() / 1000.0  # GWh

    this, prev = window(this_lo, this_hi), window(prev_lo, this_lo)
    items = [("solar", "solar"), ("wind", "wind"), ("gas", "gas"),
             ("순수입(±)", "순수입")]
    rows = []
    for region in TARGET_REGIONS:
        if region not in this.index:
            continue
        for label, col in items:
            t = float(this.loc[region, col]) if col in this.columns else float("nan")
            p = (float(prev.loc[region, col])
                 if (region in prev.index and col in prev.columns) else float("nan"))
            rows.append({"지역": region, "항목": label,
                         "지난주": round(p, 1), "이번주": round(t, 1),
                         "증감": round(t - p, 1)})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Plot — OE-style relative stacked bar (built lazily so plotly stays optional)
# --------------------------------------------------------------------------- #
def tracker_figure(daily: pd.DataFrame, region: str, run_date: date):
    """Plotly relative-stacked daily bar for one region (GWh/day), OE-style."""
    import plotly.graph_objects as go

    d = daily[daily["region"] == region]
    pivot = (d.pivot_table(index="date", columns="group", values="gwh",
                           aggfunc="sum").sort_index())

    fig = go.Figure()
    for group in PLOT_ORDER:
        if group not in pivot.columns:
            continue
        fig.add_bar(
            x=pivot.index, y=pivot[group], name=LABELS.get(group, group),
            marker_color=PALETTE.get(group, "#999999"),
            hovertemplate=f"%{{x|%d %b}}<br>{LABELS.get(group, group)}: "
                          f"%{{y:.1f}} GWh<extra></extra>",
        )

    # period average of delivered generation (positive groups only)
    gen_cols = [g for g in GENERATION_GROUPS if g in pivot.columns]
    av = pivot[gen_cols].sum(axis=1).mean() if gen_cols else 0.0

    # divider between last week and this week
    boundary = pd.Timestamp(run_date - timedelta(days=7)) - pd.Timedelta(hours=12)
    fig.add_vline(x=boundary, line_width=2, line_dash="dash", line_color="#444")
    fig.add_annotation(x=boundary, yref="paper", y=1.02, showarrow=False,
                       text="◀ 지난주 | 이번주 ▶", font=dict(size=11, color="#444"))

    fig.update_layout(
        barmode="relative", bargap=0.1,  # thick bars
        height=460, margin=dict(l=10, r=10, t=50, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=-0.28),
        yaxis_title="GWh/day", xaxis_title=None,
        title=dict(text=f"{region} — Av. {av:.0f} GWh/day", x=0.5, xanchor="center"),
    )
    fig.add_hline(y=0, line_width=1, line_color="#888")
    return fig
