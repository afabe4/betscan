"""The bet card: today's bets as betting slips, plus your graded record.

    python -m betscan card --out card.html        # from your latest scans + pick log
    python -m betscan card --demo --out card.html # sample with made-up data
"""
from __future__ import annotations

import html
import json
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Settings
from .store import Store

ET = ZoneInfo("America/New_York")
SPORT_LABEL = {"ncaab": "College hoops", "nfl": "NFL"}
KIND_LABEL = {"early": "Early totals", "ncaab_model": "Totals model", "wind": "Wind under", "price": "Price edge", "teaser": "Teaser legs"}
# What each play did in testing; shown so the record has something to be judged against.
REFERENCE = [
    ("Totals model, A", "College basketball, model 6+ pts off the opener, both teams 10+ games",
     "59.4% / 60.5%", "2008-14 / 2015-21, 1,111 bets; ~59% half a point late", "52.4%"),
    ("Totals model, B", "Same, 3-6 pts off; only near the opener",
     "54.3% / 55.1%", "2008-14 / 2015-21, 5,658 bets; ~53% half a point late", "52.4%"),
    ("Teaser legs", "NFL 6-pt, dogs +1.5 to +2.5, total 48.5 or lower", "78.3%", "2006-25, 563 legs",
     "73.9% at -120"),
    ("Wind under", "NFL outdoor, 15+ mph at kickoff", "56.4%", "2006-25 vs closing totals, 459 games", "52.4%"),
    ("Price edge", "Book price better than Pinnacle's no-vig line", "n/a", "Wins long run if the sharp line is right",
     "beat the close"),
]

e = html.escape
BOOK_NAMES = {"draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM", "williamhill_us": "Caesars",
              "espnbet": "ESPN BET", "betrivers": "BetRivers", "fanatics": "Fanatics", "pinnacle": "Pinnacle"}


def _book(b: str | None) -> str:
    return ", ".join(BOOK_NAMES.get(x.strip(), x.strip().title()) for x in (b or "").split(",") if x.strip())


def _fmt_price(p) -> str:
    if p is None:
        return "—"
    p = round(p)
    return f"+{p}" if p > 0 else f"−{abs(p)}"


def _when(iso: str) -> str:
    t = datetime.fromisoformat(iso).astimezone(ET)
    return t.strftime("%a %-I:%M %p ET")


def _kind_of_pick(p: dict) -> str:
    kinds = set((p.get("kinds") or "").split(","))
    if p.get("tier") == "EARLY" or "ncaab_early" in kinds:
        return "early"
    if p["market"] == "teaser" or "teaser" in kinds:
        return "teaser"
    if "ncaab_model" in kinds and p["market"] == "totals":
        return "ncaab_model"
    if "wind" in kinds and p["side"] == "Under":
        return "wind"
    return "price"


# --- data ---------------------------------------------------------------------------

def _demo_slates(now: datetime) -> list[dict]:
    tonight = now.astimezone(ET).replace(hour=19, minute=0, second=0, microsecond=0)
    if tonight < now:
        tonight += timedelta(days=1)
    t = lambda h: (tonight + timedelta(hours=h)).isoformat()  # noqa: E731
    bets = [
        {"sport": "ncaab", "tier": "BET", "kind": "ncaab_model", "game": "Wisconsin Badgers @ Purdue Boilermakers",
         "commence": t(0), "market": "totals", "selection": "Under 141.5", "side": "Under", "point": 141.5,
         "book": "fanduel", "price": -110, "fair_prob": 0.57, "ev": 0.088, "stake": 30, "limit_price": -129,
         "limit_point": 138.0, "grade": "A", "reasons": ["Model total 134.8, 6.7 pts below the line.",
                                           "Backtested win rate at this gap: 58.0%."]},
        {"sport": "ncaab", "tier": "BET", "kind": "ncaab_model", "game": "Baylor Bears @ Iowa State Cyclones",
         "commence": t(1), "market": "totals", "selection": "Over 137.5", "side": "Over", "point": 137.5,
         "book": "draftkings", "price": -108, "fair_prob": 0.537, "ev": 0.034, "stake": 13, "limit_price": -113,
         "limit_point": 138.5, "grade": "B", "reasons": ["Model total 141.6, 4.1 pts above the line.",
                                           "Backtested win rate at this gap: 53.7%.",
                                           "B play: only bet near the opener. If the total has moved half a point your way, pass."]},
        {"sport": "ncaab", "tier": "BET", "kind": "price", "game": "Creighton Bluejays @ Marquette Golden Eagles",
         "commence": t(1.5), "market": "spreads", "selection": "Creighton Bluejays +4.5", "side": "Creighton Bluejays",
         "point": 4.5, "book": "draftkings", "price": -105, "fair_prob": 0.53, "ev": 0.035, "stake": 9,
         "limit_price": -111, "limit_point": None,
         "reasons": ["DraftKings beats Pinnacle's no-vig line (fair 53.0%)."]},
    ]
    watch = [{"sport": "ncaab", "tier": "WATCH", "game": "Gonzaga Bulldogs @ Saint Mary's Gaels", "commence": t(3),
              "selection": "Under 139.5", "book": "fanduel", "price": -112, "ev": -0.01,
              "note": "Model total 137.1: 2.4 pts off, short of the 3-pt bar."},
             {"sport": "ncaab", "tier": "VERIFY", "game": "UCF Knights @ Cincinnati Bearcats", "commence": t(0.5),
              "selection": "UCF Knights +6.5", "book": "fanduel", "price": -110, "ev": 0.07,
              "note": "Line is far from the market. Check it's live before betting."}]
    return [{"sport": "ncaab", "scanned_at": (now - timedelta(minutes=40)).isoformat(), "credits_left": "472",
             "bets": bets, "teasers": [], "watch": watch}]


def _load_slates(st: Settings, demo: bool, now: datetime) -> list[dict]:
    if demo:
        return _demo_slates(now)
    base = st.cache_dir
    out = []
    for sport in ("ncaab", "nfl"):
        f = base / f"latest_{sport}.json"
        if f.exists():
            try:
                out.append(json.loads(f.read_text()))
            except ValueError:
                pass
    for s in out:   # drop anything that has already started
        s["bets"] = [b for b in s["bets"] if datetime.fromisoformat(b["commence"]) > now]
        s["watch"] = [w for w in s["watch"] if datetime.fromisoformat(w["commence"]) > now]
        s["teasers"] = [t for t in s["teasers"]
                        if all(datetime.fromisoformat(leg["commence"]) > now for leg in t["legs"])]
    return out


def _record(st: Settings, demo: bool, now: datetime) -> list[dict]:
    if demo:
        return _demo_record(now)
    if not st.db_path.exists():
        return []
    picks = Store(st.db_path).picks("tier IN ('BET', 'EARLY', 'TEASER') AND result IS NOT NULL")
    return sorted(picks, key=lambda p: p["commence"])


def _demo_record(now: datetime) -> list[dict]:
    rng = random.Random(7)
    plays = [("ncaab_model", 0.56, "ncaab", "totals", 0.012), ("price", 0.53, "ncaab", "spreads", 0.018),
             ("teaser", 0.78, "nfl", "teaser", 0.0), ("wind", 0.56, "nfl", "totals", 0.009)]
    teams = ["Purdue", "Creighton", "Gonzaga", "Iowa St", "Baylor", "Marquette", "Kentucky", "Texas Tech",
             "Arizona", "St. John's", "Wisconsin", "Clemson", "BYU", "Oregon", "Memphis", "Illinois"]
    out = []
    for i in range(64):
        kind, wr, sport, market, clv = rng.choices(plays, weights=(6, 3, 2, 1))[0]
        day = now - timedelta(days=44 - i * 0.68)
        r = rng.random()
        res = "P" if r > 0.985 else ("W" if r < wr else "L")
        price = None if kind == "teaser" else rng.choice((-110, -110, -108, -112, -105, 100))
        profit = None if price is None else {"W": (100 / abs(price) if price < 0 else price / 100), "L": -1.0,
                                             "P": 0.0}[res]
        a, b = rng.sample(teams, 2)
        sel = {"totals": f"{rng.choice(('Over', 'Under'))} {rng.randint(128, 158)}.5",
               "spreads": f"{a} {rng.choice((-1, 1)) * rng.choice((1.5, 3.5, 4.5, 6.5)):+g}",
               "teaser": f"{a} +2.5 -> +8.5"}[market]
        out.append({"commence": day.isoformat(), "sport": sport, "label": f"{b} @ {a}", "market": market,
                    "side": "", "selection": sel, "book": rng.choice(("draftkings", "fanduel")), "price": price,
                    "result": res, "profit": profit, "stake": 0 if kind == "teaser" else rng.choice((8, 10, 12, 15)),
                    "clv": (rng.gauss(clv, 0.025) if kind != "teaser" else rng.gauss(0.3, 0.9)),
                    "kinds": "teaser" if kind == "teaser" else kind})
    return out


def _stats(rows: list[dict]) -> dict:
    priced = [r for r in rows if r["price"] is not None]
    w = sum(r["result"] == "W" for r in priced)
    l = sum(r["result"] == "L" for r in priced)
    p = sum(r["result"] == "P" for r in priced)
    units = sum(r["profit"] or 0 for r in priced)
    dollars = sum((r["profit"] or 0) * (r.get("stake") or 0) for r in priced)
    clv = [r["clv"] for r in priced if r.get("clv") is not None]
    return {"w": w, "l": l, "p": p, "units": units, "dollars": dollars,
            "roi": units / len(priced) if priced else 0, "n": len(priced),
            "clv": sum(clv) / len(clv) if clv else None,
            "beat": sum(c > 0 for c in clv) / len(clv) if clv else None}


# --- pieces ---------------------------------------------------------------------------

def _slip(b: dict) -> str:
    sport = SPORT_LABEL.get(b["sport"], b["sport"].upper())
    kind = KIND_LABEL.get(b["kind"], "Bet")
    grade = b.get("grade")
    titles = {"A": "6+ pts off: ~60% in testing", "B": "3-6 pts off: ~54% in testing",
              "Early": "November only: ~53% in testing, close to break-even"}
    text = "Early · small stake" if grade == "Early" else f"{grade} play"
    badge = (f'<span class="grade grade-{e((grade or "").lower())}" title="{titles.get(grade, "")}">'
             f'{e(text)}</span>') if grade else ""
    limits = []
    if b.get("limit_point") is not None:
        word = "up to" if b["side"] == "Over" else "down to"   # the direction the line gets worse
        limits.append(f"Bet {word} <b>{b['limit_point']:g}</b>")
    if b.get("limit_price") is not None:
        limits.append(f"no worse than <b>{_fmt_price(b['limit_price'])}</b>")
    limit_line = (" · ".join(limits) if limits else "Only at this number") + "."
    why = "".join(f"<li>{e(r)}</li>" for r in b["reasons"])
    stake = f"${b['stake']:.0f}" if b.get("stake") else "—"
    return f"""
<article class="slip" aria-label="{e(b['selection'])}">
  <div class="slip-main">
    <div class="slip-meta"><span class="chip chip-{e(b['sport'])}">{e(sport)}</span><span class="kind">{e(kind)}</span>{badge}
      <time>{e(_when(b['commence']))}</time></div>
    <p class="game">{e(b['game'])}</p>
    <p class="pick">{e(b['selection'])}</p>
    <ul class="why">{why}</ul>
    <p class="limit">{limit_line}</p>
  </div>
  <div class="slip-stub">
    <span class="stub-price">{_fmt_price(b['price'])}</span>
    <span class="stub-book">{e(_book(b['book']))}</span>
    <dl class="stub-nums"><div><dt>Edge</dt><dd>{(b['ev'] or 0) * 100:+.1f}%</dd></div>
      <div><dt>Stake</dt><dd>{stake}</dd></div></dl>
  </div>
</article>"""


def _teaser(t: dict) -> str:
    legs = "".join(f"<li><span>{e(leg['side'])} <b>{leg['teased']:+g}</b></span>"
                   f"<small>{e(leg['game'])} · was {leg['point']:+g} · {e(_when(leg['commence']))}</small></li>"
                   for leg in t["legs"])
    books = _book(t["book"])
    return f"""
<article class="slip slip-teaser">
  <div class="slip-main">
    <div class="slip-meta"><span class="chip chip-nfl">NFL</span><span class="kind">{len(t['legs'])}-team 6-pt teaser</span></div>
    <ul class="legs">{legs}</ul>
    <p class="limit">Wins {t['prob']:.0%} of the time on the historical leg rate. Needs a price of {_fmt_price(t['price'])} or better.</p>
  </div>
  <div class="slip-stub">
    <span class="stub-price">{_fmt_price(t['price'])}</span><span class="stub-book">{e(books)}</span>
    <dl class="stub-nums"><div><dt>Edge</dt><dd>{t['ev'] * 100:+.1f}%</dd></div>
      <div><dt>Stake</dt><dd>${t['stake']:.0f}</dd></div></dl>
  </div>
</article>"""


def _chart(rows: list[dict]) -> str:
    priced = [r for r in rows if r["price"] is not None]
    if len(priced) < 2:
        return ""
    cum, xs = 0.0, [0.0]
    for r in priced:
        cum += r["profit"] or 0
        xs.append(cum)
    W, H, L, R, T, B = 640, 180, 44, 56, 14, 26
    lo, hi = min(0.0, min(xs)), max(0.0, max(xs))
    span = max(1.0, hi - lo)
    step = next(s for s in (0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100) if span / s <= 5)
    lo_t, hi_t = step * (lo // step), step * -(-hi // step)
    sx = lambda i: L + (W - L - R) * i / (len(xs) - 1)  # noqa: E731
    sy = lambda v: T + (H - T - B) * (hi_t - v) / (hi_t - lo_t)  # noqa: E731
    grid, v = [], lo_t
    while v <= hi_t + 1e-9:
        cls = "zero" if abs(v) < 1e-9 else "grid"
        grid.append(f'<line class="{cls}" x1="{L}" x2="{W - R}" y1="{sy(v):.1f}" y2="{sy(v):.1f}"/>'
                    f'<text class="axis" x="{L - 8}" y="{sy(v) + 4:.1f}" text-anchor="end">{v:+g}u</text>')
        v += step
    pts = " ".join(f"{sx(i):.1f},{sy(x):.1f}" for i, x in enumerate(xs))
    area = f"{sx(0):.1f},{sy(0):.1f} {pts} {sx(len(xs) - 1):.1f},{sy(0):.1f}"
    end_x, end_y = sx(len(xs) - 1), sy(xs[-1])
    tone = "up" if xs[-1] >= 0 else "down"
    first = datetime.fromisoformat(priced[0]["commence"]).astimezone(ET).strftime("%b %-d")
    last = datetime.fromisoformat(priced[-1]["commence"]).astimezone(ET).strftime("%b %-d")
    return f"""
<figure class="chart">
  <div class="chart-scroll"><svg viewBox="0 0 {W} {H}" role="img" aria-label="Running total in units: {xs[-1]:+.1f} after {len(priced)} bets">
    {''.join(grid)}
    <polygon class="area {tone}" points="{area}"/>
    <polyline class="run {tone}" points="{pts}"/>
    <circle class="dot {tone}" cx="{end_x:.1f}" cy="{end_y:.1f}" r="4"/>
    <text class="end {tone}" x="{end_x + 8:.1f}" y="{end_y + 4:.1f}">{xs[-1]:+.1f}u</text>
    <text class="axis" x="{L}" y="{H - 6}">{e(first)}</text>
    <text class="axis" x="{W - R}" y="{H - 6}" text-anchor="end">{e(last)}</text>
  </svg></div>
  <figcaption>Running total, 1 unit per bet, {len(priced)} graded bets. Teaser legs aren't included (graded per leg).</figcaption>
</figure>"""


def _record_html(rows: list[dict]) -> str:
    if not rows:
        return """
<div class="empty"><p class="empty-title">No graded bets yet</p>
<p>Every bet on this card is logged at the price shown. After a game ends, the next scan (or <code>python -m betscan card</code>)
grades it against the final score and the closing line, and your record builds here.</p></div>"""
    s = _stats(rows)
    clv = "—" if s["clv"] is None else f"{s['clv'] * 100:+.1f}%"
    beat = "—" if s["beat"] is None else f"{s['beat']:.0%}"
    tiles = f"""
<div class="tiles">
  <div class="tile"><span class="t-label">Record</span><span class="t-val">{s['w']}–{s['l']}{f"–{s['p']}" if s['p'] else ''}</span>
    <span class="t-sub">{s['w'] / max(1, s['w'] + s['l']):.1%} wins</span></div>
  <div class="tile"><span class="t-label">Units</span><span class="t-val {'pos' if s['units'] >= 0 else 'neg'}">{s['units']:+.1f}u</span>
    <span class="t-sub">{s['roi'] * 100:+.1f}% return per bet</span></div>
  <div class="tile"><span class="t-label">Profit at card stakes</span><span class="t-val {'pos' if s['dollars'] >= 0 else 'neg'}">{'+' if s['dollars'] >= 0 else '−'}${abs(s['dollars']):,.0f}</span>
    <span class="t-sub">{s['n']} bets</span></div>
  <div class="tile"><span class="t-label">Beat the close</span><span class="t-val">{beat}</span>
    <span class="t-sub">avg CLV {clv}</span></div>
</div>"""
    groups = defaultdict(list)
    for r in rows:
        groups[_kind_of_pick(r)].append(r)
    by = []
    for kind in ("ncaab_model", "early", "price", "wind", "teaser"):
        g = groups.get(kind)
        if not g:
            continue
        w = sum(r["result"] == "W" for r in g); l = sum(r["result"] == "L" for r in g)
        if kind == "teaser":
            cl = [r["clv"] for r in g if r.get("clv") is not None]
            by.append(f"<tr><th scope='row'>{KIND_LABEL[kind]}</th><td>{w}–{l}</td><td>{w / max(1, w + l):.0%}</td>"
                      f"<td>—</td><td>{(sum(cl) / len(cl)):+.1f} pts</td></tr>" if cl else
                      f"<tr><th scope='row'>{KIND_LABEL[kind]}</th><td>{w}–{l}</td><td>{w / max(1, w + l):.0%}</td><td>—</td><td>—</td></tr>")
            continue
        st = _stats(g)
        tone = "pos" if st["units"] >= 0 else "neg"
        clv_cell = "—" if st["clv"] is None else f"{st['clv'] * 100:+.1f}%"
        by.append(f"<tr><th scope='row'>{KIND_LABEL[kind]}</th><td>{st['w']}–{st['l']}</td>"
                  f"<td>{st['w'] / max(1, st['w'] + st['l']):.0%}</td>"
                  f"<td class='{tone}'>{st['units']:+.1f}u</td><td>{clv_cell}</td></tr>")
    recent = []
    for r in reversed(rows[-12:]):
        res = r["result"]
        pr = "" if r["price"] is None else _fmt_price(r["price"])
        units = "" if r["profit"] is None else f"{r['profit']:+.2f}u"
        recent.append(f"<tr><td><span class='res res-{res}'>{res}</span></td>"
                      f"<td class='rc-pick'>{e(r['selection'])}<small>{e(r['label'])}</small></td>"
                      f"<td>{pr}</td><td class=\"{'pos' if (r['profit'] or 0) > 0 else 'neg' if (r['profit'] or 0) < 0 else ''}\">{units}</td>"
                      f"<td>{datetime.fromisoformat(r['commence']).astimezone(ET).strftime('%b %-d')}</td></tr>")
    return f"""{tiles}
{_chart(rows)}
<div class="two">
  <div class="tblwrap"><h3>By play</h3>
    <table><thead><tr><th>Play</th><th>W–L</th><th>Win</th><th>Units</th><th>CLV</th></tr></thead><tbody>{''.join(by)}</tbody></table></div>
  <div class="tblwrap"><h3>Recent</h3>
    <table class="recent"><thead><tr><th></th><th>Bet</th><th>Price</th><th>Result</th><th>Date</th></tr></thead><tbody>{''.join(recent)}</tbody></table></div>
</div>"""


def render(st: Settings, now: datetime, demo: bool = False) -> str:
    slates = _load_slates(st, demo, now)
    record = _record(st, demo, now)
    bets = sorted((b for s in slates for b in s["bets"]), key=lambda b: b["commence"])
    teasers = [t for s in slates for t in s["teasers"]]
    watch = sorted((w for s in slates for w in s["watch"]), key=lambda w: w["commence"])[:10]
    scanned = max((s["scanned_at"] for s in slates), default=None)
    credits = next((s["credits_left"] for s in sorted(slates, key=lambda s: s["scanned_at"], reverse=True)
                    if s.get("credits_left")), None)
    total_stake = sum(b.get("stake") or 0 for b in bets) + sum(t["stake"] for t in teasers)
    today = now.astimezone(ET)

    if bets or teasers:
        bet_html = "".join(_slip(b) for b in bets) + "".join(_teaser(t) for t in teasers)
    else:
        early = today.month in (11,) or (today.month == 12 and today.day < 15)
        bet_html = f"""<div class="empty"><p class="empty-title">No bets on the card right now</p>
<p>Nothing cleared the bar on the latest scan. That's normal: most days have no edge worth betting.
{"College totals switch on once both teams have played 10 games, around mid-December." if early else ""}</p></div>"""
    def _watch_li(w):
        note = f"<p class='w-note'>{e(w['note'])}</p>" if w["note"] else ""
        return (f"<li><span class='w-tier w-{e(w['tier'].lower())}'>{e(w['tier'])}</span>"
                f"<div><p class='w-sel'>{e(w['selection'])} <span>{_fmt_price(w['price'])} · "
                f"{e(_book(w['book']))}</span></p>"
                f"<p class='w-game'>{e(w['game'])} · {e(_when(w['commence']))}</p>{note}</div></li>")
    watch_html = "".join(_watch_li(w) for w in watch) or "<li class='w-none'>Nothing to watch.</li>"
    ref_html = "".join(f"<tr><th scope='row'>{e(a)}</th><td>{e(b)}</td><td class='num'>{e(c)}</td><td>{e(d)}</td>"
                       f"<td class='num'>{e(f)}</td></tr>" for a, b, c, d, f in REFERENCE)
    banner = ('<p class="sample">Sample card. The bets and record below are made up to show the layout.</p>'
              if demo else "")
    meta = []
    if scanned:
        meta.append(f"Last scan {datetime.fromisoformat(scanned).astimezone(ET).strftime('%a %-I:%M %p ET')}")
    if credits:
        meta.append(f"{credits} odds credits left")
    meta.append(f"Bankroll ${st.bankroll:,.0f}, quarter Kelly")

    return f"""<meta charset="utf-8">
<title>{"Sample Bet Card" if demo else "Bet Card"}</title>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@500;600&display=swap">
<style>
/* Layout: one column of betting slips, stub on the right edge like a printed ticket; record below. */
:root {{
  --bg: #eef1f3; --card: #ffffff; --ink: #14191d; --muted: #5d6870; --line: #d5dbdf;
  --accent: #d9531e; --accent-ink: #ffffff; --win: #1d7a4f; --loss: #b8342c; --push: #7a848b;
  --nfl: #24508f; --ncaab: #d9531e; --stub: #f6f3ef;
  --f-display: "Barlow Condensed", "Arial Narrow", system-ui, sans-serif;
  --f-body: "Inter", system-ui, -apple-system, "Segoe UI", sans-serif;
  --f-num: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #0f1418; --card: #182027; --ink: #e8edf0; --muted: #96a3ab; --line: #2a353d;
  --accent: #f0743d; --accent-ink: #14191d; --win: #4cc489; --loss: #f07a70; --push: #8d989f;
  --nfl: #7fa8e8; --ncaab: #f0743d; --stub: #1f2830; color-scheme: dark; }} }}
:root[data-theme="dark"] {{
  --bg: #0f1418; --card: #182027; --ink: #e8edf0; --muted: #96a3ab; --line: #2a353d;
  --accent: #f0743d; --accent-ink: #14191d; --win: #4cc489; --loss: #f07a70; --push: #8d989f;
  --nfl: #7fa8e8; --ncaab: #f0743d; --stub: #1f2830; color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 var(--f-body); }}
.wrap {{ max-width: 980px; margin: 0 auto; padding-block: 28px 56px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  display: grid; gap: 36px; }}
header {{ display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between; gap: 12px 24px;
  border-bottom: 3px solid var(--ink); padding-bottom: 14px; }}
h1 {{ font: 700 clamp(40px, 7vw, 60px)/0.9 var(--f-display); margin: 0; letter-spacing: 0.01em; text-transform: uppercase; }}
.date {{ font: 600 20px/1 var(--f-display); text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin: 6px 0 0; }}
.meta {{ display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 0; padding: 0; list-style: none; color: var(--muted); font-size: 13px; }}
.sample {{ margin: 0; padding: 10px 14px; border: 1px dashed var(--accent); border-radius: 8px; color: var(--accent); font-weight: 500; }}
h2 {{ font: 700 28px/1 var(--f-display); text-transform: uppercase; letter-spacing: 0.02em; margin: 0;
  display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; text-wrap: balance; }}
h2 small {{ font: 500 14px var(--f-body); text-transform: none; letter-spacing: 0; color: var(--muted); }}
h3 {{ font: 700 18px/1 var(--f-display); text-transform: uppercase; letter-spacing: 0.04em; margin: 0 0 10px; }}
section {{ display: grid; gap: 14px; min-width: 0; }}
section > *, .wrap > * {{ min-width: 0; }}
.slips {{ display: grid; gap: 14px; }}
.slip {{ display: grid; grid-template-columns: 1fr 156px; background: var(--card); border: 1px solid var(--line);
  border-radius: 12px; overflow: hidden; }}
.slip-main {{ padding: 16px 18px; display: grid; gap: 6px; min-width: 0; }}
.slip-meta {{ display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; font-size: 13px; color: var(--muted); }}
.chip {{ font: 600 11px/1 var(--f-body); text-transform: uppercase; letter-spacing: 0.08em; padding: 4px 7px; border-radius: 4px;
  color: var(--accent-ink); background: var(--ncaab); }}
.chip-nfl {{ background: var(--nfl); }}
.kind {{ font-weight: 600; color: var(--ink); }}
.grade {{ font: 700 12px/1 var(--f-num); padding: 4px 7px; border-radius: 4px; border: 1.5px solid currentColor; }}
.grade-a {{ color: var(--win); }} .grade-b {{ color: var(--muted); }} .grade-early {{ color: var(--accent); }}
.slip-meta time {{ margin-left: auto; font-variant-numeric: tabular-nums; }}
.game {{ margin: 2px 0 0; color: var(--muted); }}
.pick {{ margin: 0; font: 700 30px/1.05 var(--f-display); letter-spacing: 0.01em; text-wrap: balance; }}
.why {{ margin: 2px 0 0; padding-left: 18px; color: var(--ink); font-size: 14px; }}
.why li + li {{ margin-top: 2px; }}
.limit {{ margin: 6px 0 0; font-size: 14px; color: var(--muted); }}
.limit b {{ color: var(--ink); font-family: var(--f-num); font-weight: 600; }}
.slip-stub {{ position: relative; background: var(--stub); border-left: 2px dashed var(--line);
  padding: 16px 14px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; text-align: center; }}
.slip-stub::before, .slip-stub::after {{ content: ""; position: absolute; left: -9px; width: 16px; height: 16px; border-radius: 50%;
  background: var(--bg); border: 1px solid var(--line); }}
.slip-stub::before {{ top: -9px; }} .slip-stub::after {{ bottom: -9px; }}
.stub-price {{ font: 600 34px/1 var(--f-num); color: var(--accent); font-variant-numeric: tabular-nums; }}
.stub-book {{ font-size: 13px; font-weight: 600; }}
.stub-nums {{ display: flex; gap: 14px; margin: 8px 0 0; }}
.stub-nums div {{ display: grid; }}
.stub-nums dt {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }}
.stub-nums dd {{ margin: 0; font: 600 15px var(--f-num); font-variant-numeric: tabular-nums; }}
.legs {{ list-style: none; margin: 4px 0 0; padding: 0; display: grid; gap: 8px; }}
.legs span {{ font: 700 24px/1.05 var(--f-display); display: block; }}
.legs b {{ font-family: var(--f-num); font-size: 20px; }}
.legs small {{ color: var(--muted); font-size: 13px; }}
.empty {{ background: var(--card); border: 1px dashed var(--line); border-radius: 12px; padding: 22px; color: var(--muted); }}
.empty p {{ margin: 0; max-width: 62ch; }}
.empty-title {{ font: 700 22px/1.1 var(--f-display); text-transform: uppercase; color: var(--ink); margin-bottom: 6px !important; }}
code {{ font-family: var(--f-num); font-size: 0.9em; }}
.tiles {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 0; background: var(--card);
  border: 1px solid var(--line); border-radius: 12px; }}
.tile {{ padding: 14px 16px; display: grid; gap: 2px; border-left: 1px solid var(--line); min-width: 0; }}
.tile:first-child {{ border-left: 0; }}
.t-label {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }}
.t-val {{ font: 600 28px/1.1 var(--f-num); font-variant-numeric: tabular-nums; }}
.t-sub {{ font-size: 13px; color: var(--muted); }}
.pos {{ color: var(--win); }} .neg {{ color: var(--loss); }}
.chart {{ margin: 0; background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px 12px 8px; }}
.chart svg {{ width: 100%; height: auto; display: block; }}
.chart .grid {{ stroke: var(--line); stroke-width: 1; }}
.chart .zero {{ stroke: var(--muted); stroke-width: 1; stroke-dasharray: 3 3; }}
.chart .axis {{ fill: var(--muted); font: 11px var(--f-num); }}
.chart .run {{ fill: none; stroke-width: 2.5; stroke-linejoin: round; }}
.chart .run.up, .chart .dot.up {{ stroke: var(--win); fill: var(--win); }} .chart .run.up {{ fill: none; }}
.chart .run.down, .chart .dot.down {{ stroke: var(--loss); fill: var(--loss); }} .chart .run.down {{ fill: none; }}
.chart .area.up {{ fill: var(--win); opacity: 0.12; }} .chart .area.down {{ fill: var(--loss); opacity: 0.12; }}
.chart .end {{ font: 600 13px var(--f-num); }} .chart .end.up {{ fill: var(--win); }} .chart .end.down {{ fill: var(--loss); }}
.chart figcaption {{ font-size: 12px; color: var(--muted); padding: 2px 4px 0; }}
.two {{ display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 3fr); gap: 14px; }}
.tblwrap {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; overflow-x: auto; min-width: 0; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; font-variant-numeric: tabular-nums; }}
th, td {{ text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }}
thead th {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); font-weight: 500; }}
tbody tr:last-child th, tbody tr:last-child td {{ border-bottom: 0; }}
tbody th {{ font-weight: 600; }}
td:not(.rc-pick), thead th {{ white-space: nowrap; }}
.ref td {{ white-space: normal; }} .ref td.num {{ white-space: nowrap; }}
.chart-scroll {{ overflow-x: auto; }}
.rc-pick small {{ display: block; color: var(--muted); font-size: 12px; }}
.res {{ display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 5px; font: 600 12px var(--f-num); color: var(--card); }}
.res-W {{ background: var(--win); }} .res-L {{ background: var(--loss); }} .res-P {{ background: var(--push); }}
.watch {{ list-style: none; margin: 0; padding: 0; background: var(--card); border: 1px solid var(--line); border-radius: 12px; }}
.watch li {{ display: grid; grid-template-columns: 74px minmax(0, 1fr); gap: 12px; padding: 12px 16px; border-top: 1px solid var(--line); }}
.watch li:first-child {{ border-top: 0; }}
.watch p {{ margin: 0; }}
.w-tier {{ font: 600 11px/1 var(--f-body); letter-spacing: 0.08em; padding: 5px 0; border-radius: 4px; text-align: center;
  border: 1px solid var(--line); color: var(--muted); align-self: start; }}
.w-verify {{ color: var(--loss); border-color: var(--loss); }}
.w-also {{ color: var(--win); border-color: var(--win); }}
.w-sel {{ font-weight: 600; }} .w-sel span {{ font-weight: 400; color: var(--muted); font-family: var(--f-num); font-size: 13px; }}
.w-game, .w-note {{ font-size: 13px; color: var(--muted); }}
.w-none {{ display: block !important; color: var(--muted); }}
.ref td.num {{ font-family: var(--f-num); white-space: nowrap; }}
.foot {{ font-size: 13px; color: var(--muted); margin: 0; max-width: 70ch; }}
@media (max-width: 640px) {{
  .slip {{ grid-template-columns: 1fr; }}
  .slip-stub {{ border-left: 0; border-top: 2px dashed var(--line); flex-direction: row; flex-wrap: wrap; justify-content: space-between; }}
  .slip-stub::before, .slip-stub::after {{ display: none; }}
  .stub-nums {{ margin: 0; }}
  .slip-meta time {{ margin-left: 0; }}
  .tiles {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .tile:nth-child(3) {{ border-left: 0; }} .tile:nth-child(n+3) {{ border-top: 1px solid var(--line); }}
  .two {{ grid-template-columns: 1fr; }}
  .pick {{ font-size: 26px; }}
  .chart svg {{ min-width: 520px; }}
}}
</style>
<div class="wrap">
  <header>
    <div><h1>Bet Card</h1><p class="date">{e(today.strftime('%A, %B %-d'))}</p></div>
    <ul class="meta">{''.join(f'<li>{e(m)}</li>' for m in meta)}</ul>
  </header>
  {banner}
  <section aria-labelledby="h-bets">
    <h2 id="h-bets">Today's bets <small>{len(bets) + len(teasers)} on the card{f" · ${total_stake:,.0f} total stake" if total_stake else ""}</small></h2>
    <div class="slips">{bet_html}</div>
  </section>
  <section aria-labelledby="h-rec">
    <h2 id="h-rec">Your record <small>bets this card recommended, at the logged price</small></h2>
    {_record_html(record)}
  </section>
  <section aria-labelledby="h-watch">
    <h2 id="h-watch">Watch list <small>also qualified, or close</small></h2>
    <ul class="watch">{watch_html}</ul>
  </section>
  <section aria-labelledby="h-ref">
    <h2 id="h-ref">What to expect <small>how each play did in testing</small></h2>
    <div class="tblwrap"><table class="ref"><thead><tr><th>Play</th><th>Rule</th><th>Hit rate</th><th>Sample</th><th>Break-even</th></tr></thead>
    <tbody>{ref_html}</tbody></table></div>
    <p class="foot">Beating the closing line is the early sign the edge is real; win-loss takes hundreds of bets to mean much.
    If your bets stop beating the close for a month, stop betting that play.</p>
  </section>
</div>"""
