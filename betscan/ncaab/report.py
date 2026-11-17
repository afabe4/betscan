"""`python -m betscan ncaab-backtest`: reproduce the college backtest tables."""
from __future__ import annotations

from collections import defaultdict

from . import archive
from .backtest import walk_forward
from .live import CAP, CARRY, DECAY, HCA, MIN_EDGE, MIN_GAMES, RIDGE


def _rec(ps, market="total", k=MIN_EDGE, lo_hi=None, min_games=0, slip=0.0, months=None, line="open"):
    w = l = 0
    for p in ps:
        g = p.g
        if months and g.day.month not in months:
            continue
        if min(p.home_games, p.away_games) < min_games:
            continue
        if market == "total":
            ln, m, r = (g.open_total if line == "open" else g.close_total), p.total, g.total
        else:
            ln, m, r = (g.open_spread if line == "open" else g.close_spread), p.margin, g.margin
        if ln is None:
            continue
        e = m - ln
        if (lo_hi and not lo_hi[0] <= abs(e) < lo_hi[1]) or (not lo_hi and abs(e) < k) or e == 0:
            continue
        d = r - (ln + slip * (1 if e > 0 else -1))
        if d == 0:
            continue
        w += (d > 0) == (e > 0)
        l += (d > 0) != (e > 0)
    return w, l


def _f(wl):
    w, l = wl
    return f"{w / (w + l):.1%} ({w + l})" if w + l else "-"


def main(fetcher) -> int:
    print("Loading Sportsbook Review archive (14 seasons)...")
    games = archive.load(fetcher)
    preds = [p for p in walk_forward(games, ridge=RIDGE, carry=CARRY, hca=HCA, cap=CAP, decay=DECAY)
             if p.g.season != archive.SEASONS[0]]   # first season has no prior
    early = [p for p in preds if p.g.season < "2014-15"]
    late = [p for p in preds if p.g.season >= "2014-15"]
    print(f"{len(games)} games; walk-forward score-based ratings, no look-ahead.\n")
    print(f"TOTALS: model vs line, {MIN_EDGE:g}+ pts apart        2008-14            2015-21")
    rows = [("vs opener, all games", {}), (f"vs opener, both teams {MIN_GAMES}+ games", {"min_games": MIN_GAMES}),
            (f"  ...at 0.5 pt worse than opener", {"min_games": MIN_GAMES, "slip": 0.5}),
            (f"  ...at 1.0 pt worse than opener", {"min_games": MIN_GAMES, "slip": 1.0}),
            (f"vs closing line, both teams {MIN_GAMES}+", {"min_games": MIN_GAMES, "line": "close"}),
            ("vs opener, November only", {"months": (11,)})]
    for name, kw in rows:
        print(f"  {name:<42}{_f(_rec(early, **kw)):>16}{_f(_rec(late, **kw)):>19}")
    print("\nBy season (vs opener, both teams 10+ games):")
    by = defaultdict(list)
    for p in preds:
        by[p.g.season].append(p)
    for s in sorted(by):
        print(f"  {s}  {_f(_rec(by[s], min_games=MIN_GAMES)):>16}")
    print("\nCalibration (all seasons, both teams 10+ games, 0.5 pt worse than opener):")
    for lo, hi in ((3, 4), (4, 5), (5, 6), (6, 8), (8, 99)):
        print(f"  {lo}-{'+' if hi == 99 else hi} pts  {_f(_rec(preds, lo_hi=(lo, hi), min_games=MIN_GAMES, slip=0.5)):>16}")
    print(f"\nSPREADS (context only): vs opener, Jan-Mar, 4+ pts   "
          f"{_f(_rec(early, 'spread', 4, months=(1, 2, 3))):>14}{_f(_rec(late, 'spread', 4, months=(1, 2, 3))):>19}")
    print("  (inconsistent season to season; one season carries it. Not used for bets.)")
    print("\nBreak-even at -110: 52.4%")
    return 0
