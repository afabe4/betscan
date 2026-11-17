"""One scheduled run: scan, grade, rebuild the card, report what's new.

    BETSCAN_HOME=data python -m betscan run --sports ncaab --card data/card.html

Prints a final line the scheduler turns into an alert:
    ALERT: 2 new bets: Under 141.5 -110 FanDuel (A); ...
    QUIET: no new bets (1 still on the card)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KEEP_LINES_DAYS = 14   # line history needed for openers/closes of recent games only


def _bets(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text())["bets"]
    except (OSError, ValueError, KeyError):
        return []


def _key(b: dict) -> tuple:
    return (b["game"], b["market"], b["side"])


def prune(db_path: Path, now: datetime) -> int:
    if not db_path.exists():
        return 0
    db = sqlite3.connect(db_path)
    cut = (now - timedelta(days=KEEP_LINES_DAYS)).isoformat()
    n = db.execute("DELETE FROM lines WHERE commence < ?", (cut,)).rowcount
    db.commit()
    db.execute("VACUUM")
    db.close()
    return n


def run(argv: list[str]) -> int:
    from .card import render
    from .cli import main as cli_main
    from .config import Settings
    from .http import Fetcher
    from .review import run as review_run
    from .store import Store

    import argparse
    ap = argparse.ArgumentParser(prog="betscan run")
    ap.add_argument("--sports", default="ncaab")
    ap.add_argument("--card", type=Path, required=True)
    a = ap.parse_args(argv)
    st = Settings()
    now = datetime.now(timezone.utc)
    codes = [c.strip() for c in a.sports.split(",")]

    before = {_key(b) for c in codes for b in _bets(st.cache_dir / f"latest_{c}.json")}
    rc = cli_main(["scan", "--sports", a.sports])
    if rc:
        print(f"ERROR: scan failed with code {rc}")
        return rc
    try:
        review_run(Store(st.db_path), Fetcher(st.cache_dir), now)
    except Exception as e:  # noqa: BLE001 - grading can wait for the next run
        print(f"  [review] skipped: {e}", file=sys.stderr)
    pruned = prune(st.db_path, now)
    a.card.write_text(render(st, now))

    current = [b for c in codes for b in _bets(st.cache_dir / f"latest_{c}.json")]
    new = [b for b in current if _key(b) not in before]
    print(f"  [run] {len(current)} bets on the card, {len(new)} new, pruned {pruned} old line rows")
    if new:
        def one(b):
            book = {"draftkings": "DraftKings", "fanduel": "FanDuel"}.get(b["book"], b["book"])
            price = f"+{b['price']:.0f}" if b["price"] > 0 else f"{b['price']:.0f}"
            grade = f" ({b['grade']})" if b.get("grade") else ""
            return f"{b['selection']} {price} {book}{grade}"
        print(f"ALERT: {len(new)} new bet{'s' if len(new) > 1 else ''}: " + "; ".join(one(b) for b in new))
    else:
        print(f"QUIET: no new bets ({len(current)} still on the card)")
    return 0
