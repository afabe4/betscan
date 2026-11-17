"""Situational signals: injuries, weather, rest/schedule, and news.

Each returns Signals pointing at the market side the situation favors.
Strengths are rough conviction points, deliberately modest: most of this
information is priced quickly. The movement check in engine.py discounts
signals the market has already reacted to.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from ..config import KEY_POSITIONS, Settings
from ..models import Game, Signal

STATUS_WEIGHT = {"out": 1.0, "doubtful": 0.75, "injured reserve": 0.0, "questionable": 0.3,
                 "suspension": 1.0, "day-to-day": 0.25}
# How many "conviction points" a weight-1.0 absence is worth, per sport.
SPORT_SCALE = {"nfl": 3.0, "ncaaf": 2.5, "nba": 2.0, "ncaab": 1.5, "nhl": 1.5, "mlb": 1.5}


def _other(game: Game, team: str) -> str:
    return game.away if team == game.home else game.home


def injury_signals(game: Game, s: Settings, now: datetime) -> list[Signal]:
    out = []
    positions = KEY_POSITIONS.get(game.sport, {})
    scale = SPORT_SCALE.get(game.sport, 1.5)
    for inj in game.injuries:
        w_status = STATUS_WEIGHT.get(inj.status.lower(), 0.0)
        w_pos = positions.get(inj.position.upper(), 0.0)
        if not w_status or not w_pos:
            continue
        fresh = inj.reported is not None and now - inj.reported <= timedelta(hours=s.news_hours)
        if not fresh:
            continue  # long-standing injuries are priced in
        strength = round(scale * w_status * w_pos, 2)
        if strength < 0.4:
            continue
        opp = _other(game, inj.team)
        hrs = (now - inj.reported).total_seconds() / 3600
        detail = (f"{inj.team}: {inj.player} ({inj.position}) {inj.status}, reported {hrs:.0f}h ago"
                  + (f" - {inj.comment}" if inj.comment else ""))
        out.append(Signal("injury", "spreads", opp, strength, detail))
        out.append(Signal("injury", "h2h", opp, strength, detail))
        if inj.position.upper() in ("QB", "SP", "G") and game.sport in ("nfl", "ncaaf", "mlb", "nhl"):
            under = "Under" if inj.position.upper() == "QB" else "Over"
            out.append(Signal("injury", "totals", under, round(strength * 0.4, 2), detail))
    return out


def weather_signals(game: Game, s: Settings) -> list[Signal]:
    w = game.weather
    if not w or game.indoor:
        return []
    out = []
    if game.sport in ("nfl", "ncaaf"):
        wind = max(w.get("wind_mph") or 0, (w.get("gust_mph") or 0) * 0.7)
        strength, why = 0.0, []
        if wind >= s.wind_mph:
            strength += min(3.0, 1.0 + (wind - s.wind_mph) / 5)
            why.append(f"wind {w.get('wind_mph'):.0f} mph (gusts {w.get('gust_mph') or 0:.0f})")
        if (w.get("precip_in") or 0) >= 0.08:
            strength += 0.5
            why.append(f"precip {w['precip_in']:.2f} in/hr")
        if (w.get("temp_f") or 99) <= 20:
            strength += 0.5
            why.append(f"{w['temp_f']:.0f}°F")
        if strength:
            detail = f"{game.venue or game.city}: " + ", ".join(why)
            out.append(Signal("weather", "totals", "Under", round(strength, 2), detail))
            if wind >= s.wind_mph:  # low-variance, low-scoring games help underdogs
                dog = _underdog(game)
                if dog:
                    out.append(Signal("weather", "spreads", dog, 0.5, detail + " (helps the dog)"))
    elif game.sport == "mlb" and (w.get("temp_f") or 99) <= 45:
        out.append(Signal("weather", "totals", "Under", 0.75, f"cold: {w['temp_f']:.0f}°F"))
    return out


def _underdog(game: Game) -> str | None:
    for mk in game.books.values():
        for o in mk.get("spreads", []):
            if o.point is not None and o.point > 0:
                return o.name
    return None


def rest_signals(game: Game) -> list[Signal]:
    h, a = game.home_rest_days, game.away_rest_days
    if h is None or a is None:
        return []
    out = []
    rested, tired = (game.home, game.away) if h > a else (game.away, game.home)
    hi, lo = max(h, a), min(h, a)
    if game.sport in ("nba", "nhl"):
        if lo < 1.5 and hi >= 1.5:
            detail = f"{tired} on a back-to-back; {rested} has {hi:.0f} days rest"
            out.append(Signal("rest", "spreads", rested, 1.0, detail))
            out.append(Signal("rest", "h2h", rested, 1.0, detail))
    elif game.sport == "ncaaf":
        # NFL deliberately excluded: 2006-25 closing lines show rest edges of 3+ days
        # covering 51.2% then 50.1%, i.e. fully priced.
        diff = hi - lo
        if diff >= 3:
            strength = round(min(1.5, 0.5 + 0.25 * (diff - 3)), 2)
            detail = f"rest edge {rested} {hi:.0f}d vs {tired} {lo:.0f}d"
            out.append(Signal("rest", "spreads", rested, strength, detail))
            out.append(Signal("rest", "h2h", rested, strength, detail))
    return out


NEGATIVE = re.compile(
    r"\b(suspend\w*|ruled out|will not play|won't play|to miss|out for|season-ending|torn|"
    r"concussion|illness|flu|personal reasons|fired|arrest\w*|benched|holdout|"
    r"placed on (ir|injured reserve)|shut down|surgery|locker room)\b", re.I)


def _news_subjects(game: Game, n, text: str) -> list[str]:
    """Which of the two teams a story is about. Prefer ESPN's team tags; else the
    team whose nickname appears first in the headline (stories usually lead with
    the subject, opponents come later)."""
    tagged = [t for t in (game.home, game.away) if t in n.teams]
    if tagged:
        return tagged
    hits = []
    for team in (game.home, game.away):
        pat = re.compile(rf"\b{re.escape(team.split()[-1])}\b")
        if (m := pat.search(n.headline)):
            hits.append((m.start(), team))
        elif (m := pat.search(n.description)):
            hits.append((len(n.headline) + m.start(), team))
    return [min(hits)[1]] if hits else []


def news_signals(game: Game, s: Settings, now: datetime) -> list[Signal]:
    per_team: dict[str, list[str]] = {}
    for n in game.news:
        if n.published and now - n.published > timedelta(hours=s.news_hours):
            continue
        text = f"{n.headline}. {n.description}"
        if not NEGATIVE.search(text):
            continue
        for team in _news_subjects(game, n, text):
            per_team.setdefault(team, []).append(n.headline)
    out = []
    for team, heads in per_team.items():
        strength = round(min(1.5, 0.5 * len(heads)), 2)
        detail = f"negative news on {team}: " + " | ".join(heads[:3])
        out.append(Signal("news", "spreads", _other(game, team), strength, detail))
        out.append(Signal("news", "h2h", _other(game, team), strength, detail))
    return out
