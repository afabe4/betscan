"""Context providers: live (ESPN + Open-Meteo) and an offline demo."""
from __future__ import annotations

from datetime import datetime, timedelta

from .http import Fetcher
from .models import Game, Injury, NewsItem, Outcome
from .sources.espn import ESPN
from .sources.weather import Weather
from .efficiency.model import RatingSet, TeamRating
from .efficiency.providers import get_ratings


class LiveContext:
    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher
        self.espn = ESPN(fetcher)
        self.wx = Weather(fetcher)

    def ratings(self, sport, now, log=print):
        return get_ratings(sport, self.fetcher, self.espn, now, log)

    def scoreboard(self, sport, start, end):
        return self.espn.scoreboard(sport, start, end)

    def injuries(self, sport):
        return self.espn.injuries(sport)

    def news(self, sport):
        return self.espn.news(sport)

    def last_game(self, sport, team_id, before):
        return self.espn.last_game(sport, team_id, before)

    def weather(self, city, state, when):
        return self.wx.at_kickoff(city, state, when)


# --- demo ------------------------------------------------------------------

def _book(spread=None, total=None, ml=None, home=None, away=None):
    m = {}
    if spread:
        (hp, hpr), (ap, apr) = spread
        m["spreads"] = [Outcome(home, hpr, hp), Outcome(away, apr, ap)]
    if total:
        pt, opr, upr = total
        m["totals"] = [Outcome("Over", opr, pt), Outcome("Under", upr, pt)]
    if ml:
        m["h2h"] = [Outcome(home, ml[0]), Outcome(away, ml[1])]
    return m


def demo_games(now: datetime) -> dict[str, list[Game]]:
    """Fabricated slate that exercises every signal."""
    sun = now + timedelta(days=2)
    tonight = now + timedelta(hours=6)

    # 1) Wind game: market total hasn't fallen enough; one book still at 42.5.
    g1 = Game("demo-nfl-1", "nfl", sun, "Cleveland Browns", "Buffalo Bills")
    H, A = g1.home, g1.away
    g1.books = {
        "pinnacle": _book((( 6.5, -104), (-6.5, -106)), (41.0, -103, -107), (230, -270), H, A),
        "draftkings": _book((( 6.5, -110), (-6.5, -110)), (41.5, -110, -110), (220, -270), H, A),
        "fanduel": _book((( 6.5, -112), (-6.5, -108)), (42.5, -110, -110), (225, -275), H, A),
        "betmgm": _book((( 7.0, -115), (-7.0, -105)), (41.5, -105, -115), (215, -265), H, A),
    }

    # 2) Breaking QB injury: sharp book moved, a slow book is still on the old number.
    g2 = Game("demo-nfl-2", "nfl", sun + timedelta(hours=3), "Las Vegas Raiders", "Denver Broncos")
    H, A = g2.home, g2.away
    g2.books = {
        "pinnacle": _book(((5.5, -108), (-5.5, -102)), (40.5, -105, -105), (195, -225), H, A),
        "draftkings": _book(((5.5, -110), (-5.5, -110)), (40.5, -110, -110), (190, -230), H, A),
        "caesars": _book(((3.5, -110), (-3.5, -110)), (42.5, -110, -110), (150, -175), H, A),
        "betmgm": _book(((5.0, -110), (-5.0, -110)), (41.0, -110, -110), (185, -220), H, A),
    }

    # 3) NBA schedule spot: home team on a back-to-back, plus a doubtful guard.
    g3 = Game("demo-nba-1", "nba", tonight, "New York Knicks", "Boston Celtics")
    H, A = g3.home, g3.away
    g3.books = {
        "pinnacle": _book(((3.0, -105), (-3.0, -105)), (221.5, -104, -106), (128, -140), H, A),
        "draftkings": _book(((3.0, -110), (-3.0, -110)), (221.5, -110, -110), (125, -148), H, A),
        "fanduel": _book(((3.0, -108), (-3.0, -112)), (222.0, -110, -110), (126, -150), H, A),
    }

    # 4) Pure price discrepancy, no story at all.
    g4 = Game("demo-nba-2", "nba", tonight + timedelta(hours=1), "Denver Nuggets", "Phoenix Suns")
    H, A = g4.home, g4.away
    g4.books = {
        "pinnacle": _book(((-4.5, -103), (4.5, -107)), (229.5, -105, -105), (-185, 165), H, A),
        "draftkings": _book(((-4.5, -110), (4.5, -110)), (229.5, -110, -110), (-190, 160), H, A),
        "betrivers": _book(((-4.5, -112), (4.5, -108)), (229.5, -110, -110), (-170, 182), H, A),
        "fanduel": _book(((-5.0, -105), (5.0, -115)), (230.0, -112, -108), (-188, 158), H, A),
    }
    return {"nfl": [g1, g2], "nba": [g3, g4]}


class DemoContext:
    def __init__(self, now: datetime, games: dict[str, list[Game]]):
        self.now = now
        self.games = games

    def ratings(self, sport, now, log=print):
        """Fabricated efficiency ratings for the demo teams."""
        if sport.code == "nfl":
            r = {"Buffalo Bills": (0.11, -0.04), "Cleveland Browns": (-0.06, -0.02),
                 "Denver Broncos": (0.02, -0.09), "Las Vegas Raiders": (-0.08, 0.05)}
            teams = {n: TeamRating(n, o, d, 62, 10, extra={"off_epa": o, "def_epa": d, "off_sr": 0.45})
                     for n, (o, d) in r.items()}
            return RatingSet("nfl", "DEMO EPA/play", "per_play", teams, 0.0, 62, 22.0, unit="EPA/play")
        if sport.code == "nba":
            r = {"Boston Celtics": (119.5, 110.0, 97), "New York Knicks": (117.0, 113.5, 98),
                 "Phoenix Suns": (116.0, 113.0, 99), "Denver Nuggets": (118.5, 114.5, 100)}
            teams = {n: TeamRating(n, o, d, p, 14, extra={"ortg": o, "drtg": d, "pace": p, "net": o - d})
                     for n, (o, d, p) in r.items()}
            return RatingSet("nba", "DEMO ORtg/DRtg", "per_poss", teams, 115.0, 99.0, unit="pts/100")
        return None

    def scoreboard(self, sport, start, end):
        venues = {
            "demo-nfl-1": ("Huntington Bank Field", "Cleveland", "OH", False),
            "demo-nfl-2": ("Allegiant Stadium", "Las Vegas", "NV", True),
            "demo-nba-1": ("Madison Square Garden", "New York", "NY", True),
            "demo-nba-2": ("Ball Arena", "Denver", "CO", True),
        }
        out = []
        for g in self.games.get(sport.code, []):
            v = venues[g.id]
            out.append({"date": g.commence, "home": {"id": g.id + "-h", "name": g.home},
                        "away": {"id": g.id + "-a", "name": g.away},
                        "venue": v[0], "city": v[1], "state": v[2], "indoor": v[3]})
        return out

    def injuries(self, sport):
        n = self.now
        return {
            "nfl": [
                Injury("Las Vegas Raiders", "Starting QB (demo)", "QB", "Out", n - timedelta(hours=5),
                       "Ankle; ruled out after Wednesday practice"),
                Injury("Buffalo Bills", "Backup LB (demo)", "LB", "Questionable", n - timedelta(hours=20)),
                Injury("Cleveland Browns", "WR2 (demo)", "WR", "Out", n - timedelta(days=12)),
            ],
            "nba": [
                Injury("New York Knicks", "Starting PG (demo)", "PG", "Doubtful", n - timedelta(hours=3),
                       "Hamstring tightness"),
            ],
        }.get(sport.code, [])

    def news(self, sport):
        n = self.now
        return {
            "nba": [NewsItem("Knicks dealing with illness in locker room ahead of Celtics game",
                             "Several rotation players listed with flu-like symptoms.",
                             n - timedelta(hours=4), ["New York Knicks"])],
            "nfl": [NewsItem("Raiders QB ruled out Sunday; backup to start",
                             "The team announced the move after practice.", n - timedelta(hours=5),
                             ["Las Vegas Raiders"])],
        }.get(sport.code, [])

    def last_game(self, sport, team_id, before):
        if team_id == "demo-nba-1-h":
            return before - timedelta(hours=24)     # back-to-back
        if team_id == "demo-nba-1-a":
            return before - timedelta(days=2)
        return before - timedelta(days=7)

    def weather(self, city, state, when):
        if city == "Cleveland":
            return {"temp_f": 34, "wind_mph": 22, "gust_mph": 35, "precip_in": 0.1, "precip_prob": 70}
        return None
