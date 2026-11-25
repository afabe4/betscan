import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from betscan.scheduled import _bets, _key, prune
from betscan.store import Store


class Scheduled(unittest.TestCase):
    def test_prune_keeps_recent_lines_and_picks(self):
        now = datetime(2027, 1, 20, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.sqlite"
            Store(p)
            db = sqlite3.connect(p)
            for days in (30, 2):
                db.execute("INSERT INTO lines VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (now.isoformat(), f"g{days}", "ncaab", "A @ B", (now - timedelta(days=days)).isoformat(),
                            "fanduel", "totals", "Over", 140.5, -110))
            db.commit(); db.close()
            self.assertEqual(prune(p, now), 1)
            self.assertEqual(sqlite3.connect(p).execute("SELECT game_id FROM lines").fetchall(), [("g2",)])

    def test_new_bet_detection(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "latest_ncaab.json"
            b = {"game": "A @ B", "market": "totals", "side": "Under"}
            f.write_text(json.dumps({"bets": [b]}))
            self.assertEqual({_key(x) for x in _bets(f)}, {("A @ B", "totals", "Under")})
            self.assertEqual(_bets(Path(d) / "missing.json"), [])


class State(unittest.TestCase):
    def test_roundtrip_and_ignores_unknown_files(self):
        from betscan.state import export, import_
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            Store(Path(a) / "snapshots.sqlite")
            (Path(a) / "latest_ncaab.json").write_text('{"bets": []}')
            export(Path(a), Path(a) / "state.json")
            doc = json.loads((Path(a) / "state.json").read_text())
            doc["files"]["../evil.py"] = "cHJpbnQoMSk="
            (Path(a) / "state.json").write_text(json.dumps(doc))
            sizes = import_(Path(a) / "state.json", Path(b))
            self.assertEqual(set(sizes), {"snapshots.sqlite", "latest_ncaab.json"})
            self.assertFalse((Path(b).parent / "evil.py").exists())
            self.assertEqual((Path(b) / "latest_ncaab.json").read_text(), '{"bets": []}')


class TopN(unittest.TestCase):
    def test_top_n_demotes_the_rest(self):
        from datetime import timedelta
        from betscan.config import Settings
        from betscan.engine import analyze
        from betscan.models import Game, Outcome
        now = datetime(2027, 1, 20, tzinfo=timezone.utc)
        games = []
        for i, price in enumerate((120, 115, 110, 105)):     # Pinnacle fair ~50%; soft book plus-money = edge
            g = Game(f"g{i}", "ncaab", now + timedelta(hours=5), f"Home{i}", f"Away{i}")
            g.books = {"pinnacle": {"spreads": [Outcome(f"Home{i}", -105, -2.5), Outcome(f"Away{i}", -105, 2.5)]},
                       "draftkings": {"spreads": [Outcome(f"Home{i}", price, -2.5), Outcome(f"Away{i}", -140, 2.5)]}}
            games.append(g)
        cands = analyze(games, Settings(bet_books=("draftkings",)), now)
        ranked = sorted((c for c in cands if c.tier == "BET"), key=lambda c: -c.ev)
        self.assertEqual(len(ranked), 4)
        for c in ranked[2:]:      # what `scan --top 2` does
            c.tier = "ALSO"
        self.assertEqual([c.best_price for c in cands if c.tier == "BET"], [120, 115])


class StatsExport(unittest.TestCase):
    def test_rows_and_labels(self):
        from betscan.stats import rows_from
        base = {"rowid": 1, "commence": "2026-11-18T01:00:00+00:00", "sport": "ncaab", "label": "A @ B",
                "market": "totals", "side": "Over", "selection": "Over 140.5", "book": "fanduel", "price": -110,
                "stake": 0.5, "ev": 0.02, "kinds": "ncaab_early", "tier": "EARLY", "result": "W",
                "profit": 0.909, "clv": -0.03}
        r = rows_from([base])[0]
        self.assertEqual((r["date"], r["play"], r["status"], r["book"]), ("2026-11-17", "Early totals", "Win", "FanDuel"))
        self.assertAlmostEqual(r["dollars"], 0.45, places=2)
        self.assertIs(r["beat_close"], False)
        self.assertEqual((r["clv_pct"], r["edge_pct"]), (-3.0, 2.0))
        pend = rows_from([{**base, "result": None, "profit": None, "clv": None, "tier": "BET", "kinds": ""}])[0]
        self.assertEqual((pend["status"], pend["dollars"], pend["play"]), ("Pending", None, "Price edge"))


class StateV2(unittest.TestCase):
    def test_compressed_roundtrip_and_reads_v1(self):
        import base64
        from betscan.state import export, import_
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            (Path(a) / "latest_ncaab.json").write_text('{"bets": []}' + " " * 5000)
            export(Path(a), Path(a) / "s.json")
            doc = json.loads((Path(a) / "s.json").read_text())
            self.assertEqual(doc["version"], 2)
            self.assertLess(len(doc["files"]["latest_ncaab.json"]), 200)      # compressed
            import_(Path(a) / "s.json", Path(b))
            self.assertTrue((Path(b) / "latest_ncaab.json").read_text().startswith('{"bets": []}'))
            v1 = {"version": 1, "files": {"latest_ncaab.json": base64.b64encode(b'{"bets": [1]}').decode()}}
            (Path(a) / "v1.json").write_text(json.dumps(v1))
            import_(Path(a) / "v1.json", Path(b))
            self.assertEqual((Path(b) / "latest_ncaab.json").read_text(), '{"bets": [1]}')
