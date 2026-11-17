"""Sportsbook Review Online college basketball archive (2007-08 .. 2020-21).

One xlsx per season: two rows per game (visitor then home, or N/N for neutral
sites). Each row's Open/Close cell holds either the spread (for the favorite's
row) or the total (for the other row); totals are the values above ~90.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from ..http import Fetcher

URL = ("https://www.sportsbookreviewsonline.com/wp-content/uploads/"
       "sportsbookreviewsonline_com_737/ncaa-basketball-{season}.xlsx")
SEASONS = [f"{y}-{str(y + 1)[2:]}" for y in range(2007, 2021)]
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def read_xlsx(data: bytes) -> list[list]:
    """Minimal stdlib reader for the first worksheet (values only)."""
    import io
    z = zipfile.ZipFile(io.BytesIO(data))
    strings = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            strings.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
    rows = []
    for r in ET.fromstring(z.read(sheet)).iter(f"{{{NS['m']}}}row"):
        out: dict[int, object] = {}
        for c in r.findall("m:c", NS):
            col = 0
            for ch in re.match(r"[A-Z]+", c.get("r")).group():
                col = col * 26 + ord(ch) - 64
            v = c.find("m:v", NS)
            if c.get("t") == "inlineStr":
                val = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
            elif v is None:
                val = None
            elif c.get("t") == "s":
                val = strings[int(v.text)]
            else:
                val = float(v.text)
                if val.is_integer():
                    val = int(val)
            out[col - 1] = val
        rows.append([out.get(i) for i in range(max(out) + 1)] if out else [])
    return rows


@dataclass
class ArchiveGame:
    season: str          # "2015-16"
    day: date
    home: str            # for neutral sites, the second-listed team
    away: str
    neutral: bool
    home_score: int
    away_score: int
    open_spread: float | None    # points the HOME team is favored by (negative = home dog)
    close_spread: float | None
    open_total: float | None
    close_total: float | None
    rot: int | None = None

    @property
    def margin(self) -> int:
        return self.home_score - self.away_score

    @property
    def total(self) -> int:
        return self.home_score + self.away_score


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower()
    if s in ("pk", "pick", "p"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return None   # "NL" (no line), blanks


def _split(a, b):
    """(spread_for_a_as_favorite, total) from a pair of Open or Close cells."""
    a, b = _num(a), _num(b)
    if a is None or b is None:
        return None, None
    if a > 90 >= b:          # b is the spread: team b is favored by b
        return -b, a
    if b > 90 >= a:          # a is the spread: team a is favored by a
        return a, b
    return None, None


def parse_season(rows: list[list], season: str) -> list[ArchiveGame]:
    head = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
    ix = {h: i for i, h in enumerate(head)}
    start = int(season[:4])
    games = []
    body = [r for r in rows[1:] if r and len(r) > ix["close"] and r[ix["team"]]]
    i = 0
    while i + 1 < len(body):
        v, h = body[i], body[i + 1]
        if not ({v[ix["vh"]], h[ix["vh"]]} <= {"V", "H", "N"}) or (v[ix["vh"]] == "H"):
            i += 1
            continue
        try:
            md = int(v[ix["date"]])
            month, dd = divmod(md, 100)
            day = date(start if month >= 8 else start + 1, month, dd)
            hs, as_ = int(h[ix["final"]]), int(v[ix["final"]])
        except (TypeError, ValueError):
            i += 2
            continue
        # _split gives "points the first team (visitor) is favored by"; flip to home.
        o_sp, o_tot = _split(v[ix["open"]], h[ix["open"]])
        c_sp, c_tot = _split(v[ix["close"]], h[ix["close"]])
        games.append(ArchiveGame(
            season, day, str(h[ix["team"]]), str(v[ix["team"]]), v[ix["vh"]] == "N", hs, as_,
            None if o_sp is None else -o_sp, None if c_sp is None else -c_sp, o_tot, c_tot,
            int(v[ix["rot"]]) if isinstance(v[ix["rot"]], int) else None))
        i += 2
    return games


def load(fetcher: Fetcher, seasons=SEASONS) -> list[ArchiveGame]:
    out = []
    for s in seasons:
        raw = fetcher.get_bytes(URL.format(season=s), ttl=30 * 86400, timeout=120,
                                headers={"User-Agent": "Mozilla/5.0"})
        out += parse_season(read_xlsx(raw), s)
    return out
