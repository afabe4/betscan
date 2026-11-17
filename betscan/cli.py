"""Command line entry point: python -m betscan scan ..."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import report
from .config import SPORTS, Settings
from .context import DemoContext, LiveContext, demo_games
from .engine import analyze, dedupe_correlated, enrich
from .http import Fetcher
from .sources.odds_api import fetch_games
from .store import Store


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="betscan", description="Scan betting lines for mispriced games.")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="scan upcoming games")
    s.add_argument("--sports", default="nfl,ncaaf,nba,nhl", help=f"comma list from {','.join(SPORTS)}")
    s.add_argument("--demo", action="store_true", help="run on built-in fabricated data (offline)")
    s.add_argument("--hours", type=int, default=96, help="look-ahead window")
    s.add_argument("--min-ev", type=float, default=1.5, help="min EV %% for a BET")
    s.add_argument("--bankroll", type=float, default=1000.0)
    s.add_argument("--kelly", type=float, default=0.25, help="Kelly multiplier")
    s.add_argument("--all", action="store_true", help="also print WATCH tier")
    s.add_argument("--json", type=Path, help="write JSON results here")
    s.add_argument("--html", type=Path, help="write an HTML report here")
    s.add_argument("--model-weight", type=float, default=0.0,
                   help="how much the efficiency model counts toward scores (default 0 = context only)")
    s.add_argument("--no-efficiency", action="store_true", help="skip efficiency models")
    s.add_argument("--no-store", action="store_true", help="don't record snapshots")
    r = sub.add_parser("ratings", help="print a sport's efficiency ratings")
    r.add_argument("sport", choices=list(SPORTS))
    r.add_argument("--top", type=int, default=40)
    b = sub.add_parser("backtest", help="walk-forward NFL efficiency-model backtest vs closing lines")
    b.add_argument("--seasons", default="2023-2026")
    a = p.parse_args(argv)
    if a.cmd == "ratings":
        return show_ratings(a)
    if a.cmd == "backtest":
        from .backtest import main as bt_main
        return bt_main(a.seasons)

    st = Settings(hours_ahead=a.hours, min_ev=a.min_ev / 100, bankroll=a.bankroll, kelly_mult=a.kelly,
                  model_weight=a.model_weight)
    now = datetime.now(timezone.utc)
    codes = [c.strip() for c in a.sports.split(",") if c.strip()]
    bad = [c for c in codes if c not in SPORTS]
    if bad:
        p.error(f"unknown sport(s): {bad}")

    store = None if (a.no_store or a.demo) else Store(st.db_path)
    fetcher = Fetcher(st.cache_dir)
    if a.demo:
        slate = demo_games(now)
        ctx = DemoContext(now, slate)
        codes = [c for c in codes if c in slate] or list(slate)
    else:
        ctx = LiveContext(fetcher)

    all_cands = []
    for code in codes:
        sport = SPORTS[code]
        print(f"Scanning {code.upper()}...", file=sys.stderr)
        try:
            games = slate[code] if a.demo else fetch_games(sport, st, fetcher)
        except Exception as e:  # noqa: BLE001
            print(f"  odds unavailable for {code}: {e}", file=sys.stderr)
            continue
        if store:
            store.record_lines(games, now)
        enrich(games, sport, ctx, now, log=lambda m: print(m, file=sys.stderr))
        ratings = None if a.no_efficiency else ctx.ratings(sport, now, log=lambda m: print(m, file=sys.stderr))
        all_cands += analyze(games, st, now, store, ratings)

    dedupe_correlated(all_cands)
    order = {"BET": 0, "VERIFY": 1, "ALT": 2, "LEAN": 3, "WATCH": 4}
    all_cands.sort(key=lambda c: (order.get(c.tier, 9), -c.score))
    tiers = ("BET", "VERIFY", "ALT", "LEAN", "WATCH") if a.all else ("BET", "VERIFY", "ALT", "LEAN")
    print(report.terminal(all_cands, show=tiers))
    if store:
        store.record_picks(all_cands, now)
    if a.json:
        a.json.write_text(report.to_json(all_cands))
    if a.html:
        a.html.write_text(report.to_html(all_cands, now, a.demo))
        print(f"\nHTML report: {a.html}", file=sys.stderr)
    return 0


def show_ratings(a) -> int:
    st = Settings()
    rs = LiveContext(Fetcher(st.cache_dir)).ratings(SPORTS[a.sport], datetime.now(timezone.utc),
                                                     log=lambda m: print(m, file=sys.stderr))
    if rs is None:
        return 1
    print(f"{rs.source}  (offense higher = better, defense lower = better; units {rs.unit})")
    rows = sorted(rs.teams.values(), key=lambda t: t.def_rank + t.off_rank)
    for t in rows[: a.top]:
        pace = f"  pace {t.pace:.1f}" if t.pace else ""
        print(f"{t.name:<30} off {t.off:8.3f} (#{t.off_rank:<3}) def {t.defn:8.3f} (#{t.def_rank:<3})"
              f" g{t.games}{pace}")
    return 0
