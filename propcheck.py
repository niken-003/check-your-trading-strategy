#!/usr/bin/env python3
"""
propcheck -- is your trading edge real, and could it pass a prop-firm challenge?

Reads an MT5 trade history report (HTML or Excel, from History > right-click >
Report) or a simple CSV, then:
  1. computes honest stats after commission and swap
  2. tests skill vs luck (your results vs thousands of random-direction versions)
  3. replays your trading days thousands of times against prop-firm rules
     (profit target, daily loss limit, max loss) to estimate a pass rate
  4. writes a clean HTML report you can open in any browser

Usage:
    python propcheck.py ReportHistory.html
    python propcheck.py ReportHistory.html --account 100000 --target 10 --daily-loss 5 --max-loss 10
    python propcheck.py trades.csv --balance 10000

This is a statistical look at PAST trades. It is not financial advice and it
cannot guarantee future results.
"""
from __future__ import annotations

import argparse
import html
import io
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

__version__ = "1.1.0"

# Edit these two lines to point the report footer at your own page.
PROMO_URL = "mailto:niken003gurung@gmail.com"
PROMO_TEXT = "Want a strategy or EA tested across markets with real costs, or to collaborate?"


# --------------------------------------------------------------------------- #
# Reading reports
# --------------------------------------------------------------------------- #
def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    if len(raw) > 1 and raw[1:2] == b"\x00":           # UTF-16 LE without BOM (common for MT5)
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _html_rows(text: str) -> list[list[str]]:
    """All table rows from an HTML report, with colspans expanded so columns line up."""
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.S | re.I):
        row = []
        for attrs, cell in re.findall(r"<t[dh]([^>]*)>(.*?)</t[dh]>", tr, flags=re.S | re.I):
            val = html.unescape(re.sub(r"<[^>]+>", " ", cell))
            val = re.sub(r"\s+", " ", val).strip()
            m = re.search(r'colspan\s*=\s*"?(\d+)', attrs, flags=re.I)
            span = int(m.group(1)) if m else 1
            row.append(val)
            row.extend([""] * (span - 1))
        if row:
            rows.append(row)
    return rows


def _num(s) -> float:
    if s is None:
        return float("nan")
    s = str(s).replace("\xa0", "").replace(" ", "").replace(",", "")
    s = s.split("/")[0]
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _time(s):
    s = str(s).strip()
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        return pd.to_datetime(s).to_pydatetime()
    except Exception:
        return None


def _positions_from_rows(rows: list[list[str]]) -> pd.DataFrame:
    """Find the 'Positions' table of an MT5 report and parse its closed trades."""
    hdr_i, cols = None, None
    for i, r in enumerate(rows):
        low = [c.strip().lower() for c in r]
        if "symbol" in low and "profit" in low and low.count("time") >= 2:
            hdr_i, cols = i, low
            break
    if hdr_i is None:
        raise ValueError("Could not find the 'Positions' table. Export from MT5: History tab > "
                         "right-click > Report, and choose 'Positions' view if asked.")

    def idx(name, nth=0):
        hits = [k for k, c in enumerate(cols) if c == name]
        return hits[nth] if len(hits) > nth else None

    ix = {
        "open_time": idx("time", 0), "close_time": idx("time", 1),
        "symbol": idx("symbol"), "type": idx("type"), "volume": idx("volume"),
        "open_price": idx("price", 0), "close_price": idx("price", 1),
        "sl": idx("s / l") if idx("s / l") is not None else idx("s/l"),
        "commission": idx("commission"), "swap": idx("swap"), "profit": idx("profit"),
    }
    out = []
    for r in rows[hdr_i + 1:]:
        get = lambda k: r[ix[k]] if ix[k] is not None and ix[k] < len(r) else ""
        typ = get("type").lower()
        if typ not in ("buy", "sell"):
            if out and r and _time(r[0]) is None and any(x.strip() for x in r):
                break                                     # next section started
            continue
        ct = _time(get("close_time"))
        if ct is None:
            continue
        out.append({
            "open_time": _time(get("open_time")), "close_time": ct,
            "symbol": get("symbol"), "type": typ, "volume": _num(get("volume")),
            "open_price": _num(get("open_price")), "close_price": _num(get("close_price")),
            "sl": _num(get("sl")),
            "commission": np.nan_to_num(_num(get("commission"))),
            "swap": np.nan_to_num(_num(get("swap"))),
            "profit": _num(get("profit")),
        })
    df = pd.DataFrame(out)
    if df.empty:
        raise ValueError("Found the Positions table but no closed buy/sell trades in it.")
    return df


def _detect_balance(rows: list[list[str]]) -> float | None:
    """First 'balance' deposit row in the report (MT5 lists the initial deposit there)."""
    for r in rows:
        low = [c.strip().lower() for c in r]
        if "balance" in low[:6]:
            nums = [_num(c) for c in r]
            nums = [n for n in nums if np.isfinite(n) and n > 0]
            if nums:
                return max(nums)
    return None


CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "AUD": "A$", "CAD": "C$",
                    "NZD": "NZ$", "CHF": "CHF ", "SGD": "S$", "HKD": "HK$", "INR": "₹", "ZAR": "R "}


def currency_symbol(code_or_symbol: str | None) -> str:
    if not code_or_symbol:
        return "$"
    c = code_or_symbol.strip()
    return CURRENCY_SYMBOLS.get(c.upper(), c if len(c) <= 3 and not c.isalpha() else c.upper() + " ")


def _detect_currency(rows: list[list[str]]) -> str | None:
    """MT5 reports show e.g. 'Account: 1234567 (USD, Broker-Server, demo, Hedge)'."""
    for r in rows[:40]:
        text = " ".join(r)
        m = re.search(r"\(([A-Z]{3}),", text)
        if m and ("account" in text.lower() or "hedge" in text.lower() or "netting" in text.lower()):
            return m.group(1)
    return None


def _csv_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    if "close_time" not in df or "profit" not in df:
        rows = [list(map(str, df.columns))] + df.astype(str).values.tolist()
        return _positions_from_rows(rows)
    for c in ("commission", "swap"):
        df[c] = pd.to_numeric(df.get(c, 0), errors="coerce").fillna(0)
    df["profit"] = pd.to_numeric(df["profit"], errors="coerce")
    df["close_time"] = pd.to_datetime(df["close_time"])
    df["symbol"] = df.get("symbol", "n/a")
    df["type"] = df.get("type", "n/a")
    return df


def load_trades(path: Path) -> tuple[pd.DataFrame, float | None, str | None]:
    """Returns (trades, starting balance or None, account currency code or None)."""
    ext = path.suffix.lower()
    if ext in (".htm", ".html"):
        rows = _html_rows(_read_text(path))
        return _positions_from_rows(rows), _detect_balance(rows), _detect_currency(rows)
    if ext in (".xlsx", ".xls"):
        raw = pd.read_excel(path, header=None, dtype=str).fillna("")
        rows = raw.values.tolist()
        return _positions_from_rows(rows), _detect_balance(rows), _detect_currency(rows)
    if ext == ".csv":
        return _csv_trades(path), None, None
    raise ValueError(f"Unsupported file type '{ext}'. Use an MT5 .html/.xlsx report or a .csv.")


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
@dataclass
class Rules:
    account: float
    target: float = 10.0
    daily_loss: float = 5.0
    max_loss: float = 10.0
    min_days: int = 4
    max_days: int = 60


def core_stats(df: pd.DataFrame, balance: float) -> dict:
    net = df["net"].values
    wins, losses = net[net > 0], net[net < 0]
    eq = np.concatenate([[0], np.cumsum(net)])
    dd = eq - np.maximum.accumulate(eq)
    streak = best = 0
    for x in net:
        streak = streak + 1 if x < 0 else 0
        best = max(best, streak)
    daily = df.groupby(df["close_time"].dt.date)["net"].sum()
    gross_profit = df.loc[df["profit"] > 0, "profit"].sum()
    costs = -(df["commission"].sum() + df["swap"].sum())
    return {
        "trades": len(net), "days": int(daily.size),
        "first": df["close_time"].min(), "last": df["close_time"].max(),
        "net": net.sum(), "net_pct": 100 * net.sum() / balance,
        "win_rate": 100 * (net > 0).mean(),
        "avg_win": wins.mean() if wins.size else 0.0,
        "avg_loss": losses.mean() if losses.size else 0.0,
        "pf": wins.sum() / -losses.sum() if losses.size and losses.sum() < 0 else float("inf"),
        "expectancy": net.mean(),
        "max_dd": -dd.min(), "max_dd_pct": 100 * -dd.min() / balance,
        "losing_streak": best,
        "best_day": daily.max(), "worst_day": daily.min(),
        "worst_day_pct": 100 * daily.min() / balance,
        "costs": costs, "cost_share": 100 * costs / gross_profit if gross_profit > 0 else float("nan"),
        "equity": eq,
    }


def luck_test(net: np.ndarray, sims: int, rng) -> dict:
    """How often do random-direction versions of the same trades do as well?"""
    obs = net.mean()
    signs = rng.choice([-1.0, 1.0], size=(sims, net.size))
    sim_means = (signs * np.abs(net)).mean(axis=1)
    p = float((sim_means >= obs).mean())
    boots = rng.choice(net, size=(sims, net.size), replace=True).mean(axis=1)
    lo, hi = np.percentile(boots, [5, 95])
    if net.size < 30:
        verdict, tone = "Too few trades to tell skill from luck. Aim for 50 or more.", "warn"
    elif p < 0.05 and lo > 0:
        verdict, tone = "Unlikely to be luck alone. The edge looks real so far.", "good"
    elif p < 0.20:
        verdict, tone = "Some sign of an edge, but luck could still explain it.", "warn"
    else:
        verdict, tone = "These results could easily be luck.", "bad"
    return {"p": p, "ci": (lo, hi), "verdict": verdict, "tone": tone}


def stability(df: pd.DataFrame) -> dict:
    half = len(df) // 2
    a, b = df["net"].iloc[:half], df["net"].iloc[half:]
    return {"first_avg": a.mean(), "second_avg": b.mean(),
            "first_wr": 100 * (a > 0).mean(), "second_wr": 100 * (b > 0).mean(),
            "consistent": (a.mean() > 0) == (b.mean() > 0)}


def challenge_sim(df: pd.DataFrame, balance: float, rules: Rules, sims: int, rng) -> dict:
    """Replay random trading days (each day's trades in order) against prop-firm rules.
    Returns are scaled as % of balance, so results transfer to any account size."""
    days = [g["net"].values * 100 / balance for _, g in df.groupby(df["close_time"].dt.date)]
    tgt, dl, ml = rules.target, rules.daily_loss, rules.max_loss
    res = {"pass": 0, "daily": 0, "max": 0, "time": 0}
    pass_days = []
    for _ in range(sims):
        bal, n = 0.0, 0
        outcome = "time"
        while n < rules.max_days:
            day_start = bal
            day = days[rng.integers(len(days))]
            failed = None
            for r in day:
                bal += r
                if bal <= -ml:
                    failed = "max"
                    break
                if bal <= day_start - dl:
                    failed = "daily"
                    break
            n += 1
            if failed:
                outcome = failed
                break
            if bal >= tgt and n >= rules.min_days:
                outcome = "pass"
                pass_days.append(n)
                break
        res[outcome] += 1
    pct = {k: 100 * v / sims for k, v in res.items()}
    pct["median_days"] = float(np.median(pass_days)) if pass_days else None
    return pct


# --------------------------------------------------------------------------- #
# HTML report
# --------------------------------------------------------------------------- #
def _money(x, cur="$"):
    if x is None or not np.isfinite(x):
        return "n/a"
    sign = "-" if x < 0 else ""
    return f"{sign}{cur}{abs(x):,.2f}"


def _svg_equity(eq: np.ndarray, w=720, h=220) -> str:
    n = len(eq)
    lo, hi = min(eq.min(), 0), max(eq.max(), 0)
    span = hi - lo or 1
    xs = [8 + i * (w - 16) / max(n - 1, 1) for i in range(n)]
    ys = [h - 12 - (v - lo) / span * (h - 24) for v in eq]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    zero = h - 12 - (0 - lo) / span * (h - 24)
    peak = np.maximum.accumulate(eq)
    py = [h - 12 - (v - lo) / span * (h - 24) for v in peak]
    dd_poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, py)) + " " + \
              " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(reversed(xs), reversed(ys)))
    end_col = "var(--good)" if eq[-1] >= 0 else "var(--bad)"
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Equity curve after costs">'
            f'<line x1="8" x2="{w-8}" y1="{zero:.1f}" y2="{zero:.1f}" class="zero"/>'
            f'<polygon points="{dd_poly}" class="dd"/>'
            f'<polyline points="{pts}" fill="none" stroke="{end_col}" stroke-width="2.2" '
            f'stroke-linejoin="round"/></svg>')


def _bar(sim: dict) -> str:
    parts = [("pass", "Passed", "good"), ("daily", "Hit daily loss limit", "bad"),
             ("max", "Hit max loss", "bad2"), ("time", "Ran out of days", "muted")]
    segs = "".join(f'<span class="seg {c}" style="width:{sim[k]:.2f}%" title="{lbl}: {sim[k]:.1f}%"></span>'
                   for k, lbl, c in parts if sim[k] > 0)
    legend = "".join(f'<li><span class="dot {c}"></span>{lbl} <b>{sim[k]:.1f}%</b></li>'
                     for k, lbl, c in parts)
    return f'<div class="bar">{segs}</div><ul class="legend">{legend}</ul>'


def render(path, df, st, luck, stab, sim, rules, balance, cur="$") -> str:
    pr = sim["pass"]
    tone = "good" if pr >= 50 else ("warn" if pr >= 25 else "bad")
    md = f", typically within {sim['median_days']:.0f} trading days" if sim["median_days"] else ""
    by_sym = (df.groupby("symbol")["net"].agg(["count", "sum", lambda s: 100 * (s > 0).mean()])
              .sort_values("sum", ascending=False))
    sym_rows = "".join(f"<tr><td>{html.escape(str(s))}</td><td>{int(r['count'])}</td>"
                       f"<td>{r.iloc[2]:.0f}%</td><td class='{'pos' if r['sum']>=0 else 'neg'}'>{_money(r['sum'], cur)}</td></tr>"
                       for s, r in by_sym.iterrows())
    pf = "∞" if not np.isfinite(st["pf"]) else f"{st['pf']:.2f}"
    stab_note = "" if stab["consistent"] else " Your early and recent trades disagree, so treat this with extra caution."
    stab_txt = ("Both halves of your history point the same way." if stab["consistent"]
                else "The two halves of your history disagree, so the edge may not be stable.")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>propcheck report: {html.escape(path.name)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Serif:wght@500;600&display=swap" rel="stylesheet">
<style>
:root {{
  --ink:#1c2733; --slate:#56636f; --paper:#f3f5f2; --sheet:#ffffff; --rule:#d9dfdb;
  --good:#1f7a52; --warn:#a86a12; --bad:#b23a26; --bad2:#7d2a1c; --muted:#9aa5ae;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--paper); color:var(--ink);
  font:16px/1.6 "IBM Plex Sans", "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
main {{ max-width:760px; margin:0 auto; padding:48px 24px 64px; }}
.file {{ color:var(--slate); font-size:14px; margin:0 0 28px; }}
h1 {{ font:600 clamp(28px,4.6vw,40px)/1.2 "IBM Plex Serif", Georgia, serif; margin:0 0 16px; letter-spacing:-0.01em; }}
h1 .num {{ color:var(--{tone}); }}
.lede {{ font-size:19px; color:var(--slate); margin:0 0 40px; max-width:62ch; }}
section {{ background:var(--sheet); border:1px solid var(--rule); border-radius:6px; padding:24px 26px; margin:0 0 20px; }}
h2 {{ font:600 21px/1.3 "IBM Plex Serif", Georgia, serif; margin:0 0 6px; }}
.sub {{ color:var(--slate); margin:0 0 18px; font-size:15px; }}
.verdict {{ font-weight:600; margin:0 0 4px; }}
.verdict.good {{ color:var(--good); }} .verdict.warn {{ color:var(--warn); }} .verdict.bad {{ color:var(--bad); }}
table {{ width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; font-size:15px; }}
td, th {{ padding:7px 4px; border-bottom:1px solid var(--rule); text-align:left; }}
th {{ font-weight:500; color:var(--slate); }}
td:last-child, th:last-child {{ text-align:right; }}
.stats td:first-child {{ color:var(--slate); }}
.pos {{ color:var(--good); }} .neg {{ color:var(--bad); }}
svg {{ width:100%; height:auto; display:block; margin:8px 0 4px; }}
svg .zero {{ stroke:var(--rule); stroke-dasharray:4 4; }}
svg .dd {{ fill:var(--bad); opacity:.10; }}
.bar {{ display:flex; height:26px; border-radius:4px; overflow:hidden; background:var(--rule); margin:10px 0 12px; }}
.seg.good {{ background:var(--good); }} .seg.bad {{ background:var(--bad); }}
.seg.bad2 {{ background:var(--bad2); }} .seg.muted {{ background:var(--muted); }}
.legend {{ list-style:none; padding:0; margin:0; display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:4px 16px; font-size:15px; }}
.dot {{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:8px; vertical-align:0; }}
.dot.good {{ background:var(--good); }} .dot.bad {{ background:var(--bad); }}
.dot.bad2 {{ background:var(--bad2); }} .dot.muted {{ background:var(--muted); }}
details {{ font-size:15px; color:var(--slate); }}
summary {{ cursor:pointer; color:var(--ink); font-weight:500; }}
summary:focus-visible, a:focus-visible {{ outline:2px solid var(--ink); outline-offset:3px; }}
footer {{ margin-top:36px; font-size:15px; color:var(--slate); }}
footer a {{ color:var(--ink); }}
@media (max-width:520px) {{ section {{ padding:18px 16px; }} .lede {{ font-size:17px; }} }}
</style></head>
<body><main>
<p class="file">{html.escape(path.name)} · {st['trades']} trades over {st['days']} trading days · {st['first']:%d %b %Y} to {st['last']:%d %b %Y}</p>

<h1><span class="num">{pr:.0f}%</span> of simulated challenges passed with these trades.</h1>
<p class="lede">{html.escape(luck['verdict'])}{stab_note} The pass rate assumes your past trading continues unchanged. Rules used: {rules.target:g}% profit target, {rules.daily_loss:g}% daily loss limit, {rules.max_loss:g}% max loss, at least {rules.min_days} trading days, within {rules.max_days} trading days{md}.</p>

<section>
<h2>Challenge simulation</h2>
<p class="sub">Your real trading days were replayed in random order {sim['n']:,} times, sized to your account.</p>
{_bar(sim)}
</section>

<section>
<h2>Skill or luck</h2>
<p class="verdict {luck['tone']}">{html.escape(luck['verdict'])}</p>
<p class="sub">Random-direction versions of your exact trades did at least as well {100*luck['p']:.0f}% of the time.
A typical trade is worth between {_money(luck['ci'][0], cur)} and {_money(luck['ci'][1], cur)} (90% range).</p>
</section>

<section>
<h2>Results after costs</h2>
<p class="sub">Every figure includes commission and swap.</p>
{_svg_equity(st['equity'])}
<table class="stats">
<tr><td>Net profit</td><td class="{'pos' if st['net']>=0 else 'neg'}">{_money(st['net'], cur)} ({st['net_pct']:+.2f}%)</td></tr>
<tr><td>Win rate</td><td>{st['win_rate']:.1f}%</td></tr>
<tr><td>Average win / average loss</td><td>{_money(st['avg_win'], cur)} / {_money(st['avg_loss'], cur)}</td></tr>
<tr><td>Profit factor</td><td>{pf}</td></tr>
<tr><td>Average per trade</td><td>{_money(st['expectancy'], cur)}</td></tr>
<tr><td>Largest drawdown</td><td>{_money(-st['max_dd'], cur)} ({st['max_dd_pct']:.2f}%)</td></tr>
<tr><td>Worst day</td><td>{_money(st['worst_day'], cur)} ({st['worst_day_pct']:.2f}%)</td></tr>
<tr><td>Longest losing streak</td><td>{st['losing_streak']} trades</td></tr>
<tr><td>Commission and swap paid</td><td>{_money(st['costs'], cur)}{'' if not np.isfinite(st['cost_share']) else f" ({st['cost_share']:.1f}% of gross profit)"}</td></tr>
</table>
</section>

<section>
<h2>Stability</h2>
<p class="sub">{stab_txt}</p>
<table>
<tr><th>Period</th><th>Win rate</th><th>Average per trade</th></tr>
<tr><td>First half of trades</td><td>{stab['first_wr']:.0f}%</td><td class="{'pos' if stab['first_avg']>=0 else 'neg'}">{_money(stab['first_avg'], cur)}</td></tr>
<tr><td>Second half of trades</td><td>{stab['second_wr']:.0f}%</td><td class="{'pos' if stab['second_avg']>=0 else 'neg'}">{_money(stab['second_avg'], cur)}</td></tr>
</table>
</section>

<section>
<h2>By symbol</h2>
<table><tr><th>Symbol</th><th>Trades</th><th>Win rate</th><th>Net</th></tr>{sym_rows}</table>
</section>

<details>
<summary>How this was calculated</summary>
<p>Net results are profit plus commission plus swap for each closed position. The luck test flips the direction of each trade at random, keeping its size, and counts how often those versions match your average result. The challenge simulation picks whole trading days from your history at random, keeps the trades inside each day in order, and checks the rules after every closed trade. Floating losses on open trades are not visible in a history report, so real daily drawdowns can be deeper than shown. Starting balance used: {_money(balance, cur)}. Works with any symbol: results come from each trade's profit, commission and swap in your account currency.</p>
<p>This is a statistical look at past trades. It is not financial advice and cannot guarantee future results.</p>
</details>

<footer>
<p>Made with propcheck {__version__}. {html.escape(PROMO_TEXT)} <a href="{html.escape(PROMO_URL)}">Get in touch</a>.</p>
</footer>
</main></body></html>"""


# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Skill or luck? Prop-firm challenge odds from your MT5 history.")
    ap.add_argument("report", help="MT5 history report (.html/.xlsx) or .csv")
    ap.add_argument("--balance", type=float, help="starting balance of the account in the report "
                                                  "(auto-detected from MT5 reports when possible)")
    ap.add_argument("--target", type=float, default=10.0, help="profit target %% (default 10)")
    ap.add_argument("--daily-loss", type=float, default=5.0, help="daily loss limit %% (default 5)")
    ap.add_argument("--max-loss", type=float, default=10.0, help="max loss %% (default 10)")
    ap.add_argument("--min-days", type=int, default=4, help="minimum trading days (default 4)")
    ap.add_argument("--max-days", type=int, default=60, help="give up after this many trading days (default 60)")
    ap.add_argument("--currency", help="account currency, e.g. USD, EUR, GBP (auto-detected from MT5 reports)")
    ap.add_argument("--sims", type=int, default=5000, help="number of simulations (default 5000)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("-o", "--out", help="output HTML file (default: <report>_propcheck.html)")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")          # Windows consoles and symbols like ₹
    except (AttributeError, ValueError):
        pass

    path = Path(a.report)
    if not path.exists():
        sys.exit(f"File not found: {path}")
    try:
        df, detected, detected_cur = load_trades(path)
    except ValueError as e:
        sys.exit(f"Could not read the report: {e}")

    balance = a.balance or detected
    if not balance:
        sys.exit("Could not detect the starting balance. Run again with --balance, e.g. --balance 100000")

    cur = currency_symbol(a.currency or detected_cur or "USD")
    df = df.dropna(subset=["profit"]).sort_values("close_time").reset_index(drop=True)
    df["net"] = df["profit"] + df["commission"] + df["swap"]
    rng = np.random.default_rng(a.seed)
    rules = Rules(account=balance, target=a.target, daily_loss=a.daily_loss, max_loss=a.max_loss,
                  min_days=a.min_days, max_days=a.max_days)

    st = core_stats(df, balance)
    luck = luck_test(df["net"].values, a.sims, rng)
    stab = stability(df)
    sim = challenge_sim(df, balance, rules, a.sims, rng)
    sim["n"] = a.sims

    out = Path(a.out) if a.out else path.with_name(path.stem + "_propcheck.html")
    out.write_text(render(path, df, st, luck, stab, sim, rules, balance, cur), encoding="utf-8")

    print(f"Trades: {st['trades']}  Net: {_money(st['net'], cur)} ({st['net_pct']:+.2f}%)  "
          f"Win rate: {st['win_rate']:.1f}%  Max DD: {st['max_dd_pct']:.2f}%")
    print(f"Skill or luck: {luck['verdict']} (random versions matched you {100*luck['p']:.0f}% of the time)")
    print(f"Challenge pass rate: {sim['pass']:.1f}%  |  daily-limit fails {sim['daily']:.1f}%  "
          f"max-loss fails {sim['max']:.1f}%  out of time {sim['time']:.1f}%")
    print(f"Report written to {out}")


if __name__ == "__main__":
    main()
