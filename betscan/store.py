"""SQLite: line snapshots every scan, plus every pick for closing-line review.

Opening line = first snapshot of a game. Closing line = last snapshot before
kickoff, so schedule a scan shortly before each kickoff window (see README).
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
  ts TEXT, game_id TEXT, sport TEXT, label TEXT, home TEXT, away TEXT, commence TEXT,
  market TEXT, side TEXT, point REAL, selection TEXT, book TEXT, price REAL,
  fair_prob REAL, ev REAL, score REAL, tier TEXT, stake REAL, kinds TEXT,
  close_point REAL, close_price REAL, close_fair REAL, clv REAL,
  result TEXT, profit REAL,
  UNIQUE(game_id, market, side)
);
CREATE TABLE IF NOT EXISTS injuries (
  sport TEXT, team TEXT, player TEXT, position TEXT, status TEXT, first_seen TEXT, last_seen TEXT,
  PRIMARY KEY (sport, team, player)
);
"""
BASELINE = "1970-01-01T00:00:00+00:00"   # status already in place when tracking started
# Lower = stronger. A pick that upgrades (WATCH -> BET) is re-recorded at the
# price available when it upgraded; otherwise the first sighting is kept.
TIER_RANK = {"BET": 0, "EARLY": 1, "TEASER": 1, "VERIFY": 2, "ALT": 3, "LEAN": 4, "WATCH": 5, "MODEL": 6}
PICK_COLS = ("ts", "game_id", "sport", "label", "home", "away", "commence", "market", "side", "point",
             "selection", "book", "price", "fair_prob", "ev", "score", "tier", "stake", "kinds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(picks)")]
        if cols and "side" not in cols:      # pre-CLV schema: keep it, start fresh
            self.db.execute("ALTER TABLE picks RENAME TO picks_v1")
        self.db.executescript(SCHEMA)

    # --- lines ---------------------------------------------------------------
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

    def closing_snapshot(self, game_id: str, commence: str) -> dict[str, dict[str, list[tuple]]]:
        """book -> market -> [(outcome, point, price)] at the last scan before kickoff."""
        row = self.db.execute("SELECT MAX(ts) FROM lines WHERE game_id=? AND ts < ?",
                              (game_id, commence)).fetchone()
        if not row or not row[0]:
            return {}
        out: dict = {}
        for book, market, outcome, point, price in self.db.execute(
                "SELECT book, market, outcome, point, price FROM lines WHERE game_id=? AND ts=?",
                (game_id, row[0])):
            out.setdefault(book, {}).setdefault(market, []).append((outcome, point, price))
        return out

    # --- injuries --------------------------------------------------------------
    def track_injuries(self, sport: str, injuries, now: datetime) -> None:
        """Set each Injury.reported to when we first saw its *current* status.

        ESPN's injury timestamps refresh daily, so they can't tell news from old
        news. Instead: the first scan records a baseline (nothing counts as new);
        afterwards a new entry or a status change (Questionable -> Out) is dated now.
        """
        have_baseline = self.db.execute("SELECT 1 FROM injuries WHERE sport=? LIMIT 1", (sport,)).fetchone()
        ts = now.isoformat()
        for inj in injuries:
            key = (sport, inj.team, inj.player)
            row = self.db.execute("SELECT status, first_seen FROM injuries WHERE sport=? AND team=? AND player=?",
                                  key).fetchone()
            if row is None:
                first = ts if have_baseline else BASELINE
                self.db.execute("INSERT INTO injuries VALUES (?,?,?,?,?,?,?)",
                                (*key, inj.position, inj.status, first, ts))
            elif row[0] != inj.status:
                first = ts
                self.db.execute("UPDATE injuries SET status=?, first_seen=?, last_seen=? "
                                "WHERE sport=? AND team=? AND player=?", (inj.status, ts, ts, *key))
            else:
                first = row[1]
                self.db.execute("UPDATE injuries SET last_seen=? WHERE sport=? AND team=? AND player=?",
                                (ts, *key))
            inj.reported = None if first == BASELINE else datetime.fromisoformat(first)
        self.db.commit()

    # --- picks ---------------------------------------------------------------
    def _upsert(self, row: dict):
        cur = self.db.execute("SELECT tier FROM picks WHERE game_id=? AND market=? AND side=?",
                              (row["game_id"], row["market"], row["side"])).fetchone()
        if cur and TIER_RANK.get(cur[0], 9) <= TIER_RANK.get(row["tier"], 9):
            return
        if cur:
            self.db.execute("DELETE FROM picks WHERE game_id=? AND market=? AND side=?",
                            (row["game_id"], row["market"], row["side"]))
        self.db.execute(f"INSERT INTO picks ({','.join(PICK_COLS)}) VALUES ({','.join('?' * len(PICK_COLS))})",
                        [row[c] for c in PICK_COLS])

    def record_picks(self, candidates, ts: datetime, teaser_legs=()):
        for c in candidates:
            tier = c.tier
            # A context-only model note (teams under 10 games) isn't what the bet is based on.
            kinds = sorted({s.kind for s in c.signals
                            if not (s.kind == "ncaab_model" and "-> grade " not in s.detail)})
            if tier == "PASS" and "model" in kinds:
                tier = "MODEL"     # shadow pick: tracks the context-only model vs the close
            if tier == "PASS":
                continue
            g = c.game
            self._upsert(dict(ts=ts.isoformat(), game_id=g.id, sport=g.sport, label=g.label, home=g.home,
                              away=g.away, commence=g.commence.isoformat(), market=c.market, side=c.side,
                              point=c.point, selection=c.selection, book=c.best_book, price=c.best_price,
                              fair_prob=c.fair_prob, ev=c.ev, score=c.score, tier=tier, stake=c.stake,
                              kinds=",".join(kinds)))
        for leg in teaser_legs:
            g = leg.game
            self._upsert(dict(ts=ts.isoformat(), game_id=g.id, sport=g.sport, label=g.label, home=g.home,
                              away=g.away, commence=g.commence.isoformat(), market="teaser", side=leg.side,
                              point=leg.point, selection=leg.label, book=leg.book, price=None,
                              fair_prob=leg.prob, ev=None, score=None, tier="TEASER", stake=0,
                              kinds="teaser"))
        self.db.commit()

    def picks(self, where: str = "1=1", args=()) -> list[dict]:
        cur = self.db.execute(f"SELECT rowid, * FROM picks WHERE {where} ORDER BY commence", args)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def update_pick(self, rowid: int, **fields):
        sets = ",".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE picks SET {sets} WHERE rowid=?", [*fields.values(), rowid])
        self.db.commit()
