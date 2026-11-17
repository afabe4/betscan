import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from betscan.config import SPORTS, Settings
from betscan.context import DemoContext, demo_games
from betscan.engine import analyze, dedupe_correlated, enrich
from betscan.models import Game, Injury, NewsItem, Outcome
from betscan.odds_math import american_to_decimal, expected_value, kelly_fraction, no_vig, shift_prob
from betscan.signals.price import price_candidates
from betscan.signals.situational import news_signals
from betscan.sources import espn
from betscan.sources.odds_api import parse_odds
from betscan.store import Store

NOW = datetime(2026, 11, 17, 18, tzinfo=timezone.utc)


class OddsMath(unittest.TestCase):
    def test_conversions(self):
        self.assertAlmostEqual(american_to_decimal(150), 2.5)
        self.assertAlmostEqual(american_to_decimal(-200), 1.5)

    def test_no_vig_sums_to_one(self):
        p = no_vig([-110, -110])
        self.assertAlmostEqual(sum(p), 1)
        self.assertAlmostEqual(p[0], 0.5)

    def test_ev_and_kelly(self):
        self.assertAlmostEqual(expected_value(0.5, 110), 0.05)
        self.assertEqual(kelly_fraction(0.4, -110), 0.0)
        self.assertGreater(kelly_fraction(0.55, 100), 0)

    def test_nfl_key_number_worth_more(self):
        through_3 = shift_prob(0.5, 2.5, 3.5, "nfl", "spreads") - 0.5
        through_5 = shift_prob(0.5, 4.5, 5.5, "nfl", "spreads") - 0.5
        self.assertGreater(through_3, through_5)


def game(**books):
    g = Game("g1", "nba", NOW + timedelta(hours=5), "Home Team", "Away Team")
    for bk, (hp, ap) in books.items():
        g.books[bk] = {"h2h": [Outcome("Home Team", hp), Outcome("Away Team", ap)]}
    return g


class Price(unittest.TestCase):
    def test_finds_soft_book_edge(self):
        g = game(pinnacle=(-150, 135), soft=(-145, 155), other=(-160, 140))
        best = {c.side: c for c in price_candidates(g)}
        self.assertEqual(best["Away Team"].best_book, "soft")
        self.assertGreater(best["Away Team"].ev, 0.03)
        self.assertLess(best["Home Team"].ev, 0.01)

    def test_consensus_when_no_sharp(self):
        g = game(a=(-110, -110), b=(-112, -108), c=(-108, -112), d=(105, -125))
        home = next(c for c in price_candidates(g) if c.side == "Home Team")
        self.assertEqual(home.best_book, "d")
        self.assertGreater(home.ev, 0)


class Situational(unittest.TestCase):
    def test_news_attributed_to_headline_subject(self):
        g = Game("g", "nba", NOW, "New York Knicks", "Boston Celtics")
        g.news = [NewsItem("Knicks hit by illness before Celtics game", "", NOW - timedelta(hours=1))]
        sigs = news_signals(g, Settings(), NOW)
        self.assertTrue(sigs)
        self.assertTrue(all(s.side == "Boston Celtics" for s in sigs))

    def test_old_news_ignored(self):
        g = Game("g", "nba", NOW, "New York Knicks", "Boston Celtics")
        g.news = [NewsItem("Knicks guard suspended", "", NOW - timedelta(days=5))]
        self.assertEqual(news_signals(g, Settings(), NOW), [])

    def test_movement_discounts_priced_injury(self):
        """If the line already moved hard toward the injury, the signal shrinks."""
        def g_at(away_spread):
            g = Game("mv", "nfl", NOW + timedelta(days=2), "Home", "Away")
            for bk in ("pinnacle", "dk", "fd"):
                g.books[bk] = {"spreads": [Outcome("Home", -110, -away_spread), Outcome("Away", -110, away_spread)]}
            g.injuries = [Injury("Home", "QB1", "QB", "Out", NOW - timedelta(hours=2))]
            return g
        with tempfile.TemporaryDirectory() as d:
            store = Store(Path(d) / "s.sqlite")
            store.record_lines([g_at(3.0)], NOW - timedelta(hours=6))   # Away +3
            store.record_lines([g_at(-3.5)], NOW)                      # Away -3.5 after news
            cands = analyze([g_at(-3.5)], Settings(), NOW, store)
        away = next(c for c in cands if c.side == "Away")
        inj = next(s for s in away.signals if s.kind == "injury")
        self.assertLess(inj.strength, 1.5)
        self.assertIn("priced", inj.detail)


class Parsers(unittest.TestCase):
    def test_odds_api_shape(self):
        payload = [{"id": "x", "commence_time": "2026-11-20T01:15:00Z", "home_team": "H", "away_team": "A",
                    "bookmakers": [{"key": "pinnacle", "markets": [
                        {"key": "spreads", "outcomes": [{"name": "H", "price": -105, "point": -3},
                                                        {"name": "A", "price": -105, "point": 3}]}]}]}]
        g = parse_odds(payload, SPORTS["nfl"])[0]
        self.assertEqual(g.books["pinnacle"]["spreads"][1].point, 3)

    def test_espn_shapes(self):
        board = espn.parse_scoreboard({"events": [{"competitions": [{
            "date": "2026-11-20T01:15Z", "venue": {"fullName": "Field", "indoor": False,
                                                   "address": {"city": "Chicago", "state": "IL"}},
            "competitors": [{"homeAway": "home", "team": {"id": 3, "displayName": "Chicago Bears"}},
                            {"homeAway": "away", "team": {"id": 9, "displayName": "Green Bay Packers"}}]}]}]})
        self.assertEqual(board[0]["city"], "Chicago")
        self.assertEqual(board[0]["date"].hour, 1)
        inj = espn.parse_injuries({"injuries": [{"displayName": "Chicago Bears", "injuries": [
            {"status": "Out", "date": "2026-11-17T15:00Z",
             "athlete": {"displayName": "X", "position": {"abbreviation": "QB"}}}]}]})
        self.assertEqual((inj[0].position, inj[0].status), ("QB", "Out"))
        self.assertEqual(espn.parse_scoreboard({"weird": 1}), [])


class Demo(unittest.TestCase):
    def test_demo_end_to_end(self):
        slate = demo_games(NOW)
        ctx = DemoContext(NOW, slate)
        cands = []
        for code, games in slate.items():
            enrich(games, SPORTS[code], ctx, NOW, log=lambda m: None)
            cands += analyze(games, Settings(), NOW)
        dedupe_correlated(cands)
        bets = {(c.game.id, c.selection) for c in cands if c.tier == "BET"}
        self.assertIn(("demo-nfl-1", "Under 42.5"), bets)        # wind game
        self.assertIn(("demo-nba-2", "Phoenix Suns ML"), bets)   # pure price
        self.assertTrue(any(c.tier == "ALT" for c in cands))     # correlated side marked


if __name__ == "__main__":
    unittest.main()
