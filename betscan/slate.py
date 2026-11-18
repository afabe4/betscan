"""What a scan hands to the bet card: one JSON file per sport with plain-language picks.

Each bet carries its limit: the worst number and price at which it is still a bet,
so the card can say "bet down to 140.5" or "no worse than -115".
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

from .models import Candidate
from .ncaab.live import EARLY_MIN_EV
from .odds_math import decimal_to_american

KIND_LABEL = {
    "ncaab_model": "Totals model", "early": "Early totals", "wind": "Wind under", "price": "Price edge",
    "teaser": "Teaser", "injury": "Injury", "news": "News", "model": "Efficiency model",
}


BOOK_NAMES = {"draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "williamhill_us": "Caesars",
              "espnbet": "ESPN BET", "betrivers": "BetRivers", "pinnacle": "Pinnacle", "lowvig": "LowVig",
              "betonlineag": "BetOnline", "circasports": "Circa", "bovada": "Bovada", "fanatics": "Fanatics"}


def _book_name(key: str) -> str:
    return BOOK_NAMES.get(key, key.title())


def play_kind(c: Candidate) -> str:
    kinds = {s.kind for s in c.signals}
    if "ncaab_early" in kinds and c.market == "totals":
        return "early"
    # Only a qualifying model play counts; a context-only model note (teams under 10 games)
    # on a price-edge bet must not make it look like a model bet.
    if c.market == "totals" and any(s.kind == "ncaab_model" and "-> grade " in s.detail for s in c.signals):
        return "ncaab_model"
    if "wind" in kinds and c.side == "Under":
        return "wind"
    return "price"


def _limit_price(fair: float | None, min_ev: float) -> int | None:
    """Worst American price that still clears min_ev at this fair probability."""
    if not fair:
        return None
    dec = (1 + min_ev) / fair
    if dec <= 1.01:
        return None
    # Round toward the better price, so the limit itself still clears min_ev
    # (-109.7 becomes -109, not -110).
    if dec >= 2:
        return math.ceil((dec - 1) * 100)
    return math.ceil(-100 / (dec - 1))


def _limit_point(c: Candidate, model_total: float | None, min_edge: float) -> float | None:
    if model_total is None or c.market != "totals":
        return None
    if c.side == "Over":     # need line <= model - edge; round down to a half point
        return math.floor((model_total - min_edge) * 2) / 2
    return math.ceil((model_total + min_edge) * 2) / 2


def _model_total(c: Candidate) -> float | None:
    """The total behind the pick: Torvik's for early plays, the model's otherwise."""
    want = ("ncaab_early", "Torvik total ") if play_kind(c) == "early" else ("ncaab_model", "model total ")
    for s in c.signals:
        if s.kind == want[0] and s.detail.startswith(want[1]):
            try:
                return float(s.detail.split()[2])
            except ValueError:
                return None
    return None


def reasons(c: Candidate) -> list[str]:
    out = []
    kind = play_kind(c)
    if kind == "early":
        mt = _model_total(c)
        out.append(f"Torvik total {mt:.1f}, {abs(mt - c.point):.1f} pts {'above' if c.side == 'Over' else 'below'} the line.")
        out.append("Early-season play: tested about 53% in November. Small fixed stake, and only at a price "
                   "with at least a 1.3% edge.")
        return out
    if kind == "ncaab_model":
        mt = _model_total(c)
        gap = abs((mt or 0) - (c.point or 0))
        out.append(f"Model total {mt:.1f}, {gap:.1f} pts {'above' if c.side == 'Over' else 'below'} the line.")
        out.append(f"Backtested win rate at this gap: {c.fair_prob:.1%}.")
        if gap < 6:
            out.append("B play: only bet near the opener. If the total has moved half a point your way, pass.")
    elif kind == "wind":
        sig = next(s for s in c.signals if s.kind == "wind")
        out.append(sig.detail.split(". ")[0].replace("forecast", "Forecast") + ".")
        out.append(f"Under's fair chance after the wind adjustment: {c.fair_prob:.1%}.")
    else:
        ref = getattr(c, "ref_book", None)
        ref = "the market consensus" if ref in (None, "consensus") else _book_name(ref)
        out.append(f"{_book_name(c.best_book) if c.best_book else 'Book'} beats {ref}'s no-vig line "
                   f"(fair {c.fair_prob:.1%}).")
    for s in c.signals:
        if s.kind in ("injury", "news") and s.strength:
            out.append(s.detail.split(" [")[0])
        if s.kind == "ncaab_model" and "already moved" in s.detail:
            out.append("The total has already moved this way, so you're not getting the opener.")
    return out


def _grade(c: Candidate) -> str | None:
    if c.tier == "EARLY":
        return "Early"
    for s in c.signals:
        if s.kind == "ncaab_model" and "-> grade " in s.detail:
            return s.detail.split("-> grade ")[1][0]
    return None


def bet_entry(c: Candidate, min_ev: float, min_edge: float = 3.0) -> dict:
    mt = _model_total(c)
    grade = _grade(c)
    early = play_kind(c) == "early"
    return {
        "sport": c.game.sport, "tier": c.tier, "kind": play_kind(c), "game": c.game.label,
        "home": c.game.home, "away": c.game.away, "commence": c.game.commence.isoformat(),
        "market": c.market, "selection": c.selection, "side": c.side, "point": c.point,
        "book": c.best_book, "price": c.best_price, "fair_prob": c.fair_prob, "ev": c.ev,
        "stake": c.stake, "limit_price": _limit_price(c.fair_prob, EARLY_MIN_EV if early else min_ev),
        "limit_point": _limit_point(c, mt, min_edge), "reasons": reasons(c), "grade": grade,
    }


def watch_entry(c: Candidate) -> dict:
    sig = next((s for s in c.signals if s.kind in ("ncaab_model", "line", "injury", "news", "wind", "model")), None)
    note = sig.detail.split(" [")[0] if sig else ""
    if c.tier == "VERIFY":
        note = "Line is far from the market. Check it's live before betting. " + note
    if c.tier == "ALSO":
        note = f"Qualified ({(c.ev or 0) * 100:+.1f}% edge) but outside today's top picks."
    return {"sport": c.game.sport, "tier": c.tier, "game": c.game.label, "commence": c.game.commence.isoformat(),
            "selection": c.selection, "book": c.best_book, "price": c.best_price, "ev": c.ev, "note": note[:220]}


def write(path: Path, sport: str, cands: list[Candidate], teasers, now: datetime, min_ev: float,
          credits_left: str | None):
    bets = [bet_entry(c, min_ev) for c in cands if c.game.sport == sport and c.tier in ("BET", "EARLY")]
    also = sorted((c for c in cands if c.game.sport == sport and c.tier == "ALSO"), key=lambda c: -(c.ev or 0))
    watch = [watch_entry(c) for c in also] + \
        [watch_entry(c) for c in cands if c.game.sport == sport and c.tier in ("VERIFY", "LEAN", "WATCH")]
    tz = [{"book": t.book, "price": t.price, "prob": t.prob, "ev": t.ev, "stake": t.stake,
           "legs": [{"game": leg.game.label, "side": leg.side, "point": leg.point, "teased": leg.teased,
                     "commence": leg.game.commence.isoformat()} for leg in t.legs]}
          for t in teasers] if sport == "nfl" else []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sport": sport, "scanned_at": now.isoformat(), "credits_left": credits_left,
                                "bets": bets, "teasers": tz, "watch": watch[:12]}, indent=1))
