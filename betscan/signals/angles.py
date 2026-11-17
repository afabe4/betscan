"""Data-backed NFL angles: forecast-wind unders and Wong-style teasers.

Unlike the heuristic signals, these move the fair probability itself, using
historical rates from nflverse (see history.py), so EV and staking reflect them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from statistics import median

from ..history import TEASER_DOG_RANGE, TEASER_FAV_RANGE, TEASER_MAX_TOTAL, HistoricalRates
from ..models import Candidate, Game, Signal
from ..odds_math import american_to_decimal, expected_value

# Historical wind is measured at the game; we bet on a forecast. Keep only part of the
# historical edge, less the further out the forecast is.
FORECAST_RELIABILITY = ((24, 0.85), (48, 0.75), (96, 0.6), (9999, 0.4))


def _reliability(hours_out: float) -> float:
    for limit, r in FORECAST_RELIABILITY:
        if hours_out <= limit:
            return r
    return 0.4


def wind_adjust(game: Game, cands: dict, rates: HistoricalRates | None, now: datetime,
                total_move: float | None = None) -> Signal | None:
    """Shift totals fair probability toward the Under when the forecast is windy."""
    if rates is None or game.sport != "nfl" or game.indoor or not game.weather:
        return None
    w = game.weather
    wind = max(w.get("wind_mph") or 0, (w.get("gust_mph") or 0) * 0.7)
    rate = rates.wind_rate(wind)
    if rate is None or rate.n < 50 or rate.p <= 0.5:
        return None
    hours = (game.commence - now).total_seconds() / 3600
    rel = _reliability(hours)
    boost = (rate.p - 0.5) * rel
    for side, sign in (("Under", 1), ("Over", -1)):
        c = cands.get(("totals", side))
        if c and c.fair_prob is not None:
            c.fair_prob = min(0.99, max(0.01, c.fair_prob + sign * boost))
            c.ev = expected_value(c.fair_prob, c.best_price)
    if total_move is None:
        moved = ""
    elif total_move > 0:
        moved = f"; total already down {total_move:.1f} since first seen (edge held vs closing totals anyway)"
    else:
        moved = f"; total {'unchanged' if total_move == 0 else f'up {-total_move:.1f}'} since first seen"
    detail = (f"forecast wind {w.get('wind_mph', 0):.0f} mph (gusts {w.get('gust_mph') or 0:.0f}) "
              f"at {game.venue or game.city}. Unders in this wind: {rate} since {rates.seasons[0]} "
              f"(vs closing totals). Kickoff in {hours:.0f}h, so {rel:.0%} of that edge kept: "
              f"+{boost:.1%} to Under{moved}")
    return Signal("wind", "totals", "Under", 0.0, detail)


# --- teasers ------------------------------------------------------------------

@dataclass
class TeaserLeg:
    game: Game
    side: str
    book: str
    point: float
    teased: float
    total: float | None
    prob: float
    rate_label: str

    @property
    def label(self) -> str:
        return f"{self.side} {self.point:+g} -> {self.teased:+g}"


@dataclass
class Teaser:
    book: str
    legs: list[TeaserLeg]
    price: float
    prob: float
    ev: float
    stake: float = 0.0
    notes: list[str] = field(default_factory=list)


def _consensus_total(game: Game) -> float | None:
    pts = [o.point for m in game.books.values() for o in m.get("totals", []) if o.point is not None]
    return median(pts) if pts else None


def teaser_legs(games: list[Game], rates: HistoricalRates, include_favs: bool = False,
                teaser_points: float = 6.0, bet_books=None) -> list[TeaserLeg]:
    legs = []
    for g in games:
        if g.sport != "nfl":
            continue
        total = _consensus_total(g)
        for book, mk in g.books.items():
            if bet_books is not None and book not in bet_books:
                continue
            for o in mk.get("spreads", []):
                if o.point is None:
                    continue
                if TEASER_DOG_RANGE[0] <= o.point <= TEASER_DOG_RANGE[1]:
                    if total is None or total > TEASER_MAX_TOTAL:
                        continue
                    rate, lab = rates.teaser_dog, "dog legs, total <= 48.5"
                elif include_favs and TEASER_FAV_RANGE[0] <= o.point <= TEASER_FAV_RANGE[1]:
                    rate, lab = rates.teaser_fav, "favorite legs"
                else:
                    continue
                legs.append(TeaserLeg(g, o.name, book, o.point, o.point + teaser_points, total,
                                      rate.p, f"{lab}: {rate}"))
    return legs


def best_teasers(legs: list[TeaserLeg], price2: float = -120, price3: float = 160,
                 bankroll: float = 1000, kelly_mult: float = 0.25, max_pct: float = 0.03,
                 min_ev: float = 0.0) -> list[Teaser]:
    """Every same-book 2- and 3-team combo (one leg per game) with positive EV.
    Assumes legs are independent, which holds well across different games."""
    by_book: dict[str, list[TeaserLeg]] = {}
    for leg in legs:
        by_book.setdefault(leg.book, []).append(leg)
    out = []
    for book, bl in by_book.items():
        for n, price in ((2, price2), (3, price3)):
            for combo in combinations(bl, n):
                if len({leg.game.id for leg in combo}) < n:
                    continue
                p = 1.0
                for leg in combo:
                    p *= leg.prob
                ev = expected_value(p, price)
                if ev <= min_ev:
                    continue
                b = american_to_decimal(price) - 1
                kelly = max(0.0, (p * b - (1 - p)) / b) * kelly_mult
                out.append(Teaser(book, list(combo), price, p, ev,
                                  round(min(kelly, max_pct) * bankroll, 2)))
    out.sort(key=lambda t: -t.ev)
    merged: dict = {}
    for t in out:   # same legs at several books -> one row listing the books
        key = (len(t.legs), frozenset((leg.game.id, leg.side, leg.teased) for leg in t.legs))
        if key in merged:
            merged[key].book += f", {t.book}"
        else:
            merged[key] = t
    return list(merged.values())


def breakeven_leg(price: float, n: int) -> float:
    return (1 / american_to_decimal(price)) ** (1 / n)
