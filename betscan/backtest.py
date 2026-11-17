"""Walk-forward backtest of the NFL EPA model against closing lines.

For each week W, ratings are built only from plays in weeks < W of that
season, then compared with nflverse's closing spread/total and the result.
No look-ahead.

  python -m betscan backtest --seasons 2023-2026
"""
from __future__ import annotations

import csv
import gzip
import io
from collections import defaultdict
from datetime import datetime, timezone

from .config import Settings
from .efficiency.model import project
from .efficiency.providers import NFL_TEAMS, NFLVERSE_PBP, nfl_epa_from_rows
from .http import Fetcher

KEEP = ("game_id", "season_type", "week", "posteam", "defteam", "play_type", "epa", "success", "wp")


def _load_pbp(fetcher: Fetcher, season: int) -> list[dict]:
    raw = fetcher.get_bytes(NFLVERSE_PBP.format(season=season), ttl=7 * 86400, timeout=180)
    reader = csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8"))
    return [{k: r[k] for k in KEEP} for r in reader]


def _load_games(fetcher: Fetcher) -> list[dict]:
    raw = fetcher.get_bytes("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv",
                            ttl=86400, timeout=60)
    return list(csv.DictReader(io.StringIO(raw.decode())))


def run(seasons: list[int], min_week: int = 4, fetcher: Fetcher | None = None) -> dict:
    fetcher = fetcher or Fetcher(Settings().cache_dir)
    games = _load_games(fetcher)
    rows_out = []
    for season in seasons:
        pbp = _load_pbp(fetcher, season)
        by_week = defaultdict(list)
        for r in pbp:
            if r["week"]:
                by_week[int(r["week"])].append(r)
        sched = [g for g in games if g["season"] == str(season) and g["game_type"] == "REG"
                 and g["home_score"] not in ("", "NA") and g["spread_line"] not in ("", "NA")]
        for week in sorted({int(g["week"]) for g in sched}):
            if week < min_week:
                continue
            prior = [r for w, rs in by_week.items() if w < week for r in rs]
            rs = nfl_epa_from_rows(prior)
            for g in (x for x in sched if int(x["week"]) == week):
                h = rs.find(NFL_TEAMS[g["home_team"]]); a = rs.find(NFL_TEAMS[g["away_team"]])
                if not h or not a:
                    continue
                hp, ap = project(rs, h, a, neutral=g["location"] == "Neutral")
                rows_out.append({
                    "season": season, "week": week, "game": g["game_id"],
                    "model_margin": hp - ap, "model_total": hp + ap,
                    "spread": float(g["spread_line"]), "total_line": float(g["total_line"]),
                    "result": float(g["result"]), "total": float(g["total"]),
                })
    return summarize(rows_out)


def _ats(rows, key_model, key_line, key_result, thresholds=(1, 2, 3, 4, 5)):
    out = {}
    for k in thresholds:
        w = l = 0
        for r in rows:
            edge = r[key_model] - r[key_line]
            if abs(edge) < k:
                continue
            diff = r[key_result] - r[key_line]
            if diff == 0:
                continue
            if (diff > 0) == (edge > 0):
                w += 1
            else:
                l += 1
        out[k] = (w, l)
    return out


def _best_weight(rows, key_model, key_line, key_result):
    """Weight w minimizing squared error of line + w*(model - line)."""
    num = sum((r[key_result] - r[key_line]) * (r[key_model] - r[key_line]) for r in rows)
    den = sum((r[key_model] - r[key_line]) ** 2 for r in rows) or 1
    return num / den


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    mae = lambda k, t: sum(abs(r[k] - r[t]) for r in rows) / n  # noqa: E731
    return {
        "games": n,
        "spread": {"mae_model": mae("model_margin", "result"), "mae_market": mae("spread", "result"),
                   "best_weight": _best_weight(rows, "model_margin", "spread", "result"),
                   "ats_by_edge": _ats(rows, "model_margin", "spread", "result")},
        "total": {"mae_model": mae("model_total", "total"), "mae_market": mae("total_line", "total"),
                  "best_weight": _best_weight(rows, "model_total", "total_line", "total"),
                  "ats_by_edge": _ats(rows, "model_total", "total_line", "total"),
                  "mean_bias": sum(r["model_total"] - r["total"] for r in rows) / n},
    }


def print_report(res: dict) -> None:
    print(f"Walk-forward NFL EPA model, {res['games']} games (weeks 4+)\n")
    for mk, label in (("spread", "Spreads"), ("total", "Totals")):
        r = res[mk]
        print(f"{label}: model MAE {r['mae_model']:.2f} vs closing line {r['mae_market']:.2f}; "
              f"optimal blend weight on model {r['best_weight']:.2f}")
        if "mean_bias" in r:
            print(f"  model total bias {r['mean_bias']:+.2f} pts")
        for k, (w, l) in r["ats_by_edge"].items():
            pct = w / (w + l) if w + l else 0
            print(f"  model disagrees by >= {k} pts: {w}-{l}  ({pct:.1%}, need 52.4% at -110)")
        print()


def main(seasons_arg: str) -> int:
    if "-" in seasons_arg:
        a, b = seasons_arg.split("-")
        seasons = list(range(int(a), int(b) + 1))
    else:
        seasons = [int(x) for x in seasons_arg.split(",")]
    print_report(run(seasons))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else str(datetime.now(timezone.utc).year)))
