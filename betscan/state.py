"""Pack/unpack scheduled-run state as one JSON file, so it can live next to the bet card page.

    python -m betscan state export data/state.json   # after a run
    python -m betscan state import data/state.json   # before a run

The bet card page stores this file beside itself; each scheduled run reads it, runs,
and publishes the updated copy with the new card.
"""
from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

FILES = ("snapshots.sqlite", "latest_ncaab.json", "latest_nfl.json")


def export(home: Path, out: Path) -> dict:
    db = home / "snapshots.sqlite"
    if db.exists():   # compact before packing
        con = sqlite3.connect(db)
        con.execute("VACUUM")
        con.close()
    files = {n: base64.b64encode((home / n).read_bytes()).decode() for n in FILES if (home / n).exists()}
    doc = {"version": 1, "saved_at": datetime.now(timezone.utc).isoformat(), "files": files}
    out.write_text(json.dumps(doc))
    return {n: len(base64.b64decode(v)) for n, v in files.items()}


def import_(src: Path, home: Path) -> dict:
    doc = json.loads(src.read_text())
    if doc.get("version") != 1:
        raise ValueError(f"unknown state version {doc.get('version')}")
    home.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for name, b64 in doc["files"].items():
        if name not in FILES:
            continue   # never write anything but the known state files
        data = base64.b64decode(b64)
        (home / name).write_bytes(data)
        sizes[name] = len(data)
    return sizes


def main(argv: list[str]) -> int:
    from .config import Settings
    if len(argv) != 2 or argv[0] not in ("export", "import"):
        print("usage: python -m betscan state export|import FILE")
        return 2
    home = Settings().cache_dir
    path = Path(argv[1])
    sizes = export(home, path) if argv[0] == "export" else import_(path, home)
    print(f"state {argv[0]}: " + ", ".join(f"{k} {v:,} bytes" for k, v in sizes.items()) + f" ({home})")
    return 0
