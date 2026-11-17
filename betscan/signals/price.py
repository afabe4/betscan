"""Price signal: compare every book's number to a de-vigged fair price.

This is the one signal with a measurable, repeatable edge: if one book hangs
a price that's better than the sharp market's no-vig probability implies,
the bet is +EV regardless of any story behind it.
"""
from __future__ import annotations

from statistics import median

from ..config import SHARP_BOOKS
from ..models import Candidate, Game
from ..odds_math import expected_value, no_vig, shift_prob


def _bettor_point(market: str, side: str, point: float | None) -> float | None:
    """Map a line to 'higher is better for the bettor' semantics."""
    if point is None:
        return None
    if market == "totals":
        return -point if side == "Over" else point
    return point


def _fair_from_book(outcomes) -> dict[str, tuple[float, float | None]]:
    probs = no_vig([o.price for o in outcomes])
    return {o.name: (p, o.point) for o, p in zip(outcomes, probs)}


def reference(game: Game, market: str) -> tuple[str, dict[str, tuple[float, float | None]]] | None:
    """Fair probability per side, from the sharpest available book, else a consensus."""
    for bk in SHARP_BOOKS:
        outs = game.books.get(bk, {}).get(market)
        if outs and len(outs) >= 2:
            return bk, _fair_from_book(outs)
    fairs = [_fair_from_book(m[market]) for m in game.books.values()
             if market in m and len(m[market]) >= 2]
    if len(fairs) < 3:
        return None
    sides = set().union(*fairs)
    out = {}
    for s in sides:
        # Use the most common line, and the median fair prob among books on it.
        pts = [f[s][1] for f in fairs if s in f]
        modal = max(set(pts), key=pts.count) if pts else None
        probs = [f[s][0] for f in fairs if s in f and f[s][1] == modal]
        out[s] = (median(probs), modal)
    return "consensus", out


def price_candidates(game: Game, bet_books=None) -> list[Candidate]:
    cands = []
    markets = {m for b in game.books.values() for m in b}
    for market in sorted(markets):
        ref = reference(game, market)
        if not ref:
            continue
        ref_name, fair = ref
        for side, (ref_prob, ref_point) in fair.items():
            best = None
            for book, mk in game.books.items():
                if book == ref_name or (bet_books is not None and book not in bet_books):
                    continue
                for o in mk.get(market, []):
                    if o.name != side:
                        continue
                    prob, approx = ref_prob, False
                    if o.point != ref_point and o.point is not None and ref_point is not None:
                        prob = shift_prob(ref_prob, _bettor_point(market, side, ref_point),
                                          _bettor_point(market, side, o.point), game.sport, market)
                        approx = True
                    ev = expected_value(prob, o.price)
                    if best is None or ev > best[0]:
                        best = (ev, book, o, prob, approx)
            if best is None:
                continue
            ev, book, o, prob, approx = best
            cands.append(Candidate(game=game, market=market, side=side, point=o.point,
                                   best_book=book, best_price=o.price, fair_prob=prob,
                                   ev=ev, approx=approx))
            cands[-1].ref_book = ref_name  # type: ignore[attr-defined]
            cands[-1].ref_point = ref_point  # type: ignore[attr-defined]
    return cands
