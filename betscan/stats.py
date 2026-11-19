"""Export every recommended bet for the stats dashboard.

    BETSCAN_HOME=data python -m betscan stats-export data/stats.json

Writes {"rows": [...], "meta": [...]}: one row per bet the card recommended
(BET, EARLY, TEASER), graded or pending, with this file's own code in `meta` so
readers can see exactly how each column is made.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PLAY = {"ncaab_model": "Totals model", "early": "Early totals", "price": "Price edge",
        "teaser": "Teaser leg", "wind": "Wind under"}
STATUS = {"W": "Win", "L": "Loss", "P": "Push", None: "Pending"}


def play_of(p: dict) -> str:
    kinds = set((p.get("kinds") or "").split(","))
    if p["market"] == "teaser" or "teaser" in kinds:
        return PLAY["teaser"]
    if p["tier"] == "EARLY" or "ncaab_early" in kinds:
        return PLAY["early"]
    if "ncaab_model" in kinds and p["market"] == "totals":
        return PLAY["ncaab_model"]
    if "wind" in kinds and p["side"] == "Under":
        return PLAY["wind"]
    return PLAY["price"]


def rows_from(picks: list[dict]) -> list[dict]:
    out = []
    for p in sorted(picks, key=lambda p: p["commence"]):
        tip = datetime.fromisoformat(p["commence"]).astimezone(ET)
        units = p["profit"]
        stake = p["stake"] or 0.0
        clv = p["clv"]
        out.append({
            "bet_id": str(p["rowid"]),
            "date": tip.strftime("%Y-%m-%d"),
            "tip_et": tip.strftime("%Y-%m-%d %H:%M"),
            "sport": p["sport"].upper(),
            "game": p["label"],
            "play": play_of(p),
            "pick": p["selection"],
            "book": {"draftkings": "DraftKings", "fanduel": "FanDuel"}.get(p["book"], p["book"] or ""),
            "price": p["price"],
            "stake": round(stake, 2),
            "edge_pct": None if p["ev"] is None else round(p["ev"] * 100, 1),
            "status": STATUS.get(p["result"], "Pending"),
            "units": None if units is None else round(units, 3),
            "dollars": None if units is None else round(units * stake, 2),
            # Teaser legs record CLV in points, everything else as a % edge vs the close.
            "clv_pct": None if clv is None or p["market"] == "teaser" else round(clv * 100, 1),
            "beat_close": None if clv is None or p["market"] == "teaser" else clv > 0,
        })
    return out


def export(db_path: Path, out: Path) -> int:
    from .store import Store
    picks = Store(db_path).picks("tier IN ('BET', 'EARLY', 'TEASER')") if db_path.exists() else []
    rows = rows_from(picks)
    meta = [
        {"label": "stats.py", "value": Path(__file__).read_text(), "type": "code", "lang": "python"},
        {"label": "Input", "value": "betscan pick log (snapshots.sqlite): every bet the card recommended, "
                                    "graded against final scores and the last line scan before tip-off"},
        {"label": "Exported", "value": datetime.now(timezone.utc).isoformat(timespec="seconds")},
    ]
    out.write_text(json.dumps({"rows": rows, "meta": meta}))
    return len(rows)


def main(argv: list[str]) -> int:
    from .config import Settings
    if len(argv) != 1:
        print("usage: python -m betscan stats-export FILE")
        return 2
    n = export(Settings().db_path, Path(argv[0]))
    print(f"stats export: {n} bets -> {argv[0]}")
    return 0
