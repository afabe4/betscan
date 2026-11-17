"""The Odds API (https://the-odds-api.com) v4 client."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import Settings, Sport
from ..http import Fetcher
from ..models import Game, Outcome

BASE = "https://api.the-odds-api.com/v4"


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_odds(payload: list[dict], sport: Sport) -> list[Game]:
    games = []
    for ev in payload:
        g = Game(id=ev["id"], sport=sport.code, commence=parse_time(ev["commence_time"]),
                 home=ev["home_team"], away=ev["away_team"])
        for bk in ev.get("bookmakers", []):
            markets = {}
            for m in bk.get("markets", []):
                markets[m["key"]] = [Outcome(o["name"], float(o["price"]), o.get("point"))
                                     for o in m.get("outcomes", [])]
            if markets:
                g.books[bk["key"]] = markets
        games.append(g)
    return games


def fetch_games(sport: Sport, settings: Settings, fetcher: Fetcher, regions: str | None = None,
                hours: int | None = None) -> list[Game]:
    # The key goes in the x-api-key header, never the URL. In the cloud the network proxy
    # injects the saved key and overwrites this header; locally, set ODDS_API_KEY.
    key = settings.odds_api_key or "proxy-injected"
    now = datetime.now(timezone.utc)
    params = {
        "regions": regions or settings.regions,
        "markets": settings.markets,
        "oddsFormat": "american",
        "commenceTimeFrom": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commenceTimeTo": (now + timedelta(hours=hours or settings.hours_ahead)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    payload = fetcher.get_json(f"{BASE}/sports/{sport.odds_key}/odds", params, ttl=300,
                               headers={"x-api-key": key})
    remaining = fetcher.last_headers.get("x-requests-remaining")
    if remaining is not None:
        print(f"  [odds-api] {sport.code}: {len(payload)} games, {remaining} credits left")
    return parse_odds(payload, sport)
