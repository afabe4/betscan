import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from betscan.history import FALLBACK_RATES, compute_rates
from betscan.models import Candidate, Game, Outcome
from betscan.review import closing_value, find_result, grade
from betscan.signals.angles import best_teasers, breakeven_leg, teaser_legs, wind_adjust
from betscan.signals.price import price_candidates
from betscan.store import Store
from tests.test_betscan import NOW


def nfl_game(gid="g", home="Green Bay Packers", away="Chicago Bears", spread=-2.5, total=44.5,
             books=("dk", "fd", "pinnacle")):
    g = Game(gid, "nfl", NOW + timedelta(hours=30), home, away)
    for b in books:
        g.books[b] = {"spreads": [Outcome(home, -110, spread), Outcome(away, -110, -spread)],
                      "totals": [Outcome("Over", -110, total), Outcome("Under", -110, total)]}
    return g


def row(season, home_line, result, total_line=44, total=40, wind="", roof="outdoors"):
    return {"season": str(season), "game_type": "REG", "spread_line": str(home_line), "result": str(result),
            "total_line": str(total_line), "total": str(total), "wind": wind, "roof": roof}


class History(unittest.TestCase):
    def test_teaser_and_wind_rates(self):
        games = [
            row(2010, 2.5, 3),     # home favored by 2.5, wins by 3: away +8.5 covers
            row(2010, -2.5, -5),   # home dog +2.5 loses by 5 -> +8.5 covers
            row(2010, -2.5, -10),  # loses by 10 -> teaser leg loses
            row(2010, -2.5, -1, total_line=52),   # high total: separate bucket
            row(2010, 0, 0, total_line=40, total=33, wind="18"),
            row(2010, 0, 0, total_line=40, total=45, wind="16"),
            row(2010, 0, 0, total_line=40, total=30, wind="16", roof="dome"),  # ignored
            row(2030, -2.5, -5),   # after last season: ignored
        ]
        r = compute_rates(games, 2025)
        self.assertEqual((r.teaser_dog.wins, r.teaser_dog.losses), (2, 1))
        self.assertEqual(r.teaser_dog_high_total.n, 1)
        self.assertEqual(r.wind_rate(17).wins, 1)
        self.assertEqual(r.wind_rate(17).losses, 1)

    def test_breakeven(self):
        self.assertAlmostEqual(breakeven_leg(-120, 2), 0.7385, places=3)


class Wind(unittest.TestCase):
    def test_wind_moves_fair_prob_to_under(self):
        g = nfl_game()
        g.indoor, g.weather = False, {"wind_mph": 18, "gust_mph": 25}
        cands = {(c.market, c.side): c for c in price_candidates(g)}
        before = cands[("totals", "Under")].fair_prob
        sig = wind_adjust(g, cands, FALLBACK_RATES, NOW)
        self.assertIsNotNone(sig)
        self.assertGreater(cands[("totals", "Under")].fair_prob, before + 0.03)
        self.assertLess(cands[("totals", "Over")].fair_prob, 0.5)

    def test_calm_or_dome_no_change(self):
        g = nfl_game(); g.indoor, g.weather = False, {"wind_mph": 6}
        self.assertIsNone(wind_adjust(g, {}, FALLBACK_RATES, NOW))
        g.indoor, g.weather = True, {"wind_mph": 25}
        self.assertIsNone(wind_adjust(g, {}, FALLBACK_RATES, NOW))


class Teasers(unittest.TestCase):
    def test_legs_and_combos(self):
        a = nfl_game("a", spread=-2.5, total=44.5)                       # Bears +2.5 qualifies
        b = nfl_game("b", "New York Jets", "Miami Dolphins", spread=1.5, total=41)  # Jets +1.5
        c = nfl_game("c", "Denver Broncos", "Kansas City Chiefs", spread=-2.5, total=51)  # total too high
        d = nfl_game("d", "Detroit Lions", "Minnesota Vikings", spread=-6.5, total=44)    # dog too big
        legs = teaser_legs([a, b, c, d], FALLBACK_RATES)
        self.assertEqual({(x.game.id, x.side) for x in legs}, {("a", "Chicago Bears"), ("b", "New York Jets")})
        ts = best_teasers(legs, -120, 160)
        two = [t for t in ts if len(t.legs) == 2]
        self.assertEqual(len(two), 1)              # merged across the 3 books
        self.assertEqual(two[0].book.count(","), 2)
        self.assertGreater(two[0].ev, 0)
        self.assertEqual(best_teasers(legs, -200, 160), [])   # bad price -> nothing


class StoreReview(unittest.TestCase):
    def test_upgrade_keeps_bet_price_and_grades(self):
        g = nfl_game("rv", spread=-2.5)
        with tempfile.TemporaryDirectory() as d:
            st = Store(Path(d) / "s.sqlite")
            st.record_lines([g], NOW)
            c = Candidate(g, "spreads", "Chicago Bears", 2.5, "dk", -110, 0.5, -0.045, tier="WATCH")
            st.record_picks([c], NOW)
            c2 = Candidate(g, "spreads", "Chicago Bears", 3.5, "fd", -110, 0.55, 0.05, tier="BET")
            st.record_picks([c2], NOW + timedelta(hours=1))
            st.record_picks([c], NOW + timedelta(hours=2))   # downgrade ignored
            picks = st.picks()
            self.assertEqual(len(picks), 1)
            self.assertEqual((picks[0]["tier"], picks[0]["point"]), ("BET", 3.5))
            # closing line moved to Bears +1.5: we beat the close
            g.books = {"pinnacle": {"spreads": [Outcome(g.home, -110, -1.5), Outcome(g.away, -110, 1.5)]}}
            st.record_lines([g], g.commence - timedelta(minutes=20))
            snap = st.closing_snapshot(g.id, g.commence.isoformat())
            cv = closing_value(picks[0], snap)
            self.assertEqual(cv["close_point"], 1.5)
            self.assertGreater(cv["clv"], 0.03)

    def test_grade_and_match(self):
        p = {"market": "spreads", "side": "Chicago Bears", "point": 2.5, "home": "Green Bay Packers",
             "away": "Chicago Bears", "price": -110, "commence": "2026-11-22T18:00:00+00:00"}
        self.assertEqual(grade(p, 24, 21)[0], "L")         # Bears lose by 3
        self.assertEqual(grade({**p, "market": "teaser", "price": None}, 24, 21), ("W", None))
        self.assertEqual(grade({**p, "market": "totals", "side": "Under", "point": 45}, 24, 21)[0], "P")
        games = [{"home_team": "GB", "away_team": "CHI", "home_score": "24", "away_score": "21",
                  "gameday": "2026-11-22"}]
        from betscan.history import nfl_abbr_map
        self.assertEqual(find_result(p, games, nfl_abbr_map()), (24.0, 21.0))


if __name__ == "__main__":
    unittest.main()


class InjuryTracking(unittest.TestCase):
    def test_only_status_changes_are_news(self):
        from betscan.models import Injury
        with tempfile.TemporaryDirectory() as d:
            st = Store(Path(d) / "s.sqlite")
            first = [Injury("Team A", "QB1", "QB", "Questionable", NOW), Injury("Team A", "WR1", "WR", "Out", NOW)]
            st.track_injuries("nfl", first, NOW)
            self.assertTrue(all(i.reported is None for i in first))           # baseline run
            later = NOW + timedelta(hours=6)
            second = [Injury("Team A", "QB1", "QB", "Out", NOW), Injury("Team A", "WR1", "WR", "Out", NOW),
                      Injury("Team B", "QB2", "QB", "Doubtful", NOW)]
            st.track_injuries("nfl", second, later)
            self.assertEqual(second[0].reported, later)    # Questionable -> Out
            self.assertIsNone(second[1].reported)          # unchanged since baseline
            self.assertEqual(second[2].reported, later)    # newly listed


class NWSParse(unittest.TestCase):
    def test_grid(self):
        from datetime import datetime, timezone
        from betscan.sources.weather import parse_nws_grid
        props = {"windSpeed": {"values": [{"validTime": "2026-11-22T17:00:00+00:00/PT2H", "value": 30},
                                          {"validTime": "2026-11-22T19:00:00+00:00/PT2H", "value": 36}]},
                 "windGust": {"values": [{"validTime": "2026-11-22T16:00:00+00:00/PT6H", "value": 50}]},
                 "temperature": {"values": [{"validTime": "2026-11-22T18:00:00+00:00/PT1H", "value": 0}]},
                 "quantitativePrecipitation": {"values": [{"validTime": "2026-11-22T15:00:00+00:00/PT6H",
                                                           "value": 6.0}]}}
        w = parse_nws_grid(props, datetime(2026, 11, 22, 18, tzinfo=timezone.utc))
        self.assertAlmostEqual(w["wind_mph"], 34 * 0.621371, places=1)   # mean of 30,36,36 km/h
        self.assertAlmostEqual(w["temp_f"], 32.0)
        self.assertAlmostEqual(w["precip_in"], 1.0 / 25.4, places=3)
