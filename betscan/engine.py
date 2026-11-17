"""Enrich games with context, run every signal, score and tier candidates."""
from __future__ import annotations

import difflib
from datetime import datetime, timedelta

from .config import Settings, Sport
from .models import Candidate, Game, Signal
from .odds_math import kelly_fraction
from .signals.price import _bettor_point, price_candidates
from .signals.situational import injury_signals, news_signals, rest_signals, weather_signals


def _match(name: str, options: list[str]) -> str | None:
    if name in options:
        return name
    hit = difflib.get_close_matches(name, options, n=1, cutoff=0.75)
    return hit[0] if hit else None


def enrich(games: list[Game], sport: Sport, ctx, now: datetime, log=print) -> None:
    """ctx provides scoreboard/injuries/news/last_game/weather (live or demo)."""
    if not games:
        return
    start = min(g.commence for g in games) - timedelta(days=1)
    end = max(g.commence for g in games) + timedelta(days=1)
    try:
        board = ctx.scoreboard(sport, start, end)
    except Exception as e:  # noqa: BLE001 - keep scanning on partial data
        log(f"  [espn] scoreboard unavailable: {e}"); board = []
    for g in games:
        for ev in board:
            if (_match(g.home, [ev["home"]["name"]]) and _match(g.away, [ev["away"]["name"]])
                    and ev["date"] and abs((ev["date"] - g.commence).total_seconds()) < 12 * 3600):
                g.venue, g.city, g.state, g.indoor = ev["venue"], ev["city"], ev["state"], ev["indoor"]
                g.espn_ids = {g.home: ev["home"]["id"], g.away: ev["away"]["id"]}
                break

    for what, fn in (("injuries", ctx.injuries), ("news", ctx.news)):
        try:
            items = fn(sport)
        except Exception as e:  # noqa: BLE001
            log(f"  [espn] {what} unavailable: {e}"); items = []
        for g in games:
            if what == "injuries":
                g.injuries = [i for i in items if _match(i.team, [g.home, g.away])]
                for i in g.injuries:
                    i.team = _match(i.team, [g.home, g.away])
            else:
                g.news = items

    for g in games:
        for team, attr in ((g.home, "home_rest_days"), (g.away, "away_rest_days")):
            tid = g.espn_ids.get(team)
            if not tid:
                continue
            try:
                last = ctx.last_game(sport, tid, g.commence)
            except Exception:  # noqa: BLE001
                last = None
            if last:
                setattr(g, attr, round((g.commence - last).total_seconds() / 86400, 1))
        if sport.outdoor_weather and g.indoor is False and g.city:
            try:
                g.weather = ctx.weather(g.city, g.state, g.commence)
            except Exception as e:  # noqa: BLE001
                log(f"  [weather] {g.label}: {e}")


def _moved_toward(store, game: Game, market: str, side: str) -> float | None:
    """How far the consensus has moved toward `side` since we first saw the game.
    Points for spreads/totals, probability-ish cents for moneylines."""
    if store is None:
        return None
    hist = store.history(game.id, market, side)
    if len(hist) < 2:
        return None
    (_, p0, pr0), (_, p1, pr1) = hist[0], hist[-1]
    if market in ("spreads", "totals") and p0 is not None and p1 is not None:
        return _bettor_point(market, side, p0) - _bettor_point(market, side, p1)
    return (pr0 - pr1) / 10 if pr0 and pr1 else None  # ~1 per 10 cents worse for the bettor


def _attach(cands: dict, sig: Signal):
    c = cands.get((sig.market, sig.side))
    if c is not None:
        c.signals.append(sig)
    # A signal for one side is evidence against the other side(s).
    for (m, side), other in cands.items():
        if m == sig.market and side != sig.side:
            other.against.append(sig)


def analyze(games: list[Game], settings: Settings, now: datetime, store=None) -> list[Candidate]:
    results = []
    for g in games:
        cands = {(c.market, c.side): c for c in price_candidates(g)}
        sigs = (injury_signals(g, settings, now) + weather_signals(g, settings)
                + rest_signals(g) + news_signals(g, settings, now))
        first_seen = store.first_seen(g.id) if store else None
        for sig in sigs:
            moved = _moved_toward(store, g, sig.market, sig.side)
            if moved is not None and moved >= max(1.0, sig.strength * 0.5):
                sig.strength = round(sig.strength * 0.3, 2)
                sig.detail += f" [market already moved {moved:.1f} toward it - mostly priced]"
            elif moved is not None and first_seen:
                sig.detail += f" [line has moved {moved:+.1f} since first seen]"
            else:
                sig.detail += " [no line history yet - check whether it is priced]"
            _attach(cands, sig)
        for c in cands.values():
            score_candidate(c, settings)
            results.append(c)
    results.sort(key=lambda c: ({"BET": 0, "VERIFY": 1, "LEAN": 2, "WATCH": 3}.get(c.tier, 9), -c.score))
    return results


def score_candidate(c: Candidate, s: Settings) -> None:
    situational = sum(x.strength for x in c.signals) - sum(x.strength for x in c.against)
    ev_pts = max(-3.0, min(6.0, (c.ev or 0) * 100))
    c.score = round(ev_pts + situational, 2)
    if c.ev is not None and c.ev > 0.12:
        c.tier = "VERIFY"  # almost always a stale/bad line or a mismatched market
    elif c.ev is not None and c.ev >= s.min_ev and situational > -1.0:
        c.tier = "BET"
    elif situational >= s.min_lean_score and (c.ev or 0) >= -0.015:
        c.tier = "LEAN"
    elif situational >= 1.0:
        c.tier = "WATCH"
    else:
        c.tier = "PASS"
    if c.tier == "BET" and c.fair_prob and c.best_price:
        f = kelly_fraction(c.fair_prob, c.best_price) * s.kelly_mult
        c.stake = round(min(f, s.max_stake_pct) * s.bankroll, 2)


def dedupe_correlated(cands: list[Candidate]) -> None:
    """Spread and moneyline on the same team are one bet, not two. Keep the
    higher-scoring BET and mark the other ALT (no stake)."""
    seen = set()
    for c in sorted(cands, key=lambda c: -c.score):
        if c.tier != "BET" or c.market not in ("spreads", "h2h"):
            continue
        key = (c.game.id, c.side)
        if key in seen:
            c.tier, c.stake = "ALT", 0.0
        seen.add(key)
