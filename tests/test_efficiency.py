import random
import unittest

from betscan.efficiency.model import RatingSet, TeamRating, fit_scores, probabilities, project, resolve_name
from betscan.efficiency.providers import (nfl_epa_from_rows, parse_moneypuck, parse_nba, parse_sp,
                                          parse_torvik)
from betscan.models import Game, Outcome
from betscan.signals.efficiency import efficiency_signals
from tests.test_betscan import NOW


class Names(unittest.TestCase):
    def test_longest_prefix_wins(self):
        names = ["Michigan", "Michigan St.", "Miami FL", "Connecticut"]
        self.assertEqual(resolve_name("Michigan State Spartans", names), "Michigan St.")
        self.assertEqual(resolve_name("Michigan Wolverines", names), "Michigan")
        self.assertEqual(resolve_name("UConn Huskies", names), "Connecticut")
        self.assertIsNone(resolve_name("Duke Blue Devils", names))


class Projection(unittest.TestCase):
    def test_per_poss_and_hca(self):
        t = {"A": TeamRating("A", 120, 110, 100), "B": TeamRating("B", 110, 115, 100)}
        rs = RatingSet("nba", "t", "per_poss", t, league_off=115, league_pace=100)
        h, a = project(rs, t["A"], t["B"], neutral=True)
        self.assertAlmostEqual(h, 120.0); self.assertAlmostEqual(a, 105.0)
        h2, a2 = project(rs, t["A"], t["B"])
        self.assertAlmostEqual((h2 - a2) - (h - a), 2.0)

    def test_normal_probs_consistent(self):
        p_home = probabilities("nfl", 27, 20, "spreads", True, -7.0)
        self.assertAlmostEqual(p_home, 0.5, places=6)
        self.assertGreater(probabilities("nfl", 27, 20, "h2h", True, None), 0.65)
        over = probabilities("nfl", 24, 20, "totals", None, 41.5, "Over")
        under = probabilities("nfl", 24, 20, "totals", None, 41.5, "Under")
        self.assertAlmostEqual(over + under, 1.0)

    def test_poisson_probs(self):
        ml_h = probabilities("nhl", 3.4, 2.6, "h2h", True, None)
        ml_a = probabilities("nhl", 3.4, 2.6, "h2h", False, None)
        self.assertAlmostEqual(ml_h + ml_a, 1.0, places=6)
        self.assertGreater(ml_h, 0.55)
        o = probabilities("nhl", 3, 3, "totals", None, 6.0, "Over")
        u = probabilities("nhl", 3, 3, "totals", None, 6.0, "Under")
        self.assertAlmostEqual(o + u, 1.0)   # pushes removed

    def test_fit_scores_recovers_strength(self):
        rng = random.Random(1)
        true = {f"T{i}": (i - 5) * 1.5 for i in range(10)}
        games = []
        for _ in range(400):
            h, a = rng.sample(list(true), 2)
            games.append((h, a, 100 + true[h] + 1 + rng.gauss(0, 8), 100 + true[a] - 1 + rng.gauss(0, 8), False))
        rs = fit_scores(games, "nba")
        order = sorted(rs.teams.values(), key=lambda t: t.off - t.defn)
        self.assertEqual(order[-1].name, "T9")
        self.assertEqual(order[0].name, "T0")


class Providers(unittest.TestCase):
    def test_nfl_epa_rows(self):
        rows = []
        for i in range(400):
            good = i % 2 == 0
            rows.append({"game_id": f"g{i // 100}", "season_type": "REG", "week": "1",
                         "posteam": "BUF" if good else "MIA", "defteam": "MIA" if good else "BUF",
                         "play_type": "pass", "epa": "0.3" if good else "-0.2",
                         "success": "1" if good else "0", "wp": "0.5"})
        rows.append({**rows[0], "wp": "0.99", "epa": "50"})   # garbage time: dropped
        rs = nfl_epa_from_rows(rows, prior_plays=0, adjust_iters=0)
        self.assertAlmostEqual(rs.teams["Buffalo Bills"].off, 0.3)
        self.assertAlmostEqual(rs.teams["Buffalo Bills"].defn, -0.2)
        self.assertEqual(rs.teams["Buffalo Bills"].off_rank, 1)

    def test_parse_nba(self):
        data = {"resultSets": [{"headers": ["TEAM_NAME", "GP", "OFF_RATING", "DEF_RATING", "NET_RATING", "PACE"],
                                "rowSet": [["Boston Celtics", 10, 120.0, 110.0, 10.0, 98.0],
                                           ["Utah Jazz", 10, 108.0, 118.0, -10.0, 101.0]]}]}
        rs = parse_nba(data)
        self.assertGreater(rs.teams["Boston Celtics"].off, rs.teams["Utah Jazz"].off)
        self.assertLess(rs.teams["Boston Celtics"].off, 120.0)   # regressed toward mean

    def test_parse_torvik_by_header(self):
        text = "rank,team,conf,record,adjoe,oe Rank,adjde,de Rank,barthag,adjt\n" \
               "1,Houston,B12,5-0,120.1,3,88.2,1,.97,63.5\n2,Duke,ACC,5-0,124.0,1,93.0,6,.96,67.0\n"
        rs = parse_torvik(text)
        self.assertEqual(rs.find("Duke Blue Devils").pace, 67.0)
        with self.assertRaises(ValueError):
            parse_torvik("a,b\n1,2\n")

    def test_parse_moneypuck(self):
        text = ("team,situation,games_played,xGoalsFor,xGoalsAgainst,iceTime,xGoalsPercentage\n"
                "T.B,all,20,70,50,72000,0.58\nT.B,5on5,20,50,40,60000,0.55\nS.J,all,20,45,72,72000,0.38\n")
        rs = parse_moneypuck(text)
        self.assertIn("Tampa Bay Lightning", rs.teams)
        self.assertGreater(rs.teams["Tampa Bay Lightning"].off, rs.teams["San Jose Sharks"].off)

    def test_parse_sp(self):
        rs = parse_sp([{"team": "Ohio State", "rating": 30, "offense": {"rating": 40}, "defense": {"rating": 10}},
                       {"team": "nationalAverages", "offense": {"rating": 28}, "defense": {"rating": 28}},
                       {"team": "Kent State", "rating": -20, "offense": {"rating": 15}, "defense": {"rating": 35}}])
        self.assertEqual(len(rs.teams), 2)
        self.assertIsNotNone(rs.find("Ohio State Buckeyes"))


class Signal(unittest.TestCase):
    def _game(self):
        g = Game("e", "nba", NOW, "Home Team", "Away Team")
        for bk in ("pinnacle", "dk"):
            g.books[bk] = {"spreads": [Outcome("Home Team", -110, 1.5), Outcome("Away Team", -110, -1.5)]}
        t = {"Home Team": TeamRating("Home Team", 122, 108, 100, 20),
             "Away Team": TeamRating("Away Team", 110, 118, 100, 20)}
        return g, RatingSet("nba", "test", "per_poss", t, league_off=115, league_pace=100)

    def test_flags_side_model_likes_context_only_by_default(self):
        g, rs = self._game()
        sigs = efficiency_signals(g, rs)
        self.assertTrue(sigs)
        self.assertTrue(all(s.side == "Home Team" and s.strength == 0 for s in sigs))
        weighted = efficiency_signals(g, rs, weight=1.0)
        self.assertTrue(all(0 < s.strength <= 2.5 for s in weighted))


if __name__ == "__main__":
    unittest.main()


class NamesRaw(unittest.TestCase):
    def test_plain_name_before_alias(self):
        names = ["SMU", "Southern", "UNLV", "Nevada", "Kansas", "Kansas St."]
        self.assertEqual(resolve_name("SMU Mustangs", names), "SMU")
        self.assertEqual(resolve_name("UNLV Rebels", names), "UNLV")
        self.assertEqual(resolve_name("Kansas Jayhawks", names), "Kansas")
        self.assertEqual(resolve_name("Kansas State Wildcats", names), "Kansas St.")
