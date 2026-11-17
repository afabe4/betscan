"""SQLite snapshots of every scan, used to measure line movement over time.

Run the scanner on a schedule (e.g. every 30-60 min) and the movement signal
gets better: it can tell whether the market already reacted to a news story.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS lines (
  ts TEXT, game_id TEXT, sport TEXT, label TEXT, commence TEXT,
  book TEXT, market TEXT, outcome TEXT, point REAL, price REAL
);
CREATE INDEX IF NOT EXISTS lines_game ON lines(game_id, market, outcome, ts);
CREATE TABLE IF NOT EXISTS picks (
  ts TEXT, game_id TEXT, label TEXT, commence TEXT, market TEXT, selection TEXT,
  book TEXT, price REAL, fair_prob REAL, ev REAL, score REAL, tier TEXT, stake REAL,
  closing_price REAL, result TEXT
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)

    def record_lines(self, games, ts: datetime):
        rows = []
        for g in games:
            for book, markets in g.books.items():
                for mkey, outcomes in markets.items():
                    for o in outcomes:
                        rows.append((ts.isoformat(), g.id, g.sport, g.label, g.commence.isoformat(),
                                     book, mkey, o.name, o.point, o.price))
        self.db.executemany("INSERT INTO lines VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        self.db.commit()

    def first_seen(self, game_id: str) -> datetime | None:
        row = self.db.execute("SELECT MIN(ts) FROM lines WHERE game_id=?", (game_id,)).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def history(self, game_id: str, market: str, outcome: str) -> list[tuple[datetime, float | None, float]]:
        """Median point/price across books, per snapshot, oldest first."""
        rows = self.db.execute(
            "SELECT ts, point, price FROM lines WHERE game_id=? AND market=? AND outcome=? ORDER BY ts",
            (game_id, market, outcome)).fetchall()
        by_ts: dict[str, list] = {}
        for ts, point, price in rows:
            by_ts.setdefault(ts, []).append((point, price))
        out = []
        for ts, vals in by_ts.items():
            pts = sorted(v[0] for v in vals if v[0] is not None)
            prs = sorted(v[1] for v in vals)
            out.append((datetime.fromisoformat(ts), pts[len(pts) // 2] if pts else None, prs[len(prs) // 2]))
        return out

    def record_picks(self, candidates, ts: datetime):
        rows = [(ts.isoformat(), c.game.id, c.game.label, c.game.commence.isoformat(), c.market,
                 c.selection, c.best_book, c.best_price, c.fair_prob, c.ev, c.score, c.tier, c.stake,
                 None, None) for c in candidates if c.tier in ("BET", "LEAN")]
        self.db.executemany("INSERT INTO picks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.db.commit()
