"""Grade logged picks: closing-line value (CLV) and results.

CLV is the best early test of whether a signal works: if your bets beat the
closing line, they win long run, well before the win-loss record means anything
(it takes ~1000+ bets for W-L to separate skill from luck; CLV shows in ~100).

  CLV (sides/totals/ML) = EV of your price measured against the no-vig closing
                          probability at your number. +2% = you beat the close by 2%.
  CLV (teaser legs)     = points: your spread minus the closing spread.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .config import SHARP_BOOKS
from .history import load_games, nfl_abbr_map
from .odds_math import american_to_decimal, no_vig, shift_prob
from .signals.price import _bettor_point
from .store import Store


def _close_reference(snap: dict, market: str):
    for bk in SHARP_BOOKS:
        if market in snap.get(bk, {}) and len(snap[bk][market]) >= 2:
            return bk, snap[bk][market]
    # consensus: the book whose line is the median
    books = [b for b in snap if market in snap[b] and len(snap[b][market]) >= 2]
    if not books:
        return None, None
    books.sort(key=lambda b: (snap[b][market][0][1] or 0))
    b = books[len(books) // 2]
    return b, snap[b][market]


def closing_value(pick: dict, snap: dict) -> dict:
    market = "spreads" if pick["market"] == "teaser" else pick["market"]
    book, outs = _close_reference(snap, market)
    if not outs:
        return {}
    mine = next((o for o in outs if o[0] == pick["side"]), None)
    if mine is None:
        return {}
    _, cpoint, cprice = mine
    out = {"close_point": cpoint, "close_price": cprice}
    if pick["market"] == "teaser":
        if cpoint is not None and pick["point"] is not None:
            out["clv"] = round(pick["point"] - cpoint, 2)
        return out
    probs = no_vig([o[2] for o in outs])
    fair = probs[[o[0] for o in outs].index(pick["side"])]
    if market != "h2h" and cpoint is not None and pick["point"] is not None and cpoint != pick["point"]:
        fair = shift_prob(fair, _bettor_point(market, pick["side"], cpoint),
                          _bettor_point(market, pick["side"], pick["point"]), pick["sport"], market)
    out["close_fair"] = round(fair, 4)
    if pick["price"] is not None:
        out["clv"] = round(fair * american_to_decimal(pick["price"]) - 1, 4)
    return out


def find_result(pick: dict, games: list[dict], abbr: dict) -> tuple[float, float] | None:
    """(home_score, away_score) from nflverse, matched by teams and date."""
    h, a = abbr.get(pick["home"]), abbr.get(pick["away"])
    when = datetime.fromisoformat(pick["commence"])
    for g in games:
        if g["home_team"] == h and g["away_team"] == a and g["home_score"] not in ("", "NA"):
            day = datetime.fromisoformat(g["gameday"]).replace(tzinfo=timezone.utc)
            if abs((day - when).total_seconds()) < 2 * 86400:
                return float(g["home_score"]), float(g["away_score"])
    return None


def grade(pick: dict, hs: float, as_: float) -> tuple[str, float | None]:
    m, side, pt = pick["market"], pick["side"], pick["point"]
    margin = hs - as_ if side == pick["home"] else as_ - hs
    if m == "totals":
        d = (hs + as_ - pt) * (1 if side == "Over" else -1)
    elif m == "spreads":
        d = margin + pt
    elif m == "teaser":
        d = margin + pt + 6
    else:
        d = margin
    res = "W" if d > 0 else "L" if d < 0 else "P"
    if pick["price"] is None:
        return res, None
    return res, {"W": american_to_decimal(pick["price"]) - 1, "L": -1.0, "P": 0.0}[res]


def run(store: Store, fetcher, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    started = store.picks("commence < ?", (now.isoformat(),))
    games = None
    abbr = nfl_abbr_map()
    for p in started:
        upd = {}
        if p["clv"] is None:
            snap = store.closing_snapshot(p["game_id"], p["commence"])
            upd.update(closing_value(p, snap))
        if p["result"] is None and p["sport"] == "nfl" and \
                datetime.fromisoformat(p["commence"]) < now - timedelta(hours=4):
            if games is None:
                games = load_games(fetcher)
            sc = find_result(p, games, abbr)
            if sc:
                upd["result"], upd["profit"] = grade(p, *sc)
        if upd:
            store.update_pick(p["rowid"], **upd)
            p.update(upd)
    return started


def summarize(picks: list[dict]) -> str:
    groups = defaultdict(list)
    for p in picks:
        groups[f"tier {p['tier']}"].append(p)
        for k in (p["kinds"] or "").split(","):
            if k:
                groups[f"signal {k}"].append(p)
    lines = [f"{'group':<18}{'picks':>6}{'W-L-P':>12}{'win%':>7}{'ROI':>8}{'avg CLV':>9}{'beat close':>12}"]
    for name in sorted(groups):
        ps = groups[name]
        w = sum(p["result"] == "W" for p in ps); l = sum(p["result"] == "L" for p in ps)
        pu = sum(p["result"] == "P" for p in ps)
        prof = [p["profit"] for p in ps if p["profit"] is not None]
        clv = [p["clv"] for p in ps if p["clv"] is not None]
        teaser = name.endswith("TEASER") or name.endswith("teaser")
        fmt_clv = (lambda v: f"{v:+.2f}pt") if teaser else (lambda v: f"{v * 100:+.1f}%")
        lines.append(
            f"{name:<18}{len(ps):>6}{f'{w}-{l}-{pu}':>12}"
            f"{(f'{w / (w + l):.0%}' if w + l else '-'):>7}"
            f"{(f'{sum(prof) / len(prof):+.1%}' if prof else '-'):>8}"
            f"{(fmt_clv(sum(clv) / len(clv)) if clv else '-'):>9}"
            f"{(f'{sum(c > 0 for c in clv) / len(clv):.0%}' if clv else '-'):>12}")
    lines.append("\nROI is flat 1 unit per pick. Teaser rows grade individual legs; CLV in points.")
    return "\n".join(lines)
