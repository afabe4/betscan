"""Command line entry point.

  python -m betscan scan            scan upcoming NFL games (records lines + picks)
  python -m betscan review          grade past picks: closing-line value and results
  python -m betscan angles          historical base rates behind wind and teaser plays
  python -m betscan ratings nfl     efficiency table
  python -m betscan backtest        walk-forward EPA model test
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import report
from .config import MY_BOOKS, SPORTS, US_REGULATED, Settings
from .context import DemoContext, LiveContext, demo_games
from .engine import analyze, dedupe_correlated, enrich
from .history import FALLBACK_RATES, get_rates
from .http import Fetcher
from .signals.angles import best_teasers, breakeven_leg, teaser_legs
from .sources.odds_api import fetch_games
from .store import Store


SPORT_REGIONS = {"nfl": "us,eu", "ncaab": "us"}
SPORT_HOURS = {"nfl": 168, "ncaab": 36}


def _log(m):
    print(m, file=sys.stderr)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["run"]:
        from .scheduled import run
        return run(argv[1:])
    if argv[:1] == ["state"]:
        from .state import main as state_main
        return state_main(argv[1:])
    p = argparse.ArgumentParser(prog="betscan", description="Scan NFL betting lines for mispriced games.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="scan upcoming games")
    s.add_argument("--sports", default="nfl", help=f"comma list from {','.join(SPORTS)}")
    s.add_argument("--demo", action="store_true", help="run on built-in fabricated data (offline)")
    s.add_argument("--hours", type=int, default=None,
                   help="look-ahead window (default: NFL 168h = a week, NCAAB 36h)")
    s.add_argument("--regions", default=None,
                   help="Odds API regions (default: NFL us,eu for Pinnacle; NCAAB us = 2 credits/scan)")
    s.add_argument("--min-ev", type=float, default=1.5, help="min EV %% for a BET")
    s.add_argument("--bankroll", type=float, default=1000.0)
    s.add_argument("--kelly", type=float, default=0.25, help="Kelly multiplier")
    s.add_argument("--teaser-price", type=float, default=-120, help="your book's 2-team 6-pt teaser price")
    s.add_argument("--teaser3-price", type=float, default=160, help="your book's 3-team 6-pt teaser price")
    s.add_argument("--teaser-favs", action="store_true",
                   help="also tease -7.5/-8.5 favorites (lost money 2018-25; off by default)")
    s.add_argument("--markets", default="spreads,totals",
                   help="Odds API markets; each costs 1 credit per region (add h2h for moneylines)")
    s.add_argument("--books", default=",".join(MY_BOOKS),
                   help="comma list of books you bet at ('us' = all US-regulated, 'all' = any). "
                        "Others are benchmark only")
    s.add_argument("--all", action="store_true", help="also print WATCH tier")
    s.add_argument("--json", type=Path, help="write JSON results here")
    s.add_argument("--html", type=Path, help="write an HTML report here")
    s.add_argument("--card", type=Path, help="also write the bet card here")
    s.add_argument("--model-weight", type=float, default=0.0,
                   help="how much the efficiency model counts toward scores (default 0 = context only)")
    s.add_argument("--no-efficiency", action="store_true", help="skip efficiency models")
    s.add_argument("--no-store", action="store_true", help="don't record snapshots")
    sub.add_parser("review", help="grade past picks vs closing line and results")
    sub.add_parser("run", help="scheduled run: scan, grade, rebuild card, print ALERT/QUIET (see scheduled.py)")
    cd = sub.add_parser("card", help="build the bet card from the latest scans and your record")
    cd.add_argument("--out", type=Path, default=Path("card.html"))
    cd.add_argument("--demo", action="store_true", help="sample card with made-up bets and record")
    sub.add_parser("angles", help="print historical rates behind the wind and teaser plays")
    r = sub.add_parser("ratings", help="print a sport's efficiency ratings")
    r.add_argument("sport", choices=list(SPORTS))
    r.add_argument("--top", type=int, default=40)
    b = sub.add_parser("backtest", help="walk-forward NFL efficiency-model backtest vs closing lines")
    b.add_argument("--seasons", default="2023-2026")
    sub.add_parser("ncaab-backtest", help="walk-forward college totals/spreads backtest, 2008-2021")
    a = p.parse_args(argv)

    st = Settings()
    if a.cmd == "ratings":
        return show_ratings(a)
    if a.cmd == "backtest":
        from .backtest import main as bt_main
        return bt_main(a.seasons)
    if a.cmd == "ncaab-backtest":
        from .ncaab.report import main as nb
        return nb(Fetcher(st.cache_dir))
    if a.cmd == "angles":
        return show_angles(st)
    if a.cmd == "review":
        return run_review(st)
    if a.cmd == "card":
        from .card import render
        if not a.demo:
            try:
                from .review import run
                run(Store(st.db_path), Fetcher(st.cache_dir))   # grade anything finished first
            except Exception as e:  # noqa: BLE001
                _log(f"  [review] skipped: {e}")
        a.out.write_text(render(st, datetime.now(timezone.utc), demo=a.demo))
        print(f"Bet card: {a.out}")
        return 0
    return scan(a, p)


def load_rates(fetcher: Fetcher):
    try:
        return get_rates(fetcher)
    except Exception as e:  # noqa: BLE001
        _log(f"  [history] nflverse unavailable ({e}); using built-in 2006-25 rates")
        return FALLBACK_RATES


def scan(a, p) -> int:
    st = Settings(hours_ahead=a.hours or 168, min_ev=a.min_ev / 100, bankroll=a.bankroll, kelly_mult=a.kelly,
                  model_weight=a.model_weight, markets=a.markets,
                  bet_books=(None if a.books == "all" else US_REGULATED if a.books == "us"
                             else tuple(b.strip() for b in a.books.split(","))))
    now = datetime.now(timezone.utc)
    codes = [c.strip() for c in a.sports.split(",") if c.strip()]
    bad = [c for c in codes if c not in SPORTS]
    if bad:
        p.error(f"unknown sport(s): {bad}")

    store = None if (a.no_store or a.demo) else Store(st.db_path)
    fetcher = Fetcher(st.cache_dir)
    if a.demo:
        st.bet_books = None   # demo uses made-up book names
        slate = demo_games(now)
        ctx = DemoContext(now, slate)
        codes = [c for c in codes if c in slate] or list(slate)
    else:
        ctx = LiveContext(fetcher)
    rates = load_rates(fetcher) if "nfl" in codes else None

    all_cands, nfl_games = [], []
    for code in codes:
        sport = SPORTS[code]
        _log(f"Scanning {code.upper()}...")
        try:
            games = slate[code] if a.demo else fetch_games(
                sport, st, fetcher, regions=a.regions or SPORT_REGIONS.get(code),
                hours=a.hours or SPORT_HOURS.get(code))
        except Exception as e:  # noqa: BLE001
            _log(f"  odds unavailable for {code}: {e}")
            continue
        if store:
            store.record_lines(games, now)
        enrich(games, sport, ctx, now, log=_log, store=store)
        ratings = None if a.no_efficiency else ctx.ratings(sport, now, log=_log)
        ncaab = None
        if code == "ncaab" and not a.demo:
            from .ncaab.live import NameIndex, build_ratings
            try:
                r = build_ratings(fetcher, now, log=_log)
                ncaab = (r, NameIndex(r.o), ratings)
            except Exception as e:  # noqa: BLE001
                _log(f"  [ncaab] model unavailable: {e}")
        all_cands += analyze(games, st, now, store, ratings, rates if code == "nfl" else None, ncaab)
        if code == "nfl":
            nfl_games += games

    dedupe_correlated(all_cands)
    order = {"BET": 0, "EARLY": 1, "VERIFY": 2, "ALT": 3, "LEAN": 4, "WATCH": 5}
    all_cands.sort(key=lambda c: (order.get(c.tier, 9), -c.score))
    tiers = ("BET", "EARLY", "VERIFY", "ALT", "LEAN", "WATCH") if a.all else ("BET", "EARLY", "VERIFY", "ALT", "LEAN")
    print(report.terminal(all_cands, show=tiers))

    legs = (teaser_legs(nfl_games, rates, include_favs=a.teaser_favs, bet_books=st.bet_books)
            if rates and nfl_games else [])
    teasers = best_teasers(legs, a.teaser_price, a.teaser3_price, st.bankroll, st.kelly_mult,
                           st.max_stake_pct)
    if nfl_games:
        print("\n" + report.teasers_text(legs, teasers, a.teaser_price, a.teaser3_price))

    if store:
        store.record_picks(all_cands, now, legs)
    if not a.demo:
        from . import slate
        for code in codes:
            slate.write(st.cache_dir / f"latest_{code}.json", code, all_cands, teasers, now, st.min_ev,
                        fetcher.last_headers.get("x-requests-remaining"))
    if a.card:
        from .card import render
        a.card.write_text(render(st, now, demo=a.demo))
        _log(f"\nBet card: {a.card}")
    if a.json:
        a.json.write_text(report.to_json(all_cands))
    if a.html:
        a.html.write_text(report.to_html(all_cands, now, a.demo, legs, teasers))
        _log(f"\nHTML report: {a.html}")
    return 0


def show_angles(st: Settings) -> int:
    rates = load_rates(Fetcher(st.cache_dir))
    y0, y1 = rates.seasons
    print(f"NFL base rates vs closing lines, {y0}-{y1}\n")
    print(f"6-pt teaser legs, dogs +1.5..+2.5, total <= 48.5:  {rates.teaser_dog}")
    print(f"6-pt teaser legs, same dogs, total 49+:            {rates.teaser_dog_high_total}  (excluded)")
    print(f"6-pt teaser legs, favorites -7.5..-8.5:            {rates.teaser_fav}  (off by default)")
    print(f"  break-even per leg: 2-team -120 {breakeven_leg(-120, 2):.1%}, -130 {breakeven_leg(-130, 2):.1%}, "
          f"3-team +160 {breakeven_leg(160, 3):.1%}")
    for (lo, hi), r in rates.wind.items():
        rng = f"{lo:.0f}+" if hi >= 99 else f"{lo:.0f}-{hi - 1:.0f}"
        print(f"Unders, outdoor wind {rng:>5} mph:                     {r}")
    print("  break-even at -110: 52.4%")
    return 0


def run_review(st: Settings) -> int:
    from .review import run, summarize
    store = Store(st.db_path)
    picks = run(store, Fetcher(st.cache_dir))
    if not picks:
        print("No started games with logged picks yet. Run `scan` on a schedule (see README).")
        return 0
    print(summarize(picks))
    return 0


def show_ratings(a) -> int:
    st = Settings()
    rs = LiveContext(Fetcher(st.cache_dir)).ratings(SPORTS[a.sport], datetime.now(timezone.utc), log=_log)
    if rs is None:
        return 1
    print(f"{rs.source}  (offense higher = better, defense lower = better; units {rs.unit})")
    rows = sorted(rs.teams.values(), key=lambda t: t.def_rank + t.off_rank)
    for t in rows[: a.top]:
        pace = f"  pace {t.pace:.1f}" if t.pace else ""
        print(f"{t.name:<30} off {t.off:8.3f} (#{t.off_rank:<3}) def {t.defn:8.3f} (#{t.def_rank:<3})"
              f" g{t.games}{pace}")
    return 0
