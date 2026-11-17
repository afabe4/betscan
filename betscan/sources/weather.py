"""Kickoff weather: National Weather Service (primary, US) with Open-Meteo fallback.

Wind is averaged over the first three hours from kickoff (gusts: the max), which
is closer to what nflverse's historical game-time wind reading represents.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..http import Fetcher

GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
NWS = "https://api.weather.gov"
NWS_HEADERS = {"Accept": "application/geo+json"}
NWS_RETRY = (404, 429, 500, 502, 503, 504)   # NWS gridpoints return spurious 404s
KMH_TO_MPH = 0.621371

# Home stadium coordinates, keyed by the team's full name (as the odds feed spells it).
STADIUMS = {
    "Arizona Cardinals": (33.5276, -112.2626), "Atlanta Falcons": (33.7554, -84.4008),
    "Baltimore Ravens": (39.2780, -76.6227), "Buffalo Bills": (42.7738, -78.7870),
    "Carolina Panthers": (35.2258, -80.8528), "Chicago Bears": (41.8623, -87.6167),
    "Cincinnati Bengals": (39.0955, -84.5160), "Cleveland Browns": (41.5061, -81.6995),
    "Dallas Cowboys": (32.7473, -97.0945), "Denver Broncos": (39.7439, -105.0201),
    "Detroit Lions": (42.3400, -83.0456), "Green Bay Packers": (44.5013, -88.0622),
    "Houston Texans": (29.6847, -95.4107), "Indianapolis Colts": (39.7601, -86.1639),
    "Jacksonville Jaguars": (30.3239, -81.6373), "Kansas City Chiefs": (39.0489, -94.4839),
    "Las Vegas Raiders": (36.0909, -115.1833), "Los Angeles Chargers": (33.9535, -118.3392),
    "Los Angeles Rams": (33.9535, -118.3392), "Miami Dolphins": (25.9580, -80.2389),
    "Minnesota Vikings": (44.9737, -93.2581), "New England Patriots": (42.0909, -71.2643),
    "New Orleans Saints": (29.9511, -90.0812), "New York Giants": (40.8135, -74.0745),
    "New York Jets": (40.8135, -74.0745), "Philadelphia Eagles": (39.9008, -75.1675),
    "Pittsburgh Steelers": (40.4468, -80.0158), "San Francisco 49ers": (37.4030, -121.9700),
    "Seattle Seahawks": (47.5952, -122.3316), "Tampa Bay Buccaneers": (27.9759, -82.5033),
    "Tennessee Titans": (36.1665, -86.7713), "Washington Commanders": (38.9076, -76.8645),
}

_DUR = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?")


def _interval(valid: str) -> tuple[datetime, datetime]:
    start, dur = valid.split("/")
    m = _DUR.fullmatch(dur)
    d, h, mi = (int(x or 0) for x in m.groups()) if m else (0, 1, 0)
    s = datetime.fromisoformat(start)
    return s, s + timedelta(days=d, hours=h, minutes=mi)


def _series_at(values: list[dict], hours: list[datetime]) -> list[float]:
    out = []
    for t in hours:
        for v in values:
            s, e = _interval(v["validTime"])
            if s <= t < e and v.get("value") is not None:
                out.append(v["value"])
                break
    return out


def parse_nws_grid(props: dict, when: datetime) -> dict | None:
    """NWS gridpoint forecast -> kickoff summary in mph / °F / inches."""
    k = when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hours = [k + timedelta(hours=i) for i in range(3)]
    wind = _series_at(props.get("windSpeed", {}).get("values", []), hours)
    if not wind:
        return None
    gust = _series_at(props.get("windGust", {}).get("values", []), hours)
    temp = _series_at(props.get("temperature", {}).get("values", []), hours[:1])
    pop = _series_at(props.get("probabilityOfPrecipitation", {}).get("values", []), hours[:1])
    qpf = []
    for v in props.get("quantitativePrecipitation", {}).get("values", []):
        s, e = _interval(v["validTime"])
        if s <= k < e and v.get("value") is not None:
            qpf.append(v["value"] / max(1.0, (e - s).total_seconds() / 3600))  # mm per hour
    return {
        "wind_mph": round(sum(wind) / len(wind) * KMH_TO_MPH, 1),
        "gust_mph": round(max(gust) * KMH_TO_MPH, 1) if gust else None,
        "temp_f": round(temp[0] * 9 / 5 + 32, 1) if temp else None,
        "precip_in": round(qpf[0] / 25.4, 3) if qpf else None,
        "precip_prob": pop[0] if pop else None,
        "source": "NWS",
    }


def pick_hour(data: dict, when: datetime) -> dict | None:
    """Open-Meteo hourly -> same summary (3-hour wind mean from kickoff)."""
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    k = when.astimezone(timezone.utc)
    idx = [times.index(t) for t in ((k + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(3))
           if t in times]
    if not idx:
        return None
    i0 = idx[0]
    gusts = hourly.get("wind_gusts_10m") or [None] * len(times)
    g = [gusts[i] for i in idx if gusts[i] is not None]
    return {
        "temp_f": hourly["temperature_2m"][i0],
        "wind_mph": round(sum(hourly["wind_speed_10m"][i] for i in idx) / len(idx), 1),
        "gust_mph": max(g) if g else None,
        "precip_in": hourly["precipitation"][i0],
        "precip_prob": (hourly.get("precipitation_probability") or [None] * len(times))[i0],
        "source": "Open-Meteo",
    }


class Weather:
    def __init__(self, fetcher: Fetcher):
        self.f = fetcher

    def geocode(self, city: str, state: str | None) -> tuple[float, float] | None:
        res = self.f.get_json(GEO, {"name": city, "count": 10}, ttl=30 * 86400)
        results = res.get("results") or []
        if state:
            st = [r for r in results if state.lower() in (r.get("admin1", "").lower(),
                                                           r.get("admin1_code", "").lower(),
                                                           r.get("country", "").lower())]
            results = st or results
        if not results:
            return None
        return results[0]["latitude"], results[0]["longitude"]

    def nws(self, lat: float, lon: float, when: datetime) -> dict | None:
        pt = self.f.get_json(f"{NWS}/points/{lat:.4f},{lon:.4f}", ttl=30 * 86400, headers=NWS_HEADERS,
                             retry_on=NWS_RETRY)
        grid = pt["properties"]["forecastGridData"]
        data = self.f.get_json(grid, ttl=2 * 3600, headers=NWS_HEADERS, retry_on=NWS_RETRY)
        return parse_nws_grid(data["properties"], when)

    def open_meteo(self, lat: float, lon: float, when: datetime) -> dict | None:
        data = self.f.get_json(FORECAST, {
            "latitude": lat, "longitude": lon, "timezone": "UTC",
            "hourly": "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation,precipitation_probability",
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
            "forecast_days": 7,
        }, ttl=3 * 3600)
        return pick_hour(data, when)

    def at_kickoff(self, city: str, state: str | None, when: datetime, home: str | None = None,
                   neutral: bool = False) -> dict | None:
        loc = None if neutral else STADIUMS.get(home or "")
        errors = []
        if loc is None:
            try:
                loc = self.geocode(city, state)
            except Exception as e:  # noqa: BLE001
                errors.append(f"geocode: {e}")
        if loc is None:
            raise RuntimeError("; ".join(errors) or f"could not locate {city}")
        for fn in (self.nws, self.open_meteo):
            try:
                w = fn(loc[0], loc[1], when)
                if w:
                    return w
            except Exception as e:  # noqa: BLE001 - NWS is US-only; fall through
                errors.append(f"{fn.__name__}: {e}")
        if errors:
            raise RuntimeError("; ".join(errors))
        return None   # kickoff beyond the forecast horizon
