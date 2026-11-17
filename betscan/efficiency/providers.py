"""Per-sport efficiency providers, each with a score-based fallback.

  NFL    nflverse play-by-play -> opponent-adjusted EPA/play & success rate
  NCAAF  CollegeFootballData SP+ (needs free CFBD_API_KEY)
  NBA    NBA.com advanced team stats (ORtg / DRtg / pace)
  NCAAB  Bart Torvik adjusted efficiency (AdjO / AdjD / tempo)
  NHL    MoneyPuck expected goals (xGF/60, xGA/60)
  any    opponent-adjusted ratings fit from ESPN final scores
"""
from __future__ import annotations

import csv
import gzip
import io
import os
from collections import defaultdict
from datetime import datetime, timedelta

from ..config import Sport
from ..http import Fetcher
from .model import RatingSet, TeamRating, fit_scores, shrink

NFL_TEAMS = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LA": "Los Angeles Rams", "LAC": "Los Angeles Chargers",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}
NHL_TEAMS = {
    "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres", "CGY": "Calgary Flames",
    "CAR": "Carolina Hurricanes", "CHI": "Chicago Blackhawks", "COL": "Colorado Avalanche",
    "CBJ": "Columbus Blue Jackets", "DAL": "Dallas Stars", "DET": "Detroit Red Wings",
    "EDM": "Edmonton Oilers", "FLA": "Florida Panthers", "L.A": "Los Angeles Kings",
    "LAK": "Los Angeles Kings", "MIN": "Minnesota Wild", "MTL": "Montréal Canadiens",
    "NSH": "Nashville Predators", "N.J": "New Jersey Devils", "NJD": "New Jersey Devils",
    "NYI": "New York Islanders", "NYR": "New York Rangers", "OTT": "Ottawa Senators",
    "PHI": "Philadelphia Flyers", "PIT": "Pittsburgh Penguins", "S.J": "San Jose Sharks",
    "SJS": "San Jose Sharks", "SEA": "Seattle Kraken", "STL": "St Louis Blues",
    "T.B": "Tampa Bay Lightning", "TBL": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs",
    "UTA": "Utah Mammoth", "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights",
    "WSH": "Washington Capitals", "WPG": "Winnipeg Jets",
}


def season_year(sport: str, now: datetime) -> int:
    """Season label each source uses. NFL/NHL/NBA/NCAAF: start year. NCAAB (Torvik): end year."""
    start = now.year if now.month >= 8 else now.year - 1
    return start + 1 if sport == "ncaab" else start


# --- NFL: EPA/play from nflverse -------------------------------------------

NFLVERSE_PBP = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz"


def nfl_epa_from_rows(rows, min_wp=0.1, max_wp=0.9, prior_plays=250, adjust_iters=4) -> RatingSet:
    """Aggregate play-by-play rows into opponent-adjusted EPA/play.

    Uses only pass/run plays, drops garbage time (win probability outside
    10-90%), regresses each team toward average by `prior_plays`, then
    adjusts each side of the ball for the opponents faced.
    """
    off = defaultdict(lambda: [0.0, 0, 0])   # epa sum, plays, successes
    dfn = defaultdict(lambda: [0.0, 0, 0])
    games_plays = defaultdict(lambda: defaultdict(int))   # team -> game -> offensive plays (all)
    opp_plays = defaultdict(lambda: defaultdict(int))     # team -> opponent -> plays
    pts_total, team_games = 0.0, set()
    for r in rows:
        if r.get("season_type") not in ("REG", "POST"):
            continue
        if r.get("play_type") not in ("pass", "run"):
            continue
        pos, de, epa = r.get("posteam"), r.get("defteam"), r.get("epa")
        if not pos or not de or epa in ("", "NA", None):
            continue
        games_plays[pos][r["game_id"]] += 1
        wp = r.get("wp")
        if wp not in ("", "NA", None) and not (min_wp <= float(wp) <= max_wp):
            continue
        e, s = float(epa), 1 if r.get("success") in ("1", "1.0") else 0
        off[pos][0] += e; off[pos][1] += 1; off[pos][2] += s
        dfn[de][0] += e; dfn[de][1] += 1; dfn[de][2] += s
        opp_plays[pos][de] += 1
    if not off:
        raise ValueError("no plays parsed")
    tot_e = sum(v[0] for v in off.values()); tot_n = sum(v[1] for v in off.values())
    lg = tot_e / tot_n
    raw_o = {t: shrink(v[0] / v[1], lg, v[1], prior_plays) for t, v in off.items()}
    raw_d = {t: shrink(v[0] / v[1], lg, v[1], prior_plays) for t, v in dfn.items()}
    adj_o, adj_d = dict(raw_o), dict(raw_d)
    for _ in range(adjust_iters):
        new_o, new_d = {}, {}
        for t in raw_o:
            faced = opp_plays[t]; n = sum(faced.values()) or 1
            # Facing good defenses (low EPA allowed) deflates raw offense; add it back.
            new_o[t] = raw_o[t] - sum((adj_d.get(o, lg) - lg) * k for o, k in faced.items()) / n
        for t in raw_d:
            faced = {o: opp_plays[o][t] for o in raw_o if opp_plays[o][t]}
            n = sum(faced.values()) or 1
            new_d[t] = raw_d[t] - sum((adj_o.get(o, lg) - lg) * k for o, k in faced.items()) / n
        adj_o, adj_d = new_o, new_d
    pace = {t: sum(g.values()) / len(g) for t, g in games_plays.items()}
    lg_pace = sum(pace.values()) / len(pace)
    teams = {}
    for abbr in raw_o:
        name = NFL_TEAMS.get(abbr, abbr)
        o, d = off[abbr], dfn[abbr]
        teams[name] = TeamRating(name, adj_o[abbr], adj_d.get(abbr, lg), pace.get(abbr, lg_pace),
                                 games=len(games_plays[abbr]), extra={
                                     "off_epa": round(adj_o[abbr], 3), "def_epa": round(adj_d.get(abbr, lg), 3),
                                     "off_sr": round(o[2] / o[1], 3), "def_sr": round(d[2] / max(1, d[1]), 3)})
    # Points per team-game at league-average EPA. 22.75 removes the -1.5 pt total
    # bias the 2023-26 walk-forward backtest found with 22.0.
    return RatingSet("nfl", "nflverse EPA/play (opp-adjusted)", "per_play", teams, league_off=lg,
                     league_pace=lg_pace, league_pts=22.75, unit="EPA/play")


def nfl_epa(fetcher: Fetcher, now: datetime) -> RatingSet:
    raw = fetcher.get_bytes(NFLVERSE_PBP.format(season=season_year("nfl", now)), ttl=6 * 3600, timeout=120)
    rows = csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(raw)), encoding="utf-8"))
    return nfl_epa_from_rows(rows)


# --- NCAAF: SP+ ----------------------------------------------------------------

def ncaaf_sp(fetcher: Fetcher, now: datetime) -> RatingSet:
    key = os.environ.get("CFBD_API_KEY")
    if not key:
        raise RuntimeError("CFBD_API_KEY not set (free: collegefootballdata.com)")
    data = fetcher.get_json("https://api.collegefootballdata.com/ratings/sp",
                            {"year": season_year("ncaaf", now)}, ttl=12 * 3600,
                            headers={"Authorization": f"Bearer {key}"})
    return parse_sp(data)


def parse_sp(data: list[dict]) -> RatingSet:
    teams = {}
    for r in data:
        o, d = (r.get("offense") or {}).get("rating"), (r.get("defense") or {}).get("rating")
        if r.get("team") in (None, "nationalAverages") or o is None or d is None:
            continue
        teams[r["team"]] = TeamRating(r["team"], float(o), float(d),
                                      extra={"sp_overall": r.get("rating")})
    if not teams:
        raise ValueError("no SP+ rows")
    lg = sum(t.off for t in teams.values()) / len(teams)
    return RatingSet("ncaaf", "SP+ (CollegeFootballData)", "points", teams, league_off=lg, unit="SP+ pts")


# --- NBA: NBA.com advanced -------------------------------------------------

NBA_HEADERS = {"Referer": "https://www.nba.com/", "Origin": "https://www.nba.com",
               "Accept": "application/json", "x-nba-stats-origin": "stats", "x-nba-stats-token": "true",
               "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def nba_advanced(fetcher: Fetcher, now: datetime) -> RatingSet:
    y = season_year("nba", now)
    data = fetcher.get_json("https://stats.nba.com/stats/leaguedashteamstats", {
        "MeasureType": "Advanced", "PerMode": "PerGame", "Season": f"{y}-{str(y + 1)[2:]}",
        "SeasonType": "Regular Season", "LeagueID": "00", "LastNGames": 0, "Month": 0,
        "OpponentTeamID": 0, "PaceAdjust": "N", "Period": 0, "PlusMinus": "N", "Rank": "N",
        "DateFrom": "", "DateTo": "", "GameSegment": "", "Location": "", "Outcome": "",
        "PORound": 0, "SeasonSegment": "", "ShotClockRange": "", "VsConference": "", "VsDivision": "",
        "Conference": "", "Division": "", "GameScope": "", "PlayerExperience": "",
        "PlayerPosition": "", "StarterBench": "", "TeamID": 0, "TwoWay": 0,
    }, ttl=6 * 3600, headers=NBA_HEADERS)
    return parse_nba(data)


def parse_nba(data: dict, prior_games: float = 8) -> RatingSet:
    rs = data["resultSets"][0]
    idx = {h: i for i, h in enumerate(rs["headers"])}
    rows = rs["rowSet"]
    lg_o = sum(r[idx["OFF_RATING"]] for r in rows) / len(rows)
    lg_p = sum(r[idx["PACE"]] for r in rows) / len(rows)
    teams = {}
    for r in rows:
        gp = r[idx["GP"]]
        o = shrink(r[idx["OFF_RATING"]], lg_o, gp, prior_games)
        d = shrink(r[idx["DEF_RATING"]], lg_o, gp, prior_games)
        name = r[idx["TEAM_NAME"]]
        teams[name] = TeamRating(name, o, d, r[idx["PACE"]], gp, extra={
            "ortg": r[idx["OFF_RATING"]], "drtg": r[idx["DEF_RATING"]], "net": r[idx["NET_RATING"]],
            "pace": r[idx["PACE"]]})
    return RatingSet("nba", "NBA.com ORtg/DRtg (regressed)", "per_poss", teams, league_off=lg_o,
                     league_pace=lg_p, unit="pts/100")


# --- NCAAB: Torvik -------------------------------------------------------------

def ncaab_torvik(fetcher: Fetcher, now: datetime) -> RatingSet:
    raw = fetcher.get_bytes(f"https://barttorvik.com/{season_year('ncaab', now)}_team_results.csv",
                            ttl=6 * 3600)
    return parse_torvik(raw.decode("utf-8", "replace"))


def parse_torvik(text: str) -> RatingSet:
    reader = csv.reader(io.StringIO(text))
    header = [h.strip().lower() for h in next(reader)]
    col = {h: i for i, h in enumerate(header)}
    need = ("team", "adjoe", "adjde", "adjt")
    if not all(k in col for k in need):
        raise ValueError(f"unexpected Torvik header: {header[:12]}")
    teams = {}
    for r in reader:
        if len(r) <= max(col[k] for k in need):
            continue
        try:
            o, d, t = float(r[col["adjoe"]]), float(r[col["adjde"]]), float(r[col["adjt"]])
        except ValueError:
            continue
        name = r[col["team"]].strip()
        teams[name] = TeamRating(name, o, d, t, extra={"adjoe": o, "adjde": d, "tempo": t,
                                                     "barthag": r[col["barthag"]] if "barthag" in col else None})
    lg = sum(x.off for x in teams.values()) / len(teams)
    lp = sum(x.pace for x in teams.values()) / len(teams)
    return RatingSet("ncaab", "Torvik AdjO/AdjD", "per_poss", teams, league_off=lg, league_pace=lp,
                     unit="pts/100")


# --- NHL: MoneyPuck xG -----------------------------------------------------------

def nhl_moneypuck(fetcher: Fetcher, now: datetime) -> RatingSet:
    url = (f"https://moneypuck.com/moneypuck/playerData/seasonSummary/"
           f"{season_year('nhl', now)}/regular/teams.csv")
    return parse_moneypuck(fetcher.get_bytes(url, ttl=6 * 3600).decode("utf-8", "replace"))


def parse_moneypuck(text: str, prior_games: float = 10) -> RatingSet:
    rows = [r for r in csv.DictReader(io.StringIO(text)) if r.get("situation") == "all"]
    if not rows:
        raise ValueError("no 'all' situation rows")
    per = {}
    for r in rows:
        ice = float(r["iceTime"]) / 3600
        per[r["team"]] = (float(r["xGoalsFor"]) / ice, float(r["xGoalsAgainst"]) / ice,
                          int(float(r["games_played"])), r)
    lg = sum(v[0] for v in per.values()) / len(per)
    teams = {}
    for abbr, (xf, xa, gp, r) in per.items():
        name = NHL_TEAMS.get(abbr, abbr)
        teams[name] = TeamRating(name, shrink(xf, lg, gp, prior_games), shrink(xa, lg, gp, prior_games),
                                 games=gp, extra={"xgf60": round(xf, 2), "xga60": round(xa, 2),
                                                  "xg_pct": r.get("xGoalsPercentage")})
    return RatingSet("nhl", "MoneyPuck xG/60 (regressed)", "per60", teams, league_off=lg, unit="xG/60")


# --- fallback: scores ------------------------------------------------------------

def scores_from_espn(espn, sport: Sport, now: datetime, days: int = 120) -> RatingSet:
    start = max(datetime(season_year(sport.code, now) - (1 if sport.code == "ncaab" else 0), 8, 1,
                         tzinfo=now.tzinfo), now - timedelta(days=days))
    games = [(e["home"]["name"], e["away"]["name"], e["home"]["score"], e["away"]["score"], e["neutral"])
             for e in espn.results(sport, start, now)
             if e["home"]["score"] is not None and e["away"]["score"] is not None]
    if len(games) < 20:
        raise ValueError(f"only {len(games)} completed games")
    rs = fit_scores(games, sport.code)
    rs.source = "score-adjusted ratings (ESPN results)"
    return rs


def nfl_scores_nflverse(fetcher: Fetcher, now: datetime) -> RatingSet:
    raw = fetcher.get_bytes("https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv",
                            ttl=6 * 3600, timeout=60)
    season = str(season_year("nfl", now))
    games = []
    for r in csv.DictReader(io.StringIO(raw.decode())):
        if r["season"] == season and r["home_score"] not in ("", "NA"):
            games.append((NFL_TEAMS.get(r["home_team"], r["home_team"]),
                          NFL_TEAMS.get(r["away_team"], r["away_team"]),
                          float(r["home_score"]), float(r["away_score"]), r["location"] == "Neutral"))
    rs = fit_scores(games, "nfl")
    rs.source = "score-adjusted ratings (nflverse results)"
    return rs


PRIMARY = {"nfl": [nfl_epa, nfl_scores_nflverse], "ncaaf": [ncaaf_sp], "nba": [nba_advanced],
           "ncaab": [ncaab_torvik], "nhl": [nhl_moneypuck], "mlb": []}


def get_ratings(sport: Sport, fetcher: Fetcher, espn, now: datetime, log=print) -> RatingSet | None:
    for fn in PRIMARY.get(sport.code, []):
        try:
            rs = fn(fetcher, now)
            log(f"  [efficiency] {sport.code}: {rs.source}, {len(rs.teams)} teams")
            return rs
        except Exception as e:  # noqa: BLE001
            log(f"  [efficiency] {sport.code}: {fn.__name__} unavailable ({e}); trying fallback")
    try:
        rs = scores_from_espn(espn, sport, now)
        log(f"  [efficiency] {sport.code}: {rs.source}, {len(rs.teams)} teams")
        return rs
    except Exception as e:  # noqa: BLE001
        log(f"  [efficiency] {sport.code}: no ratings available ({e})")
        return None
