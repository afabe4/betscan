"""Walk-forward college basketball backtest against opening AND closing lines.

For every game, ratings use only games completed before that day (refit every
`refit_days`), seeded with last season's ratings shrunk by `carry`. Results are
split into a tuning period and a hold-out period so settings chosen on the first
can be checked honestly on the second.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from .archive import ArchiveGame
from .ratings import Ratings, fit


@dataclass
class Pred:
    g: ArchiveGame
    margin: float          # model home margin
    total: float           # model total
    home_games: int        # games played this season before this one
    away_games: int


def walk_forward(games: list[ArchiveGame], ridge=4.0, carry=0.6, hca=3.0, refit_days=3,
                 cap: float | None = None, decay: float | None = None) -> list[Pred]:
    by_season = defaultdict(list)
    for g in games:
        by_season[g.season].append(g)
    preds, prior = [], None
    for season in sorted(by_season):
        gs = sorted(by_season[season], key=lambda g: g.day)
        played: list = []
        r = None
        next_fit = None
        for g in gs:
            if next_fit is None or g.day >= next_fit:
                r = fit(played, prior=prior, ridge=ridge, hca=hca, iters=20 if r else 40, warm=r,
                        cap=cap, decay=decay, today=g.day) if (played or prior) else None
                next_fit = g.day + timedelta(days=refit_days)
            if r:
                p = r.project(g.home, g.away, g.neutral)
                if p:
                    preds.append(Pred(g, p[0] - p[1], p[0] + p[1], r.games.get(g.home, 0),
                                      r.games.get(g.away, 0)))
            played.append((g.home, g.away, g.home_score, g.away_score, g.neutral, g.day))
        final = fit(played, prior=prior, ridge=ridge, hca=hca, iters=40, warm=r, cap=cap)
        prior = final.carry(carry)
    return preds


# --- scoring ---------------------------------------------------------------------

def ats(preds, line="close", k=0.0, market="spread", filt=None):
    """Record betting the model's side whenever it differs from the line by >= k."""
    w = l = 0
    for p in preds:
        if filt and not filt(p):
            continue
        g = p.g
        if market == "spread":
            ln = g.close_spread if line == "close" else g.open_spread
            if ln is None:
                continue
            edge, res = p.margin - ln, g.margin - ln
        else:
            ln = g.close_total if line == "close" else g.open_total
            if ln is None:
                continue
            edge, res = p.total - ln, g.total - ln
        if abs(edge) < k or edge == 0 or res == 0:
            continue
        if (res > 0) == (edge > 0):
            w += 1
        else:
            l += 1
    return w, l


def moves_toward(preds, k=2.0, market="spread", filt=None):
    """When the model disagrees with the OPEN by >= k, how often does the CLOSE move
    toward the model? Above 50% means the model anticipates sharp money."""
    toward = away = 0
    for p in preds:
        if filt and not filt(p):
            continue
        g = p.g
        o, c, m = ((g.open_spread, g.close_spread, p.margin) if market == "spread"
                   else (g.open_total, g.close_total, p.total))
        if o is None or c is None or abs(m - o) < k or c == o:
            continue
        if (c - o > 0) == (m - o > 0):
            toward += 1
        else:
            away += 1
    return toward, away


def mae(preds, which="model", filt=None):
    """Mean absolute error vs the final margin, over games that have both lines."""
    xs = [p for p in preds if p.g.close_spread is not None and p.g.open_spread is not None
          and (not filt or filt(p))]
    if which == "model":
        return sum(abs(p.g.margin - p.margin) for p in xs) / len(xs)
    return sum(abs(p.g.margin - getattr(p.g, f"{which}_spread")) for p in xs) / len(xs)


def pct(wl):
    w, l = wl
    return f"{w}-{l} {w / (w + l):.1%}" if w + l else "-"
