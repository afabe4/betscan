"""Historical NFL base rates from nflverse closing lines (2006 -> last full season).

Everything here is computed from data at runtime, not hard-coded, so the rates
update as seasons are added. `python -m betscan angles` prints the table.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone

from .http import Fetcher

GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
FIRST_SEASON = 2006

# Teaser rule: underdog legs whose spread is +1.5..+2.5, teased 6 pts through 3 and 7,
# only when the game total is at or below this (higher totals failed historically).
TEASER_DOG_RANGE = (1.5, 2.5)
TEASER_FAV_RANGE = (-8.5, -7.5)
TEASER_MAX_TOTAL = 48.5
WIND_BUCKETS = ((10.0, 15.0), (15.0, 99.0))


def load_games(fetcher: Fetcher) -> list[dict]:
    raw = fetcher.get_bytes(GAMES_URL, ttl=6 * 3600, timeout=60)
    return list(csv.DictReader(io.StringIO(raw.decode())))


def _f(v):
    return None if v in ("", "NA", None) else float(v)


@dataclass
class Rate:
    wins: int
    losses: int

    @property
    def n(self):
        return self.wins + self.losses

    @property
    def p(self):
        return self.wins / self.n if self.n else 0.5

    def __str__(self):
        return f"{self.wins}-{self.losses} ({self.p:.1%})"


@dataclass
class HistoricalRates:
    teaser_dog: Rate          # qualifying dog legs, total <= TEASER_MAX_TOTAL
    teaser_dog_high_total: Rate
    teaser_fav: Rate
    wind: dict                # (lo, hi) -> Rate of unders
    seasons: tuple[int, int]

    def wind_rate(self, mph: float) -> Rate | None:
        for (lo, hi), r in self.wind.items():
            if lo <= mph < hi:
                return r
        return None


def compute_rates(games: list[dict], last_season: int) -> HistoricalRates:
    dog, dog_hi, fav = Rate(0, 0), Rate(0, 0), Rate(0, 0)
    wind = {b: Rate(0, 0) for b in WIND_BUCKETS}
    for g in games:
        if g.get("game_type") != "REG" and g.get("game_type") not in ("WC", "DIV", "CON", "SB"):
            continue
        season = int(g["season"])
        if not FIRST_SEASON <= season <= last_season:
            continue
        res, spread, tl, tot = _f(g["result"]), _f(g["spread_line"]), _f(g["total_line"]), _f(g["total"])
        if res is None or spread is None:
            continue
        for home in (True, False):
            hcap = -spread if home else spread
            margin = res if home else -res
            cover = margin + hcap + 6
            if cover == 0 or tl is None:
                continue
            if TEASER_DOG_RANGE[0] <= hcap <= TEASER_DOG_RANGE[1]:
                r = dog if tl <= TEASER_MAX_TOTAL else dog_hi
            elif TEASER_FAV_RANGE[0] <= hcap <= TEASER_FAV_RANGE[1]:
                r = fav
            else:
                continue
            if cover > 0:
                r.wins += 1
            else:
                r.losses += 1
        w = _f(g.get("wind"))
        if g.get("roof") in ("outdoors", "open") and w is not None and tl is not None and tot is not None \
                and tot != tl:
            for (lo, hi), r in wind.items():
                if lo <= w < hi:
                    if tot < tl:
                        r.wins += 1
                    else:
                        r.losses += 1
    return HistoricalRates(dog, dog_hi, fav, wind, (FIRST_SEASON, last_season))


def last_complete_season(now: datetime) -> int:
    # The NFL season ends in February; a season counts once it is over.
    return now.year - 1 if now.month >= 3 else now.year - 2


def get_rates(fetcher: Fetcher, now: datetime | None = None) -> HistoricalRates:
    now = now or datetime.now(timezone.utc)
    return compute_rates(load_games(fetcher), last_complete_season(now))


# Odds API full names -> nflverse abbreviations, for grading.
def nfl_abbr_map() -> dict[str, str]:
    from .efficiency.providers import NFL_TEAMS
    m = {v: k for k, v in NFL_TEAMS.items()}
    m.update({"Oakland Raiders": "OAK", "San Diego Chargers": "SD", "St. Louis Rams": "STL",
              "Washington Football Team": "WAS", "Washington Redskins": "WAS"})
    return m


# Offline fallback: the same computation run on nflverse data for 2006-2025.
FALLBACK_RATES = HistoricalRates(
    teaser_dog=Rate(441, 122), teaser_dog_high_total=Rate(79, 35), teaser_fav=Rate(257, 95),
    wind={(10.0, 15.0): Rate(470, 387), (15.0, 99.0): Rate(259, 200)}, seasons=(2006, 2025))
