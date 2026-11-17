"""Efficiency-model signal: where the ratings disagree with the market's fair price."""
from __future__ import annotations

from ..efficiency.model import RatingSet, probabilities, project
from ..models import Game, Signal
from .price import reference

MIN_EDGE = 0.03       # model must beat market fair prob by 3 pts of probability
STRENGTH_PER_PCT = 0.4
MAX_STRENGTH = 2.5    # capped: public ratings are largely priced into closing lines
MIN_GAMES = 3


def _team_line(rs: RatingSet, name: str) -> str:
    t = rs.find(name)
    if not t:
        return f"{name}: unrated"
    abbr = name.split()[-1]
    ex = t.extra
    if "off_epa" in ex:
        return (f"{abbr} off {ex['off_epa']:+.3f} EPA (#{t.off_rank}, SR {ex['off_sr']:.0%}), "
                f"def {ex['def_epa']:+.3f} (#{t.def_rank})")
    if "ortg" in ex:
        return f"{abbr} ORtg {ex['ortg']:.1f} (#{t.off_rank}), DRtg {ex['drtg']:.1f} (#{t.def_rank}), pace {ex['pace']:.1f}"
    if "adjoe" in ex:
        return f"{abbr} AdjO {ex['adjoe']:.1f} (#{t.off_rank}), AdjD {ex['adjde']:.1f} (#{t.def_rank}), tempo {ex['tempo']:.1f}"
    if "xgf60" in ex:
        return f"{abbr} xGF/60 {ex['xgf60']:.2f} (#{t.off_rank}), xGA/60 {ex['xga60']:.2f} (#{t.def_rank})"
    return f"{abbr} off {t.off:.1f} (#{t.off_rank}), def {t.defn:.1f} (#{t.def_rank}) {rs.unit}"


def model_projection(game: Game, rs: RatingSet) -> tuple[float, float] | None:
    h, a = rs.find(game.home), rs.find(game.away)
    if not h or not a:
        return None
    if rs.kind != "points" and min(h.games or MIN_GAMES, a.games or MIN_GAMES) < MIN_GAMES:
        return None
    return project(rs, h, a, game.neutral)


def efficiency_signals(game: Game, rs: RatingSet | None, weight: float = 0.0) -> list[Signal]:
    """`weight` scales how much the model counts toward a pick's score. Default 0:
    the walk-forward NFL backtest (python -m betscan backtest) found no edge vs
    closing lines, so by default the model is shown as context only."""
    if rs is None:
        return []
    proj = model_projection(game, rs)
    if proj is None:
        return []
    hp, ap = proj
    head = (f"model ({rs.source}): {game.home.split()[-1]} {hp:.1f} - {game.away.split()[-1]} {ap:.1f}"
            f" | {_team_line(rs, game.home)}; {_team_line(rs, game.away)}")
    out = []
    for market in ("h2h", "spreads", "totals"):
        ref = reference(game, market)
        if not ref:
            continue
        _, fair = ref
        for side, (mkt_p, point) in fair.items():
            if market != "h2h" and point is None:
                continue
            side_is_home = None if market == "totals" else side == game.home
            mp = probabilities(game.sport, hp, ap, market, side_is_home, point, side)
            if mp is None:
                continue
            edge = mp - mkt_p
            if edge < MIN_EDGE:
                continue
            strength = round(min(MAX_STRENGTH, edge * 100 * STRENGTH_PER_PCT) * weight, 2)
            line = f"{point:g}" if point is not None else "ML"
            out.append(Signal("model", market, side, strength,
                              f"{head} -> {side} {line}: model {mp:.0%} vs market {mkt_p:.0%}"
                              + ("" if weight else " (context only)")))
    return out
