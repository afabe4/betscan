"""Pricing math: conversions, de-vigging, expected value, Kelly sizing."""
from __future__ import annotations

from statistics import median


def american_to_decimal(price: float) -> float:
    if price >= 100:
        return 1 + price / 100
    if price <= -100:
        return 1 + 100 / abs(price)
    raise ValueError(f"invalid American price: {price}")


def decimal_to_american(dec: float) -> int:
    if dec >= 2:
        return round((dec - 1) * 100)
    return round(-100 / (dec - 1))


def implied_prob(price: float) -> float:
    return 1 / american_to_decimal(price)


def no_vig(prices: list[float]) -> list[float]:
    """Strip the bookmaker margin from a set of mutually exclusive outcomes
    (multiplicative / proportional method)."""
    raw = [implied_prob(p) for p in prices]
    total = sum(raw)
    return [r / total for r in raw]


def expected_value(fair_prob: float, price: float) -> float:
    """EV per unit staked, e.g. 0.031 == +3.1%."""
    return fair_prob * american_to_decimal(price) - 1


def kelly_fraction(fair_prob: float, price: float) -> float:
    b = american_to_decimal(price) - 1
    f = (fair_prob * b - (1 - fair_prob)) / b
    return max(0.0, f)


# --- point adjustments -----------------------------------------------------
# Approximate win-probability value of one point of spread/total, per sport.
# These are rough market heuristics, used only when two books hang different
# numbers; results built on them are marked "approx".
POINT_VALUE = {
    "nfl": {"spreads": 0.030, "totals": 0.022},
    "ncaaf": {"spreads": 0.027, "totals": 0.020},
    "nba": {"spreads": 0.030, "totals": 0.020},
    "ncaab": {"spreads": 0.030, "totals": 0.020},
    "nhl": {"spreads": 0.0, "totals": 0.090},
    "mlb": {"spreads": 0.0, "totals": 0.070},
}
# Extra probability for crossing NFL key numbers (share of games decided by it).
NFL_KEY_NUMBERS = {3: 0.075, 7: 0.045, 10: 0.025, 6: 0.02, 4: 0.02, 14: 0.02}


def shift_prob(prob: float, from_point: float, to_point: float, sport: str,
               market: str) -> float:
    """Shift a cover probability when moving from one line to another.

    `point` is the outcome's own handicap (e.g. -3.5 for a favorite, +3.5 dog;
    for totals, Over uses +total semantics handled by the caller). A larger
    point is always better for the bettor.
    """
    per_pt = POINT_VALUE.get(sport, {}).get(market, 0.025)
    delta = (to_point - from_point) * per_pt
    if sport in ("nfl", "ncaaf") and market == "spreads":
        lo, hi = sorted((from_point, to_point))
        sign = 1 if to_point > from_point else -1
        for key, val in NFL_KEY_NUMBERS.items():
            for k in (key, -key):
                # Moving across or onto/off a key number.
                if lo < k < hi:
                    delta += sign * val
                elif k in (lo, hi) and lo != hi:
                    delta += sign * val / 2
    return min(0.99, max(0.01, prob + delta))


def consensus(values: list[float]) -> float | None:
    return median(values) if values else None
