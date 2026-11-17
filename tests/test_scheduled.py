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
