"""Terminal, JSON and HTML output."""
from __future__ import annotations

import html
import json
from datetime import datetime

from .models import Candidate

TIER_NOTE = {
    "BET": "price beats the sharp fair line",
    "VERIFY": "EV too large to trust - check the line is live and the market matches",
    "LEAN": "situational edge, price roughly fair",
    "WATCH": "news/situation worth a look; no price edge",
    "ALT": "same position as a BET above in another market - pick one",
}


def _fmt_price(p):
    return "-" if p is None else (f"+{p:.0f}" if p > 0 else f"{p:.0f}")


def _when(c: Candidate) -> str:
    return c.game.commence.astimezone().strftime("%a %m/%d %I:%M%p")


def terminal(cands: list[Candidate], show=("BET", "VERIFY", "ALT", "LEAN", "WATCH"), limit=40) -> str:
    rows = [c for c in cands if c.tier in show][:limit]
    if not rows:
        return "No opportunities met the thresholds."
    lines = []
    for c in rows:
        ev = f"{c.ev * 100:+.1f}%" if c.ev is not None else "  n/a"
        fair = f"{c.fair_prob * 100:.1f}%" if c.fair_prob else "-"
        lines.append(f"[{c.tier:<6}] {c.game.sport.upper():<5} {_when(c)}  {c.game.label}")
        lines.append(f"         {c.selection:<28} {_fmt_price(c.best_price):>5} @ {c.best_book or '-':<14}"
                     f" fair {fair:<6} EV {ev}{' (approx)' if c.approx else ''}  score {c.score:+.1f}"
                     + (f"  stake ${c.stake:.2f}" if c.stake else ""))
        for s in c.signals:
            lines.append(f"           + {s.kind:<8} {s.strength:>4.1f}  {s.detail}")
        for s in c.against:
            lines.append(f"           - {s.kind:<8} {s.strength:>4.1f}  {s.detail}")
    return "\n".join(lines)


def to_json(cands: list[Candidate]) -> str:
    out = []
    for c in cands:
        if c.tier == "PASS":
            continue
        out.append({
            "tier": c.tier, "sport": c.game.sport, "game": c.game.label,
            "commence": c.game.commence.isoformat(), "market": c.market, "selection": c.selection,
            "book": c.best_book, "price": c.best_price, "fair_prob": c.fair_prob, "ev": c.ev,
            "approx": c.approx, "score": c.score, "stake": c.stake,
            "signals": [vars(s) for s in c.signals], "against": [vars(s) for s in c.against],
        })
    return json.dumps(out, indent=2)


def to_html(cands: list[Candidate], generated: datetime, demo: bool) -> str:
    e = html.escape
    cards = []
    for c in cands:
        if c.tier == "PASS":
            continue
        sig = "".join(f'<li class="pro"><b>{e(s.kind)}</b> +{s.strength:.1f} {e(s.detail)}</li>' for s in c.signals)
        sig += "".join(f'<li class="con"><b>{e(s.kind)}</b> −{s.strength:.1f} {e(s.detail)}</li>' for s in c.against)
        ev = f"{c.ev * 100:+.1f}%" if c.ev is not None else "n/a"
        cards.append(f"""
<article class="card t-{c.tier.lower()}">
  <header><span class="tier">{c.tier}</span><span class="meta">{e(c.game.sport.upper())} · {e(_when(c))}</span></header>
  <h2>{e(c.game.label)}</h2>
  <p class="pick">{e(c.selection)} <span class="px">{_fmt_price(c.best_price)}</span> at {e(c.best_book or '-')}</p>
  <dl><div><dt>Fair</dt><dd>{(c.fair_prob or 0) * 100:.1f}%</dd></div>
      <div><dt>EV</dt><dd>{ev}{' ≈' if c.approx else ''}</dd></div>
      <div><dt>Score</dt><dd>{c.score:+.1f}</dd></div>
      <div><dt>Stake</dt><dd>{f'${c.stake:.2f}' if c.stake else '—'}</dd></div></dl>
  <p class="note">{e(TIER_NOTE.get(c.tier, ''))}</p>
  <ul>{sig}</ul>
</article>""")
    banner = '<p class="demo">DEMO DATA: fabricated lines and news for illustration only.</p>' if demo else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Betscan Report</title>
<style>
:root{{--bg:#f7f7f5;--fg:#1d1d1b;--muted:#6b6b66;--card:#fff;--line:#e4e4df;--bet:#1f7a4d;--lean:#2b5fab;--watch:#9a6b12;--verify:#b23b3b}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#151514;--fg:#ececea;--muted:#9a9a94;--card:#1f1f1d;--line:#33332f;--bet:#4fc48a;--lean:#7aa7f0;--watch:#e0b050;--verify:#f07a7a}}}}
:root[data-theme="dark"]{{--bg:#151514;--fg:#ececea;--muted:#9a9a94;--card:#1f1f1d;--line:#33332f;--bet:#4fc48a;--lean:#7aa7f0;--watch:#e0b050;--verify:#f07a7a}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px}} h1{{margin:0 0 4px;font-size:24px}}
.sub{{color:var(--muted);margin:0 0 16px}} .demo{{border:1px dashed var(--verify);color:var(--verify);padding:8px 12px;border-radius:8px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}}
.card{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--c);border-radius:10px;padding:14px}}
.t-bet{{--c:var(--bet)}} .t-lean{{--c:var(--lean)}} .t-watch{{--c:var(--watch)}} .t-verify{{--c:var(--verify)}} .t-alt{{--c:var(--muted)}}
header{{display:flex;justify-content:space-between;font-size:12px}} .tier{{color:var(--c);font-weight:700;letter-spacing:.05em}}
.meta{{color:var(--muted)}} h2{{font-size:17px;margin:6px 0}} .pick{{font-weight:600;margin:0 0 8px}} .px{{color:var(--c)}}
dl{{display:grid;grid-template-columns:repeat(4,1fr);margin:0 0 8px}} dt{{font-size:11px;color:var(--muted)}} dd{{margin:0;font-variant-numeric:tabular-nums}}
.note{{font-size:12px;color:var(--muted);margin:0 0 6px}} ul{{margin:0;padding-left:18px;font-size:13px}} li.con{{color:var(--muted)}}
</style></head><body><main>
<h1>Betscan</h1><p class="sub">Generated {e(generated.astimezone().strftime('%Y-%m-%d %H:%M %Z'))}. Not betting advice; verify every line before wagering.</p>
{banner}<div class="grid">{''.join(cards) or '<p>No opportunities met the thresholds.</p>'}</div></main></body></html>"""
