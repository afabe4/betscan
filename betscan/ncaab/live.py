"""Live college basketball totals model: the exact method the archive backtest tested.

Backtest (Sportsbook Review archive, 2008-09 .. 2020-21, walk-forward, no look-ahead):
model total vs the OPENING total, both teams with >= 10 games played, 3+ pts apart:
  2008-14: 55.0% (2,314)   2015-21: 56.1% (4,454)   2015-21 at 0.5 pt worse: 55.0%
Against CLOSING totals the edge is ~51%: it only exists early, before the market moves.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from ..models import Game, Signal
from ..odds_math import expected_value
from .ratings import Ratings, fit

# Same settings the backtest validated. Don't tune these on live results.
RIDGE, CARRY, HCA, CAP, DECAY = 6.0, 0.5, 3.0, 20.0, 60.0
MIN_GAMES = 10
MIN_EDGE = 3.0
# Win rate by |model - line|, from the backtest at 0.5 pt worse than the opener
# (deliberately the pessimistic column: you rarely get the true opener).
CALIBRATION = ((3.0, 0.534), (4.0, 0.537), (5.0, 0.537), (6.0, 0.57))   # 6+ shrunk from 58.9%

ESPN_CBB = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"


def season_start(d: date) -> date:
    return date(d.year if d.month >= 8 else d.year - 1, 11, 1)


def day_results(fetcher, day: date, today: date) -> list[tuple]:
    """Completed D1 games on one day: (home, away, hs, as, neutral, day)."""
    ttl = 30 * 86400 if day < today - timedelta(days=2) else 1800
    data = fetcher.get_json(ESPN_CBB, {"dates": f"{day:%Y%m%d}", "groups": "50", "limit": 500}, ttl=ttl)
    out = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        if not ((comp.get("status") or {}).get("type") or {}).get("completed"):
            continue
        t = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        try:
            out.append((t["home"]["team"]["displayName"], t["away"]["team"]["displayName"],
                        float(t["home"]["score"]), float(t["away"]["score"]),
                        bool(comp.get("neutralSite")), day))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def season_results(fetcher, start: date, end: date, today: date, log=print) -> list[tuple]:
    out, d = [], start
    while d <= end:
        try:
            out += day_results(fetcher, d, today)
        except Exception as e:  # noqa: BLE001
            log(f"  [ncaab] results for {d} unavailable: {e}")
        d += timedelta(days=1)
    return out


def build_ratings(fetcher, now: datetime, log=print) -> Ratings:
    today = now.date()
    this_start = season_start(today)
    last_start = date(this_start.year - 1, 11, 1)
    last = season_results(fetcher, last_start, date(this_start.year, 4, 10), today, log)
    prior = fit(last, ridge=RIDGE, hca=HCA, iters=40, cap=CAP).carry(CARRY) if last else None
    cur = season_results(fetcher, this_start, today - timedelta(days=1), today, log)
    r = fit(cur, prior=prior, ridge=RIDGE, hca=HCA, iters=40, cap=CAP, decay=DECAY, today=today)
    log(f"  [ncaab] ratings: {len(last)} games last season (prior), {len(cur)} this season, "
        f"{sum(1 for v in r.games.values() if v >= MIN_GAMES)} teams with {MIN_GAMES}+ games")
    return r


# --- names: odds feed vs ESPN ---------------------------------------------------

def _norm(s: str) -> str:
    s = s.lower().replace("&", "and").replace("é", "e").replace("'", "")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    toks = s.split()
    # "Arizona St Sun Devils" -> "arizona state sun devils"; keep leading "St" (St. John's).
    toks = [("state" if t == "st" and i > 0 else "saint" if t == "st" else t) for i, t in enumerate(toks)]
    return " ".join(toks)


class NameIndex:
    def __init__(self, names):
        self.by_norm = {_norm(n): n for n in names}

    def find(self, name: str) -> str | None:
        n = _norm(name)
        if n in self.by_norm:
            return self.by_norm[n]
        import difflib
        hit = difflib.get_close_matches(n, list(self.by_norm), n=1, cutoff=0.88)
        return self.by_norm[hit[0]] if hit else None


# --- signal ------------------------------------------------------------------------

def calibrated(edge: float) -> float | None:
    p = None
    for lo, rate in CALIBRATION:
        if abs(edge) >= lo:
            p = rate
    return p


def totals_signal(game: Game, cands: dict, r: Ratings, idx: NameIndex, moved: float | None) -> Signal | None:
    """Re-price the totals candidates with the backtested model. `moved` = how far the
    consensus total has already moved toward the model's side since first seen."""
    if game.sport != "ncaab" or r is None:
        return None
    h, a = idx.find(game.home), idx.find(game.away)
    if not h or not a:
        return None
    proj = r.project(h, a, game.neutral)
    if proj is None:
        return None
    model_total = proj[0] + proj[1]
    gh, ga = r.games.get(h, 0), r.games.get(a, 0)
    head = (f"model total {model_total:.1f} ({h} {proj[0]:.1f}, {a} {proj[1]:.1f}; "
            f"games played {gh}/{ga})")
    if min(gh, ga) < MIN_GAMES:
        return Signal("ncaab_model", "totals", "Over" if model_total > 0 else "Under", 0.0,
                      head + f" - context only until both teams have {MIN_GAMES}+ games "
                             f"(early-season totals lost money in the backtest)")
    best = None
    for side in ("Over", "Under"):
        c = cands.get(("totals", side))
        if not c or c.point is None or c.best_price is None:
            continue
        edge = (model_total - c.point) * (1 if side == "Over" else -1)
        if edge < MIN_EDGE:
            continue
        p = calibrated(edge)
        c.fair_prob = p
        c.ev = expected_value(p, c.best_price)
        c.approx = False
        best = (side, edge, p, c)
    if not best:
        return None
    side, edge, p, c = best
    note = ""
    if moved is not None and moved >= 1.0:
        note = f"; the total has already moved {moved:.1f} toward this side since first seen, so this is not the opener"
    return Signal("ncaab_model", "totals", side, 0.0,
                  f"{head}: {side} {c.point:g} is {edge:.1f} pts off the model -> backtested win rate "
                  f"{p:.1%} (both teams {MIN_GAMES}+ games, near the opener){note}")
