"""Daily temperature per region (spec Phase 2d, the climate input).

ISOLATED / BEST-EFFORT: raises only ``WeatherUnavailable`` on failure; never
breaks the core. Source: Open-Meteo (free, no API key) — used as the "BOM 등"
separate climate source. Daily mean 2 m temperature at each region's capital,
which represents the bulk of that region's electricity demand centre.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import requests

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Region demand-centre (capital city) coordinates.
REGION_COORDS = {
    "NSW": (-33.87, 151.21),  # Sydney
    "QLD": (-27.47, 153.03),  # Brisbane
    "VIC": (-37.81, 144.96),  # Melbourne
    "SA": (-34.93, 138.60),   # Adelaide
}
TIMEZONE = "Australia/Sydney"


class WeatherUnavailable(RuntimeError):
    """Raised when temperature data cannot be fetched."""


def fetch_daily_temp(run_date: date, *, days: int = 30) -> pd.DataFrame:
    """Daily mean temperature (°C) per region for the last ``days``.

    Returns tidy [date, region, temp_mean_c]. Uses the forecast endpoint with
    ``past_days`` so the most recent days (which the ERA5 archive lags on) are
    included. Raises WeatherUnavailable on any failure.
    """
    frames = []
    for region, (lat, lon) in REGION_COORDS.items():
        try:
            resp = requests.get(
                OPEN_METEO_URL,
                params={
                    "latitude": lat, "longitude": lon,
                    "daily": "temperature_2m_mean",
                    "past_days": min(days + 1, 92),
                    "forecast_days": 1,
                    "timezone": TIMEZONE,
                },
                timeout=30,
            )
            resp.raise_for_status()
            daily = resp.json()["daily"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise WeatherUnavailable(
                f"기온 데이터를 가져오지 못했습니다 ({region}): {exc}") from exc
        df = pd.DataFrame({
            "date": pd.to_datetime(daily["time"]),
            "region": region,
            "temp_mean_c": daily["temperature_2m_mean"],
        })
        frames.append(df)

    out = pd.concat(frames, ignore_index=True)
    cutoff = pd.Timestamp(run_date - timedelta(days=days))
    out = out[(out["date"] >= cutoff) & (out["date"] <= pd.Timestamp(run_date))]
    return out.reset_index(drop=True)
