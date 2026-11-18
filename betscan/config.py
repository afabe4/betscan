"""Sport definitions and tunable thresholds."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Sport:
    code: str           # our short code
    odds_key: str       # The Odds API sport key
    espn_path: str      # ESPN site API path, e.g. football/nfl
    outdoor_weather: bool
    back_to_back: bool  # sport where 0-rest games are common (NBA/NHL)


SPORTS = {
    "nfl": Sport("nfl", "americanfootball_nfl", "football/nfl", True, False),
    "ncaaf": Sport("ncaaf", "americanfootball_ncaaf", "football/college-football", True, False),
    "nba": Sport("nba", "basketball_nba", "basketball/nba", False, True),
    "ncaab": Sport("ncaab", "basketball_ncaab", "basketball/mens-college-basketball", False, False),
    "nhl": Sport("nhl", "icehockey_nhl", "hockey/nhl", False, True),
    "mlb": Sport("mlb", "baseball_mlb", "baseball/mlb", True, False),
}

# Books treated as "sharp" (lowest margin, fastest to move). The first one
# present in a game's odds becomes the fair-price reference.
SHARP_BOOKS = ["pinnacle", "circasports", "lowvig", "betonlineag", "bookmaker"]

# Books you can bet at. Everything else (Pinnacle, EU books, exchanges) is only used
# as the fair-price benchmark. Default: US state-regulated books on The Odds API.
US_REGULATED = ("draftkings", "fanduel", "betmgm", "williamhill_us", "espnbet", "betrivers",
                "fanatics", "hardrockbet", "ballybet", "betparx", "fliff", "windcreek")
MY_BOOKS = ("draftkings", "fanduel")   # where picks and teasers are placed (--books to change)

# Key positions per sport, with a rough importance weight 0-1.
KEY_POSITIONS = {
    "nfl": {"QB": 1.0, "LT": 0.35, "WR": 0.3, "RB": 0.2, "TE": 0.2, "CB": 0.25,
            "EDGE": 0.3, "DE": 0.3, "OT": 0.3, "C": 0.2, "S": 0.15, "LB": 0.15},
    "ncaaf": {"QB": 1.0, "WR": 0.25, "RB": 0.2},
    "nba": {"PG": 0.5, "SG": 0.4, "SF": 0.4, "PF": 0.4, "C": 0.45, "G": 0.4, "F": 0.4},
    "ncaab": {"G": 0.35, "F": 0.35, "C": 0.35},
    "nhl": {"G": 0.8, "C": 0.3, "D": 0.25, "LW": 0.2, "RW": 0.2},
    "mlb": {"SP": 1.0},
}


@dataclass
class Settings:
    odds_api_key: str | None = field(default_factory=lambda: os.environ.get("ODDS_API_KEY"))
    regions: str = "us,eu"          # eu brings in Pinnacle
    markets: str = "spreads,totals"  # moneylines off: 4 credits/scan instead of 6
    hours_ahead: int = 96           # only scan games starting within this window
    min_ev: float = 0.015           # 1.5% EV to call something a BET
    min_lean_score: float = 2.0     # situational score to call something a LEAN
    news_hours: int = 48            # how far back news/injury reports count
    bankroll: float = 100.0         # your bankroll in dollars; stakes scale with it
    kelly_mult: float = 0.25        # quarter Kelly
    max_stake_pct: float = 0.03     # cap any single stake at 3% of bankroll
    wind_mph: float = 15.0          # football wind threshold
    bet_books: tuple | None = MY_BOOKS   # None = every book counts as bettable
    model_weight: float = 0.0       # efficiency model's share of the score (0 = context only)
    cache_dir: Path = field(default_factory=lambda: Path(
        os.environ.get("BETSCAN_HOME", Path.home() / ".betscan")))

    @property
    def db_path(self) -> Path:
        return self.cache_dir / "snapshots.sqlite"
