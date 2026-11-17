import io
import unittest
import zipfile
from datetime import date, datetime, timedelta, timezone

from betscan.models import Game, Outcome
from betscan.ncaab.archive import parse_season, read_xlsx
from betscan.ncaab.live import NameIndex, calibrated, totals_signal
from betscan.ncaab.ratings import fit
from betscan.signals.price import price_candidates


def xlsx(rows):
    """Build a tiny xlsx in memory (inline strings) for the reader."""
    def cell(ref, v):
        if isinstance(v, str):
            return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>'
        return f'<c r="{ref}"><v>{v}</v></c>'
    body = "".join(
        f'<row r="{i + 1}">' + "".join(cell(f"{chr(65 + j)}{i + 1}", v) for j, v in enumerate(r)) + "</row>"
        for i, r in enumerate(rows))
    sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f"<sheetData>{body}</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


HEAD = ["Date", "Rot", "VH", "Team", "1st", "2nd", "Final", "Open", "Close", "ML", "2H"]


class Archive(unittest.TestCase):
    def test_parse_pairs_and_signs(self):
        rows = read_xlsx(xlsx([HEAD,
                               [1105, 505, "V", "TennMartin", 36, 35, 71, 140, 144, 13000, 75],
                               [1105, 506, "H", "MemphisU", 49, 53, 102, 35, 35.5, -39000, 16],
                               [115, 507, "V", "Duke", 30, 30, 60, 6, "pk", -120, 2],
                               [115, 508, "H", "Clemson", 30, 31, 61, 131, 133, 100, 66],
                               [116, 509, "V", "Iona", 30, 30, 60, "NL", 4, -120, 2],
                               [116, 510, "H", "Manhattan", 30, 31, 61, "NL", 130, 100, 66]]))
        g = parse_season(rows, "2007-08")
        self.assertEqual(len(g), 3)
        self.assertEqual((g[0].home, g[0].close_spread, g[0].close_total), ("MemphisU", 35.5, 144.0))
        self.assertEqual(g[0].day, date(2007, 11, 5))
        self.assertEqual((g[1].open_spread, g[1].close_spread, g[1].day), (-6.0, 0.0, date(2008, 1, 15)))
        self.assertIsNone(g[2].open_total)
        self.assertEqual(g[2].close_spread, -4.0)    # visitor Iona favored by 4


class Ratings(unittest.TestCase):
    def test_prior_and_cap(self):
        games = [("A", "B", 90, 60, False, date(2026, 1, 1))] * 3
        r = fit(games, ridge=1, hca=0, cap=10)
        self.assertLess(r.margin("A", "B", True), 10.5)
        prior = r.carry(0.5)
        r2 = fit([], prior=prior, ridge=6)
        self.assertGreater(r2.margin("A", "B", True), 0)


class Names(unittest.TestCase):
    def test_st_handling(self):
        idx = NameIndex(["Arizona State Sun Devils", "St. John's Red Storm", "Arizona Wildcats"])
        self.assertEqual(idx.find("Arizona St Sun Devils"), "Arizona State Sun Devils")
        self.assertEqual(idx.find("St. John's Red Storm"), "St. John's Red Storm")
        self.assertEqual(idx.find("Arizona Wildcats"), "Arizona Wildcats")


class Signal(unittest.TestCase):
    def _setup(self, games_played):
        g = Game("c1", "ncaab", datetime(2027, 1, 20, tzinfo=timezone.utc), "Home U", "Away U")
        for b in ("draftkings", "fanduel", "betmgm"):
            g.books[b] = {"totals": [Outcome("Over", -110, 140.5), Outcome("Under", -110, 140.5)]}
        games = [("Home U", "X", 80, 75, False, date(2027, 1, 1)), ("Away U", "Y", 82, 78, False, date(2027, 1, 1))]
        r = fit(games * games_played, ridge=0.5, hca=3)
        return g, r

    def test_bet_only_after_min_games(self):
        g, r = self._setup(12)
        cands = {(c.market, c.side): c for c in price_candidates(g, ("draftkings", "fanduel"))}
        sig = totals_signal(g, cands, r, NameIndex(r.o), None)
        self.assertEqual(sig.side, "Over")
        self.assertGreater(cands[("totals", "Over")].ev, 0)
        g2, r2 = self._setup(2)
        cands2 = {(c.market, c.side): c for c in price_candidates(g2, ("draftkings", "fanduel"))}
        sig2 = totals_signal(g2, cands2, r2, NameIndex(r2.o), None)
        self.assertIn("context only", sig2.detail)

    def test_calibration(self):
        self.assertIsNone(calibrated(2.5))
        self.assertAlmostEqual(calibrated(3.2), 0.534)
        self.assertAlmostEqual(calibrated(-7), 0.58)


if __name__ == "__main__":
    unittest.main()


class Grades(unittest.TestCase):
    def test_b_play_passes_once_line_moved(self):
        g, r = Signal._setup(Signal(), 12)
        cands = {(c.market, c.side): c for c in price_candidates(g, ("draftkings", "fanduel"))}
        idx = NameIndex(r.o)
        mt = sum(r.project("Home U", "Away U", False))
        # put the line 4 pts under the model: a B play
        for b in g.books.values():
            b["totals"] = [Outcome("Over", -110, round(mt - 4)), Outcome("Under", -110, round(mt - 4))]
        cands = {(c.market, c.side): c for c in price_candidates(g, ("draftkings", "fanduel"))}
        self.assertIn("grade B", totals_signal(g, cands, r, idx, 0.0).detail)
        cands = {(c.market, c.side): c for c in price_candidates(g, ("draftkings", "fanduel"))}
        self.assertIn("pass", totals_signal(g, cands, r, idx, 1.0).detail)
        # 7 pts off: an A play even after a move
        for b in g.books.values():
            b["totals"] = [Outcome("Over", -110, round(mt - 7)), Outcome("Under", -110, round(mt - 7))]
        cands = {(c.market, c.side): c for c in price_candidates(g, ("draftkings", "fanduel"))}
        self.assertIn("grade A", totals_signal(g, cands, r, idx, 1.0).detail)
