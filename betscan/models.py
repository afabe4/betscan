from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Outcome:
    name: str            # team name, "Over" or "Under"
    price: float         # American odds
    point: float | None = None


@dataclass
class Injury:
    team: str
    player: str
    position: str
    status: str          # Out, Doubtful, Questionable, IR...
    reported: datetime | None
    comment: str = ""


@dataclass
class NewsItem:
    headline: str
    description: str
    published: datetime | None
    teams: list[str] = field(default_factory=list)


@dataclass
class Game:
    id: str
    sport: str
    commence: datetime
    home: str
    away: str
    # book -> market -> outcomes
    books: dict[str, dict[str, list[Outcome]]] = field(default_factory=dict)
    # context filled in by enrichment
    venue: str | None = None
    city: str | None = None
    state: str | None = None
    indoor: bool | None = None
    neutral: bool = False
    home_rest_days: float | None = None
    away_rest_days: float | None = None
    injuries: list[Injury] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)
    weather: dict | None = None
    espn_ids: dict[str, str] = field(default_factory=dict)  # team name -> id

    @property
    def label(self) -> str:
        return f"{self.away} @ {self.home}"


@dataclass
class Signal:
    kind: str            # price, line, injury, weather, rest, news, movement
    market: str          # h2h, spreads, totals
    side: str            # outcome name the signal favors
    strength: float      # >0, rough "points of conviction"
    detail: str


@dataclass
class Candidate:
    game: Game
    market: str
    side: str
    point: float | None
    best_book: str | None = None
    best_price: float | None = None
    fair_prob: float | None = None
    ev: float | None = None
    approx: bool = False
    signals: list[Signal] = field(default_factory=list)
    against: list[Signal] = field(default_factory=list)
    score: float = 0.0
    tier: str = "PASS"
    stake: float = 0.0

    @property
    def selection(self) -> str:
        if self.market == "totals":
            return f"{self.side} {self.point:g}"
        if self.market == "spreads" and self.point is not None:
            return f"{self.side} {self.point:+g}"
        return f"{self.side} ML"
