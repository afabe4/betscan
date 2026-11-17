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
| **wind** (NFL) | Open-Meteo forecast at kickoff + nflverse history | Outdoor games forecast at 10+ mph. The Under's fair probability is raised by the historical under rate in that wind (54.8% at 10–14 mph, 56.4% at 15+ vs closing totals, 2006–25), discounted for forecast uncertainty (85% of the edge kept inside 24h, down to 40% at 4+ days). |
| **teasers** (NFL) | nflverse history | 6-pt teaser legs on dogs of +1.5 to +2.5 in games totaled ≤48.5 (78.3% leg rate, 2006–25). Builds every +EV 2- and 3-team combo at the same book. |
| **rest** | ESPN team schedules | NBA/NHL back-to-backs, NCAAF rest gaps. **Off for NFL**: rest edges covered 51.2%/50.1% (2006–17 / 2018–25), i.e. already priced. |
| **news** | ESPN news | Recent headlines with negative keywords (suspended, ruled out, illness, fired...) about one team. |
| **model** | per-sport efficiency (below) | Projected score → win/cover/over probability, compared with the market's fair price. Context only by default (see backtest). |
| **movement** | Local SQLite snapshots | If the line already moved toward a signal since we first saw the game, that signal is cut 70% and marked "priced". |

## College basketball (NCAAB)

```bash
python -m betscan scan --sports ncaab --html report.html
python -m betscan ncaab-backtest          # reproduce the numbers below
```

**The play: totals, early, mid-season onward.** Opponent-adjusted ratings are built from every D1
result this season (ESPN), seeded with last season's ratings shrunk 50%, blowouts capped at 20 points,
recent games weighted more (60-day half-life). When the model's total is 3+ points off the line
**and both teams have played 10+ games**, the side the model likes is re-priced at its backtested win
rate and becomes a BET at DraftKings/FanDuel if the price allows.

Walk-forward backtest on the Sportsbook Review archive (57,113 games, 2007-08 to 2020-21, ratings
only use games before each date; settings picked on 2008-14, then checked on 2015-21):

| Totals, model 3+ pts off, both teams 10+ games | 2008-14 | 2015-21 |
|---|---|---|
| vs **opening** total | 55.0% (2,315) | 56.1% (4,454) |
| ...0.5 pt worse than opener | 53.5% | 55.0% |
| ...1.0 pt worse than opener | 52.4% | 53.8% |
| vs **closing** total | 51.0% | 52.0% |
| vs opener in **November** (no games filter) | 57.7% | **48.3%** |

Every season 2008-09 to 2020-21 cleared 52.4% (worst 52.7%). Three things follow:

- **Timing is the edge.** It's large at the opener and gone by the close: the market moves toward the
  model's number by tip-off. Scan right after lines post and bet promptly; a pick flagged "the total has
  already moved toward this side" is mostly priced.
- **Nothing before ~mid-December.** Until teams have 10 games the model leans on last season, and in the
  transfer-portal era that lost money (48.3% in November 2015-21). Those games show as context only.
- **The data ends in 2021.** Markets may be sharper now. `review` measures your CLV live: if your bets
  stop beating the closing total, the edge has closed and you should stop.

Spreads: the same model was 53.5% / 55.8% vs the opener in Jan-Mar but swung wildly by season (one season
at 65.9%), so it's not used for bets. Torvik's AdjO/AdjD/tempo are shown as context.

College scans use US books only (2 credits: spreads + totals x 1 region); the totals model supplies the
fair price, so Pinnacle isn't needed. Lines for the next day typically post in the evening; four scans a
day is ~240 credits/month:

```cron
CRON_TZ=America/New_York
0 7,11,16,21 * * *   cd ~/betscan && python3 -m betscan scan --sports ncaab >> ~/.betscan/ncaab.log 2>&1
30 6 * * *           cd ~/betscan && python3 -m betscan review >> ~/.betscan/review.log 2>&1
```

With the NFL schedule too, that's ~395 of the 500 free credits.

Price-only picks are BETs only against a sharp reference line. A book 2+ points off the reference spread
(3+ on totals) is flagged VERIFY: that's usually a stale line or a feed error, not an edge.

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

## Run it on a schedule: opening lines, closing lines, and review

Every scan stores every book's line. The first scan of a game is its **opening line**. The last
scan before kickoff is its **closing line**. Every pick (BET, LEAN, WATCH, teaser legs, plus
context-only model sides logged as `MODEL`) is saved with the price at the time.
`python -m betscan review` then grades each pick:

- **CLV**: your price vs the no-vig closing price at your number. Beating the close consistently
  is the earliest reliable sign a signal works; it shows within ~100 bets, while W-L needs 1000+.
- **Result** from nflverse final scores, with flat 1-unit ROI.
- Broken out by tier and by signal (`signal wind`, `signal model`, `signal injury`, `tier TEASER`...).

A pick that upgrades (WATCH → BET) is re-logged at the price when it upgraded; downgrades don't
overwrite it.

A scan costs 4 Odds API credits (spreads + totals × US + EU regions; moneylines are off by
default, add them with `--markets h2h,spreads,totals` for 6). This schedule is 9 scans a week
(~155 credits/month, inside the free 500):

```cron
CRON_TZ=America/New_York
0 12 * * 2    cd ~/betscan && python3 -m betscan review >> ~/.betscan/review.log 2>&1
5 12 * * 2    cd ~/betscan && python3 -m betscan scan >> ~/.betscan/scan.log 2>&1   # openers
0 12 * * 4    cd ~/betscan && python3 -m betscan scan >> ~/.betscan/scan.log 2>&1
50 19 * * 4   cd ~/betscan && python3 -m betscan scan >> ~/.betscan/scan.log 2>&1   # TNF close
0 12 * * 6    cd ~/betscan && python3 -m betscan scan >> ~/.betscan/scan.log 2>&1   # forecasts firm up
0 9 * * 0     cd ~/betscan && python3 -m betscan scan --html ~/betscan/report.html >> ~/.betscan/scan.log 2>&1
40 12 * * 0   cd ~/betscan && python3 -m betscan scan >> ~/.betscan/scan.log 2>&1   # 1pm close
55 15 * * 0   cd ~/betscan && python3 -m betscan scan >> ~/.betscan/scan.log 2>&1   # 4pm close
5 20 * * 0    cd ~/betscan && python3 -m betscan scan >> ~/.betscan/scan.log 2>&1   # SNF close
55 19 * * 1   cd ~/betscan && python3 -m betscan scan >> ~/.betscan/scan.log 2>&1   # MNF close
```

Data lives in `~/.betscan/snapshots.sqlite` (override with `BETSCAN_HOME`).

## Books

Picks and teasers only use DraftKings and FanDuel (`MY_BOOKS` in `config.py`). Every other
book, Pinnacle especially, is used only to work out the fair price. Override per run with
`--books draftkings,fanduel,betmgm`, `--books us` (all US-regulated) or `--books all`.

## Teasers

The Odds API doesn't carry teaser prices, so give yours: `--teaser-price -120 --teaser3-price 160`.
Break-even per leg is 73.9% at -120 but 75.2% at -130 and 76.5% at -140, so the price matters:
at -140 the historical 78.3% leaves little margin. Some books stopped offering teasers through
these numbers, or price them worse, for exactly this reason. `--teaser-favs` adds -7.5/-8.5
favorites (73.0% overall, but 71.1% in 2018–25: below break-even). The combo EV assumes legs in
different games are independent.

`python -m betscan angles` prints the current historical rates.

## Caveats

- The only edge that holds up over time is **price**: beating the sharp line, and beating the closing line.
  Situational signals are heuristics with hand-set weights. They haven't been backtested; treat them as
  pointers for your own research.
- Free Odds API tier = 500 credits/month. One scan costs (markets × regions) credits per sport, 4 at defaults.
- ESPN endpoints are unofficial and may change; parsers fail soft (the signal is skipped, the scan goes on).
- Check that sports betting is legal where you are. Bet only what you can afford to lose.
