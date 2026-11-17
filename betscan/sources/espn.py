"""ESPN public site API: schedule/venues, injuries, news, rest days.

These endpoints are unofficial and undocumented; every parser is defensive so
a shape change degrades a signal instead of crashing the scan.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..config import Sport
from ..http import Fetcher
from ..models import Injury, NewsItem

BASE = "https://site.api.espn.com/apis/site/v2/sports"
COLLEGE_GROUPS = {"ncaaf": "80", "ncaab": "50"}


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        s = s.replace("Z", "+00:00")
        if len(s) == 22 and s[16] == "+":  # "2026-11-17T18:00+00:00"
            s = s[:16] + ":00" + s[16:]
        return datetime.fromisoformat(s)
    except ValueError:
        return None


# --- parsers ---------------------------------------------------------------

def parse_scoreboard(data: dict) -> list[dict]:
    out = []
    for ev in data.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        venue = comp.get("venue") or {}
        addr = venue.get("address") or {}
        teams = {}
        for c in comp.get("competitors", []):
            t = c.get("team") or {}
            try:
                score = float(c.get("score")) if c.get("score") not in (None, "") else None
            except (TypeError, ValueError):
                score = None
            teams[c.get("homeAway")] = {"id": str(t.get("id")), "name": t.get("displayName", ""),
                                        "score": score}
        if "home" not in teams or "away" not in teams:
            continue
        out.append({
            "date": _dt(comp.get("date") or ev.get("date")),
            "home": teams["home"], "away": teams["away"],
            "venue": venue.get("fullName"), "city": addr.get("city"),
            "state": addr.get("state") or addr.get("country"),
            "indoor": venue.get("indoor"),
            "neutral": bool(comp.get("neutralSite")),
            "completed": bool(((comp.get("status") or {}).get("type") or {}).get("completed")),
        })
    return out


def parse_injuries(data: dict) -> list[Injury]:
    out = []
    for team in data.get("injuries", []):
        tname = team.get("displayName", "")
        for inj in team.get("injuries", []):
            ath = inj.get("athlete") or {}
            pos = (ath.get("position") or {}).get("abbreviation", "")
            out.append(Injury(team=tname, player=ath.get("displayName", "?"), position=pos,
                              status=inj.get("status", ""), reported=_dt(inj.get("date")),
                              comment=inj.get("shortComment", "") or ""))
    return out


def parse_news(data: dict) -> list[NewsItem]:
    out = []
    for a in data.get("articles", []):
        teams = [c.get("description", "") for c in a.get("categories", [])
                 if c.get("type") == "team" and c.get("description")]
        out.append(NewsItem(headline=a.get("headline", ""), description=a.get("description", "") or "",
                            published=_dt(a.get("published")), teams=teams))
    return out


def parse_last_game(data: dict, before: datetime) -> datetime | None:
    last = None
    for ev in data.get("events", []):
        d = _dt(ev.get("date"))
        if d is None or d >= before - timedelta(hours=3):
            continue
        comp = (ev.get("competitions") or [{}])[0]
        status = ((comp.get("status") or {}).get("type") or {})
        if status.get("completed") is False:
            continue
        if last is None or d > last:
            last = d
    return last


# --- fetchers --------------------------------------------------------------

class ESPN:
    def __init__(self, fetcher: Fetcher):
        self.f = fetcher

    def scoreboard(self, sport: Sport, start: datetime, end: datetime) -> list[dict]:
        params = {"dates": f"{start:%Y%m%d}-{end:%Y%m%d}", "limit": 500}
        if sport.code in COLLEGE_GROUPS:
            params["groups"] = COLLEGE_GROUPS[sport.code]
        return parse_scoreboard(self.f.get_json(f"{BASE}/{sport.espn_path}/scoreboard", params, ttl=1800))

    def results(self, sport: Sport, start: datetime, end: datetime) -> list[dict]:
        """Completed games in [start, end], fetched in 3-week chunks."""
        out, cur = [], start
        while cur < end:
            nxt = min(end, cur + timedelta(days=21))
            out += [e for e in self.scoreboard(sport, cur, nxt) if e["completed"]]
            cur = nxt + timedelta(days=1)
        return out

    def injuries(self, sport: Sport) -> list[Injury]:
        return parse_injuries(self.f.get_json(f"{BASE}/{sport.espn_path}/injuries", ttl=900))

    def news(self, sport: Sport) -> list[NewsItem]:
        return parse_news(self.f.get_json(f"{BASE}/{sport.espn_path}/news", {"limit": 100}, ttl=900))

    def last_game(self, sport: Sport, team_id: str, before: datetime) -> datetime | None:
        data = self.f.get_json(f"{BASE}/{sport.espn_path}/teams/{team_id}/schedule", ttl=6 * 3600)
        return parse_last_game(data, before)
