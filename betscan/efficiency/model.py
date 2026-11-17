"""Efficiency ratings -> projected score -> win / cover / over probabilities.

Every provider produces a RatingSet in one of four unit systems:

  per_play  NFL EPA/play (offense gained, defense allowed); pace = plays/game
  per_poss  points per 100 possessions (NBA ORtg/DRtg, Torvik AdjO/AdjD); pace = poss/game
  points    points per game, opponent-adjusted (SP+, score-fit model)
  per60     goals per 60 (NHL xGF/60, xGA/60)

`project()` turns any of them into an expected home and away score.
"""
from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field

# Home advantage in points (goals/runs for NHL/MLB) and the spread of outcomes.
HCA = {"nfl": 1.5, "ncaaf": 2.5, "nba": 2.0, "ncaab": 3.0, "nhl": 0.15, "mlb": 0.15}
SIGMA_MARGIN = {"nfl": 13.5, "ncaaf": 15.5, "nba": 12.5, "ncaab": 10.5}
SIGMA_TOTAL = {"nfl": 10.5, "ncaaf": 13.5, "nba": 16.0, "ncaab": 13.0}
POISSON_SPORTS = {"nhl", "mlb"}


@dataclass
class TeamRating:
    name: str
    off: float              # offense, in the set's units (higher = better)
    defn: float             # defense allowed, same units (lower = better)
    pace: float | None = None
    games: int = 0
    extra: dict = field(default_factory=dict)   # display-only metrics
    off_rank: int = 0
    def_rank: int = 0


@dataclass
class RatingSet:
    sport: str
    source: str             # human-readable, shown in signal details
    kind: str               # per_play | per_poss | points | per60
    teams: dict[str, TeamRating]
    league_off: float       # league-average offense in set units
    league_pace: float | None = None
    league_pts: float | None = None    # avg points per team-game (per_play only)
    aliases: dict[str, str] = field(default_factory=dict)  # odds name -> provider name
    unit: str = ""          # e.g. "EPA/play"

    def __post_init__(self):
        ranked = sorted(self.teams.values(), key=lambda t: -t.off)
        for i, t in enumerate(ranked, 1):
            t.off_rank = i
        for i, t in enumerate(sorted(self.teams.values(), key=lambda t: t.defn), 1):
            t.def_rank = i

    def find(self, odds_name: str) -> TeamRating | None:
        if odds_name in self.teams:
            return self.teams[odds_name]
        if odds_name in self.aliases and self.aliases[odds_name] in self.teams:
            return self.teams[self.aliases[odds_name]]
        hit = resolve_name(odds_name, list(self.teams))
        return self.teams.get(hit) if hit else None


# --- name matching ---------------------------------------------------------

_ALIASES = {"uconn": "connecticut", "ole miss": "mississippi", "umass": "massachusetts",
            "pitt": "pittsburgh", "unlv": "nevada las vegas", "utep": "texas el paso",
            "ucf": "central florida", "smu": "southern methodist", "byu": "brigham young",
            "lsu": "louisiana state", "usc": "southern california", "tcu": "texas christian",
            "vcu": "virginia commonwealth", "app state": "appalachian state"}


def _norm(s: str) -> str:
    s = s.lower().replace("&", "and").replace("é", "e")
    s = re.sub(r"\bst\.(?=\s|$)", "state", s)      # "Michigan St." -> "michigan state"
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def resolve_name(odds_name: str, provider_names: list[str]) -> str | None:
    """Map 'Michigan State Spartans' -> 'Michigan St.' etc. Prefers the longest
    provider name that is a word-prefix of the odds name, so 'Michigan' never
    steals 'Michigan State'."""
    target = _norm(odds_name)
    for short, full in _ALIASES.items():
        if target == short or target.startswith(short + " "):
            target = full + target[len(short):]
    norm = {_norm(p): p for p in provider_names}
    best = None
    for n, orig in norm.items():
        if target == n or target.startswith(n + " "):
            if best is None or len(n) > len(best[0]):
                best = (n, orig)
    if best:
        return best[1]
    hit = difflib.get_close_matches(target, list(norm), n=1, cutoff=0.85)
    return norm[hit[0]] if hit else None


# --- projection ------------------------------------------------------------

def shrink(value: float, league: float, n: float, k: float) -> float:
    """Regress a small-sample rate toward the league mean (n samples, prior weight k)."""
    return league + (value - league) * n / (n + k)


def project(rs: RatingSet, home: TeamRating, away: TeamRating, neutral: bool = False
            ) -> tuple[float, float]:
    hca = 0.0 if neutral else HCA.get(rs.sport, 0.0)
    lg = rs.league_off
    if rs.kind == "per_play":
        plays = ((home.pace or rs.league_pace) + (away.pace or rs.league_pace)) / 2
        h = rs.league_pts + plays * ((home.off - lg) + (away.defn - lg))
        a = rs.league_pts + plays * ((away.off - lg) + (home.defn - lg))
    elif rs.kind == "per_poss":
        lp = rs.league_pace
        poss = (home.pace or lp) * (away.pace or lp) / lp
        h = (home.off + away.defn - lg) * poss / 100
        a = (away.off + home.defn - lg) * poss / 100
    elif rs.kind == "points":
        h = home.off + away.defn - lg
        a = away.off + home.defn - lg
    elif rs.kind == "per60":
        h = home.off * away.defn / lg
        a = away.off * home.defn / lg
    else:
        raise ValueError(rs.kind)
    return max(0.1, h + hca / 2), max(0.1, a - hca / 2)


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _poisson(lam: float, kmax: int = 25) -> list[float]:
    p = [math.exp(-lam)]
    for k in range(1, kmax + 1):
        p.append(p[-1] * lam / k)
    return p


def probabilities(sport: str, home_pts: float, away_pts: float, market: str, side_is_home: bool | None,
                  point: float | None, side: str | None = None) -> float | None:
    """Model probability that a given market side wins (pushes split out).

    market: h2h | spreads | totals. For spreads, `point` is the side's own handicap.
    For totals, `side` is "Over"/"Under" and `point` the total.
    """
    if sport in POISSON_SPORTS:
        ph, pa = _poisson(home_pts), _poisson(away_pts)
        win = lose = tie = 0.0
        over = under = 0.0
        cover = no_cover = 0.0
        for i, x in enumerate(ph):
            for j, y in enumerate(pa):
                pr = x * y
                d = i - j if side_is_home else j - i
                if d > 0: win += pr
                elif d < 0: lose += pr
                else: tie += pr
                if point is not None:
                    if market == "totals":
                        if i + j > point: over += pr
                        elif i + j < point: under += pr
                    elif market == "spreads":
                        if d + point > 0: cover += pr
                        elif d + point < 0: no_cover += pr
        if market == "h2h":
            ot_edge = 0.52 if side_is_home else 0.48   # OT / extras: near coin flip
            return win + tie * ot_edge
        if market == "totals":
            return (over if side == "Over" else under) / max(1e-9, over + under)
        return cover / max(1e-9, cover + no_cover)

    margin = home_pts - away_pts if side_is_home or side_is_home is None else away_pts - home_pts
    if market == "h2h":
        return _phi(margin / SIGMA_MARGIN[sport])
    if market == "spreads":
        return _phi((margin + point) / SIGMA_MARGIN[sport])
    if market == "totals":
        z = (home_pts + away_pts - point) / SIGMA_TOTAL[sport]
        return _phi(z) if side == "Over" else 1 - _phi(z)
    return None


# --- score-based fallback model ---------------------------------------------

def fit_scores(games: list[tuple[str, str, float, float, bool]], sport: str,
               ridge: float = 3.0, iters: int = 60) -> RatingSet:
    """Opponent-adjusted offense/defense from final scores (ridge-regularized).

    home_pts = mu + hca/2 + o[home] + d[away];  away_pts = mu - hca/2 + o[away] + d[home]
    `ridge` acts like that many games of league-average results per team.
    """
    teams = sorted({g[0] for g in games} | {g[1] for g in games})
    o = dict.fromkeys(teams, 0.0)
    d = dict.fromkeys(teams, 0.0)
    n = dict.fromkeys(teams, 0)
    for h, a, *_ in games:
        n[h] += 1; n[a] += 1
    mu = sum(g[2] + g[3] for g in games) / (2 * len(games))
    hca = HCA.get(sport, 0.0)
    for _ in range(iters):
        ro = dict.fromkeys(teams, 0.0)
        rd = dict.fromkeys(teams, 0.0)
        for h, a, hs, as_, neu in games:
            hh = 0 if neu else hca / 2
            ro[h] += hs - mu - hh - d[a]
            ro[a] += as_ - mu + hh - d[h]
            rd[a] += hs - mu - hh - o[h]
            rd[h] += as_ - mu + hh - o[a]
        o = {t: ro[t] / (n[t] + ridge) for t in teams}
        d = {t: rd[t] / (n[t] + ridge) for t in teams}
    rated = {t: TeamRating(t, mu + o[t], mu + d[t], games=n[t],
                           extra={"adj_margin": round(o[t] - d[t], 1)}) for t in teams}
    return RatingSet(sport, "score-adjusted ratings", "points", rated, league_off=mu, unit="pts/g")
