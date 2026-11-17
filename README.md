# betscan

Scans upcoming betting lines and flags games where price, injuries, weather,
rest/schedule, or breaking news point to a possible mispricing. Python 3.10+,
standard library only.

```bash
python -m betscan scan --demo --all --html report.html        # offline demo
export ODDS_API_KEY=...                                       # https://the-odds-api.com
python -m betscan scan --sports nfl,nba --html report.html    # live
```

## What it looks for

| Signal | Source | What it flags |
|---|---|---|
| **price** | The Odds API (US + EU regions, which includes Pinnacle) | A book whose price beats the sharp book's no-vig fair probability (or the consensus if no sharp book). Different numbers are adjusted point by point, with NFL key numbers 3/7/10 weighted; those results are marked `approx`. |
| **injury** | ESPN injuries | Key-position players (QB, SP, goalie, guards...) ruled Out/Doubtful in the last 48h. Older injuries are assumed priced in. |
| **weather** | Open-Meteo at kickoff hour | Outdoor football with wind ≥15 mph, heavy precipitation, or ≤20°F → Under, small boost to the underdog. Cold MLB → Under. |
| **rest** | ESPN team schedules | NBA/NHL back-to-back vs rested opponent; NFL/NCAAF rest gaps of 3+ days. |
| **news** | ESPN news | Recent headlines with negative keywords (suspended, ruled out, illness, fired...) about one team. |
| **model** | per-sport efficiency (below) | Projected score → win/cover/over probability, compared with the market's fair price. Context only by default (see backtest). |
| **movement** | Local SQLite snapshots | If the line already moved toward a signal since we first saw the game, that signal is cut 70% and marked "priced". |

## Efficiency models

| Sport | Source | Metrics | Key needed |
|---|---|---|---|
| NFL | nflverse play-by-play | Opponent-adjusted EPA/play off & def, success rate, plays/game. Garbage time (win prob outside 10–90%) dropped, small samples regressed. | no |
| NCAAF | CollegeFootballData | SP+ offense/defense | `CFBD_API_KEY` (free) |
| NBA | NBA.com advanced | ORtg, DRtg, pace (regressed by games played) | no |
| NCAAB | Bart Torvik | AdjO, AdjD, adjusted tempo | no |
| NHL | MoneyPuck | xGF/60, xGA/60 (Poisson scoring model) | no |
| any | ESPN final scores | Opponent-adjusted points for/against (ridge fit); automatic fallback | no |

```bash
python -m betscan ratings nfl            # print the current table
python -m betscan backtest --seasons 2023-2026
```

**Backtest (NFL, walk-forward, 774 games, weeks 4+ of 2023–2026, vs closing lines):**

| | Model MAE | Closing line MAE | Win rate when model disagrees by ≥2 pts |
|---|---|---|---|
| Spreads | 10.45 | 9.60 | 47.4% (222–246) |
| Totals | 10.77 | 10.01 | 51.3% (265–252) |

Break-even at -110 is 52.4%. Public efficiency metrics are already priced into NFL closing lines, and
changing the regression strength or adding prior-season data didn't change that. So the model counts
for **0** in scoring by default. It's shown on each pick as context (projection, team ranks, model vs
market probability). Use `--model-weight 0.5` (or similar) to let it move scores, but only after testing
it where you think it can win (opening lines, small-conference college games). The other sports' models
haven't been backtested.

## Tiers

- **BET**: price edge ≥ `--min-ev` (default 1.5%) with no strong situation against it. Stake = quarter Kelly, capped at 3% of bankroll.
- **VERIFY**: EV > 12%. Usually a stale or mismatched line; confirm it's real before you do anything.
- **ALT**: same team as a BET in another market (spread vs ML). Take one.
- **LEAN**: situational score ≥ 2 with a roughly fair price. No stake suggested.
- **WATCH** (`--all`): a situation worth a look, but the price has no edge.

## Run it on a schedule

The movement check only works once there's line history. Run every 30–60 minutes (cron) so the
tool can tell "news broke and the line hasn't moved" (the real opportunity) apart from "news broke
and the market already adjusted." Snapshots and logged picks are stored in `~/.betscan/snapshots.sqlite`
(override with `BETSCAN_HOME`).

## Caveats

- The only edge that holds up over time is **price**: beating the sharp line, and beating the closing line.
  Situational signals are heuristics with hand-set weights. They haven't been backtested; treat them as
  pointers for your own research.
- Free Odds API tier = 500 credits/month. One scan costs (markets × regions) credits per sport, 6 at defaults.
- ESPN endpoints are unofficial and may change; parsers fail soft (the signal is skipped, the scan goes on).
- Check that sports betting is legal where you are. Bet only what you can afford to lose.
