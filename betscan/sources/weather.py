"""Open-Meteo geocoding + hourly forecast (free, no key)."""
from __future__ import annotations

from datetime import datetime, timezone

from ..http import Fetcher

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"


def pick_hour(data: dict, when: datetime) -> dict | None:
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return None
    target = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:00")
    idx = times.index(target) if target in times else None
    if idx is None:
        return None
    return {
        "temp_f": hourly["temperature_2m"][idx],
        "wind_mph": hourly["wind_speed_10m"][idx],
        "gust_mph": hourly.get("wind_gusts_10m", [None] * len(times))[idx],
        "precip_in": hourly["precipitation"][idx],
        "precip_prob": hourly.get("precipitation_probability", [None] * len(times))[idx],
    }


class Weather:
    def __init__(self, fetcher: Fetcher):
        self.f = fetcher

    def geocode(self, city: str, state: str | None) -> tuple[float, float] | None:
        res = self.f.get_json(GEO, {"name": city, "count": 10, "country_code": "US"}, ttl=30 * 86400)
        results = res.get("results") or []
        if state:
            st = [r for r in results if state.lower() in (r.get("admin1", "").lower(),
                                                           r.get("admin1_code", "").lower())]
            results = st or results
        if not results:
            return None
        return results[0]["latitude"], results[0]["longitude"]

    def at_kickoff(self, city: str, state: str | None, when: datetime) -> dict | None:
        loc = self.geocode(city, state)
        if not loc:
            return None
        data = self.f.get_json(FORECAST, {
            "latitude": loc[0], "longitude": loc[1], "timezone": "UTC",
            "hourly": "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation,precipitation_probability",
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
            "forecast_days": 7,
        }, ttl=3 * 3600)
        return pick_hour(data, when)
