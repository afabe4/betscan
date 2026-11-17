"""Opponent-adjusted college basketball ratings from scores, with a preseason prior.

  home_pts = mu + hca/2 + o[home] + d[away]
  away_pts = mu - hca/2 + o[away] + d[home]      (no hca at neutral sites)

o = points scored above average, d = points allowed above average (lower is better).
Ridge regularization pulls each team toward its prior (last season's rating,
regressed), weighted like `ridge` games, so early-season ratings lean on the prior
and fade into this season's results.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ratings:
    mu: float
    o: dict[str, float] = field(default_factory=dict)
    d: dict[str, float] = field(default_factory=dict)
    games: dict[str, int] = field(default_factory=dict)
    hca: float = 3.0

    def project(self, home: str, away: str, neutral: bool) -> tuple[float, float] | None:
        if home not in self.o or away not in self.o:
            return None
        h = 0.0 if neutral else self.hca / 2
        return (self.mu + h + self.o[home] + self.d[away],
                self.mu - h + self.o[away] + self.d[home])

    def margin(self, home, away, neutral) -> float | None:
        p = self.project(home, away, neutral)
        return None if p is None else p[0] - p[1]

    def carry(self, keep: float) -> "Ratings":
        """Next season's prior: shrink every team toward average."""
        return Ratings(self.mu, {t: v * keep for t, v in self.o.items()},
                       {t: v * keep for t, v in self.d.items()}, {}, self.hca)


def fit(games, prior: Ratings | None = None, ridge: float = 4.0, hca: float = 3.0,
        iters: int = 25, warm: Ratings | None = None, cap: float | None = None,
        decay: float | None = None, today=None) -> Ratings:
    """games: iterable of (home, away, home_score, away_score, neutral[, date]).

    cap:   blowout margins beyond this are pulled in (total kept), so running up the
           score on a weak opponent doesn't inflate a rating.
    decay: half-life in days for game weights (recent games count more).
    """
    rows = []
    for g in games:
        h, a, hs, as_, neu = g[:5]
        if cap is not None and abs(hs - as_) > cap:
            adj = (abs(hs - as_) - cap) / 2 * (1 if hs > as_ else -1)
            hs, as_ = hs - adj, as_ + adj
        w = 1.0
        if decay and today is not None and len(g) > 5:
            w = 0.5 ** ((today - g[5]).days / decay)
        rows.append((h, a, hs, as_, neu, w))
    games = rows
    teams = {g[0] for g in games} | {g[1] for g in games}
    if prior:
        teams |= set(prior.o)
    po = prior.o if prior else {}
    pd = prior.d if prior else {}
    mu = (sum(g[2] + g[3] for g in games) / (2 * len(games))) if games else (prior.mu if prior else 70.0)
    # weights: n[t] is the weighted game count
    o = {t: (warm.o.get(t, po.get(t, 0.0)) if warm else po.get(t, 0.0)) for t in teams}
    d = {t: (warm.d.get(t, pd.get(t, 0.0)) if warm else pd.get(t, 0.0)) for t in teams}
    n = dict.fromkeys(teams, 0.0)
    for h, a, *_, w in games:
        n[h] += w
        n[a] += w
    for _ in range(iters):
        ro = {t: ridge * po.get(t, 0.0) for t in teams}
        rd = {t: ridge * pd.get(t, 0.0) for t in teams}
        for h, a, hs, as_, neu, w in games:
            hh = 0.0 if neu else hca / 2
            ro[h] += w * (hs - mu - hh - d[a])
            ro[a] += w * (as_ - mu + hh - d[h])
            rd[a] += w * (hs - mu - hh - o[h])
            rd[h] += w * (as_ - mu + hh - o[a])
        o = {t: ro[t] / (n[t] + ridge) for t in teams}
        d = {t: rd[t] / (n[t] + ridge) for t in teams}
    return Ratings(mu, o, d, {t: round(v) for t, v in n.items()}, hca)
