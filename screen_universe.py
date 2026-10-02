"""
Pick the target roster from the whole US market, on fundamentals.

Run quarterly, not daily. Two reasons: the inputs are 10-K/10-Q figures that only
change four times a year, and the accuracy experiment needs 30 graded predictions
PER SYMBOL — a roster that churns weekly never lets any symbol get there, so the
central question stays unanswerable no matter how long it runs.

Fundamentals are used **only to choose who is worth predicting.** They never size
a position, never pick a side, and never gate a short. A balance sheet is a
multi-year signal and this system holds for days to three weeks; asset cover tells
you nothing about a 2% move a fortnight out. What it does tell you is whether a
company deserves to be in the pool at all.

The funnel:

    ~13,500 tradable US equities        Alpaca /v2/assets
      -> common shares on real exchanges
      -> top POOL by 30-day dollar volume    one bulk Alpaca bars call
      -> fundamentals from Yahoo             ~0.7s each, so POOL stays modest
      -> ranked, top N                       plus the pinned index ETFs

Scoring is a rank-average of four scale-free components, so no single raw number
dominates and outliers cannot run away with it:

    assets / liabilities        can the company survive a bad year
    revenue growth - opex       is it growing faster than it is spending
    free cash flow growth       is cash generation improving
    free cash flow margin       ...and is it efficient, not merely large

That fourth one is an addition, not something that was asked for. Without it the
first three rank Apple LAST of the megacaps and Tesla fourth, because a raw
assets-to-liabilities ratio punishes companies that borrow deliberately to buy
back stock. FCF margin is scale-free, so it rewards efficient cash generation
rather than size. Drop it by removing it from COMPONENTS if you disagree.

Sector and industry are carried through but NOT scored. Judging whether an
industry is relevant is the one part of this that is not arithmetic, so it is
left to the routine, which reads these strings and can drop names on that basis.

    python screen_universe.py --pool 600 --targets 38
"""

import argparse
import json
import os
import statistics
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import broker

warnings.filterwarnings("ignore")

OUT = Path(__file__).parent / "data" / "universe.json"
DATA_BASE = "https://data.alpaca.markets"
HEADERS = {
    "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
    "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
}

# Index ETFs are always predicted: they are the benchmark the stock picks have to
# beat, and the cleanest expression of a whole-market call. They have no
# fundamentals, so they bypass the screen rather than scoring zero on it.
PINNED = ["SPY", "QQQ"]

REAL_EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}

# Alpaca's free tier reports IEX volume only, which is roughly 2-3% of
# consolidated tape. $2M here is therefore on the order of $80M real, not $2M.
MIN_DOLLAR_VOLUME = 2_000_000
MIN_PRICE = 5.0                    # below this, spreads and tick noise dominate

COMPONENTS = ["assets_to_liabilities", "growth_spread_pct",
              "fcf_growth_pct", "fcf_margin_pct"]

# Percentage growth off a tiny or negative base is noise that dominates a rank:
# unclipped, a company recovering from near-zero free cash flow posts +9,678% and
# outranks every genuinely good business. Clipping makes "grew enormously" a tie
# rather than a prize. A spread beyond +-50pp is nearly always a spinoff or an
# acquisition rather than operating performance.
CLIP = {"growth_spread_pct": 50.0, "fcf_growth_pct": 100.0}

# A quality rank with no sector constraint is a technology screen in disguise:
# tech is structurally asset-light, low-debt and high-margin, so it sweeps every
# component. Capping per sector is what actually diversifies the book.
MAX_PER_SECTOR = 5

# A sector cap alone is not enough: "Basic Materials: 5" turned out to be four
# gold miners and a silver one, which is a single bet on the metals price
# wearing five tickers. Industry is the level concentration actually hides at.
MAX_PER_INDUSTRY = 2

# Growth is only meaningful against a base worth growing from.
MIN_FCF_BASE_MARGIN = 0.02         # prior FCF must be >=2% of prior revenue


def tradable_pool() -> dict[str, dict]:
    """Common shares on a real exchange. Excludes ARCA/BATS, which are mostly
    ETFs, and anything with a punctuated symbol (warrants, units, preferreds)."""
    out = {}
    for a in broker.assets():
        sym = a["symbol"]
        if not a.get("tradable") or a.get("exchange") not in REAL_EXCHANGES:
            continue
        if not sym.isalpha() or len(sym) > 5:
            continue
        out[sym] = {
            "exchange": a["exchange"],
            "shortable": bool(a.get("shortable") and a.get("easy_to_borrow")),
            "name": a.get("name", ""),
        }
    return out


def liquidity(symbols: list[str], days: int = 45) -> dict[str, dict]:
    """30-session median dollar volume and last close, in bulk."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    out: dict[str, list] = {}
    for i in range(0, len(symbols), 500):
        chunk = symbols[i:i + 500]
        params = {
            "symbols": ",".join(chunk), "timeframe": "1Day",
            "start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"),
            "limit": 10000, "adjustment": "all", "feed": "iex",
        }
        token = None
        while True:
            if token:
                params["page_token"] = token
            r = requests.get(f"{DATA_BASE}/v2/stocks/bars", headers=HEADERS,
                             params=params, timeout=90)
            r.raise_for_status()
            payload = r.json()
            for sym, bars in (payload.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            token = payload.get("next_page_token")
            if not token:
                break

    res = {}
    for sym, bars in out.items():
        bars = sorted(bars, key=lambda b: b["t"])[-30:]
        if len(bars) < 15:
            continue
        dv = [b["c"] * b["v"] for b in bars if b.get("c") and b.get("v")]
        if not dv:
            continue
        res[sym] = {
            "dollar_volume": statistics.median(dv),
            "last_close": bars[-1]["c"],
            "sessions": len(bars),
        }
    return res


def fundamentals(symbol: str) -> dict | None:
    """The four scored signals plus sector/industry, from Yahoo."""
    import yfinance as yf

    def pick(df, keys):
        if df is None or df.empty:
            return None
        for k in keys:
            if k in df.index:
                s = df.loc[k].dropna()
                if len(s):
                    return [float(v) for v in s.values]
        return None

    def growth(vals):
        if not vals or len(vals) < 2 or not vals[1]:
            return None
        return (vals[0] - vals[1]) / abs(vals[1]) * 100

    try:
        t = yf.Ticker(symbol)
        bs, inc, cf, info = t.balance_sheet, t.income_stmt, t.cashflow, t.info
    except Exception:
        return None
    if (info or {}).get("quoteType") not in (None, "EQUITY"):
        return None

    assets = pick(bs, ["Total Assets"])
    liabs = pick(bs, ["Total Liabilities Net Minority Interest", "Total Liabilities"])
    rev = pick(inc, ["Total Revenue", "Operating Revenue"])
    opex = pick(inc, ["Operating Expense", "Total Expenses", "Cost Of Revenue"])
    fcf = pick(cf, ["Free Cash Flow"])

    rg, og = growth(rev), growth(opex)

    # Only trust FCF growth when last year's FCF was a real number to grow from.
    fcf_growth = None
    if fcf and len(fcf) >= 2 and rev and len(rev) >= 2:
        prior, prior_rev = fcf[1], rev[1]
        if prior > 0 and prior_rev and prior >= MIN_FCF_BASE_MARGIN * prior_rev:
            fcf_growth = (fcf[0] - prior) / prior * 100

    return {
        "sector": (info or {}).get("sector"),
        "industry": (info or {}).get("industry"),
        "market_cap": (info or {}).get("marketCap"),
        "assets_to_liabilities": (
            round(assets[0] / liabs[0], 3) if assets and liabs and liabs[0] else None
        ),
        "revenue_growth_pct": round(rg, 2) if rg is not None else None,
        "opex_growth_pct": round(og, 2) if og is not None else None,
        "growth_spread_pct": (
            round(rg - og, 2) if (rg is not None and og is not None) else None
        ),
        "fcf_latest": round(fcf[0], 0) if fcf else None,
        "fcf_growth_pct": round(fcf_growth, 2) if fcf_growth is not None else None,
        "fcf_margin_pct": (
            round(fcf[0] / rev[0] * 100, 2) if fcf and rev and rev[0] else None
        ),
    }


def rank_scores(rows: dict[str, dict]) -> dict[str, float | None]:
    """Average percentile rank across the components a symbol actually has.
    Rank-based so one wild raw number cannot dominate the composite."""
    ranks: dict[str, dict[str, float]] = {s: {} for s in rows}
    for comp in COMPONENTS:
        cap = CLIP.get(comp)
        have = []
        for s, r in rows.items():
            v = r.get(comp)
            if v is None:
                continue
            have.append((s, max(-cap, min(cap, v)) if cap is not None else v))
        have.sort(key=lambda t: t[1])
        n = max(1, len(have) - 1)
        for pos, (s, _) in enumerate(have):
            ranks[s][comp] = pos / n
    return {
        s: (round(statistics.mean(v.values()), 4) if len(v) >= 3 else None)
        for s, v in ranks.items()
    }


def pick_diversified(scored: dict[str, dict], n: int,
                     sector_cap: int, industry_cap: int) -> list[str]:
    """
    Best scores first, subject to both a per-sector and a per-industry cap.

    If the caps leave the roster short — the liquid pool simply may not hold
    enough distinct industries — they are loosened together a name at a time
    rather than returning a half-empty roster. The printed mix shows where it
    actually settled.
    """
    order = sorted(scored, key=lambda s: -scored[s]["score"])
    while True:
        picks, by_sector, by_industry = [], {}, {}
        for s in order:
            sec = scored[s].get("sector") or "Unknown"
            ind = scored[s].get("industry") or "Unknown"
            if by_sector.get(sec, 0) >= sector_cap:
                continue
            if by_industry.get(ind, 0) >= industry_cap:
                continue
            picks.append(s)
            by_sector[sec] = by_sector.get(sec, 0) + 1
            by_industry[ind] = by_industry.get(ind, 0) + 1
            if len(picks) == n:
                return picks
        if len(picks) == len(order) or sector_cap > n:
            return picks
        sector_cap += 1
        industry_cap += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", type=int, default=600,
                    help="how many liquid names to pull fundamentals for")
    ap.add_argument("--targets", type=int, default=38,
                    help="screened names to keep, on top of the pinned ETFs")
    ap.add_argument("--dry-run", action="store_true", help="print, do not write")
    args = ap.parse_args()

    print("1. tradable pool from Alpaca")
    pool = tradable_pool()
    print(f"   {len(pool):,} common shares on {'/'.join(sorted(REAL_EXCHANGES))}")

    print("2. liquidity")
    liq = liquidity(sorted(pool))
    liquid = {
        s: v for s, v in liq.items()
        if v["dollar_volume"] >= MIN_DOLLAR_VOLUME and v["last_close"] >= MIN_PRICE
    }
    print(f"   {len(liquid):,} clear ${MIN_DOLLAR_VOLUME/1e6:.0f}M/day and "
          f"${MIN_PRICE:.0f}/share")

    ranked = sorted(liquid, key=lambda s: -liquid[s]["dollar_volume"])[:args.pool]
    print(f"3. fundamentals for the top {len(ranked)} by dollar volume "
          f"(~{len(ranked) * 0.7 / 60:.0f} min)")

    rows, failed = {}, 0
    for i, sym in enumerate(ranked, 1):
        f = fundamentals(sym)
        if f is None:
            failed += 1
            continue
        rows[sym] = {**f, **liquid[sym], **pool[sym]}
        if i % 50 == 0:
            print(f"   {i}/{len(ranked)}  ({failed} without usable data)")
    print(f"   {len(rows)} scored, {failed} skipped")

    scores = rank_scores(rows)
    scored = {s: r for s, r in rows.items() if scores[s] is not None}
    for s in scored:
        scored[s]["score"] = scores[s]

    picks = pick_diversified(scored, args.targets, MAX_PER_SECTOR, MAX_PER_INDUSTRY)
    targets = PINNED + [s for s in picks if s not in PINNED]

    print(f"\n{'sym':<7}{'score':>6}{'A/L':>7}{'spread':>8}{'FCF g%':>8}"
          f"{'FCF m%':>8}{'$vol M':>8}  {'sector':<24}industry")
    for s in picks:
        r = scored[s]
        f = lambda v, w, d=1: (f"{v:>{w}.{d}f}" if v is not None else f"{'-':>{w}}")
        print(f"{s:<7}{r['score']:>6.2f}{f(r['assets_to_liabilities'],7,2)}"
              f"{f(r['growth_spread_pct'],8)}{f(r['fcf_growth_pct'],8)}"
              f"{f(r['fcf_margin_pct'],8)}{r['dollar_volume']/1e6:>8.0f}  "
              f"{(r['sector'] or '-')[:23]:<24}{r['industry'] or '-'}")

    shortable = sum(1 for s in picks if scored[s]["shortable"])
    sectors = {}
    for s in picks:
        sectors[scored[s]["sector"] or "?"] = sectors.get(scored[s]["sector"] or "?", 0) + 1
    print(f"\n{len(targets)} targets ({len(PINNED)} pinned + {len(picks)} screened), "
          f"{shortable} shortable")
    print("sector mix: " + "  ".join(f"{k}:{v}" for k, v in
                                     sorted(sectors.items(), key=lambda x: -x[1])))

    payload = {
        "screened_at": datetime.now(timezone.utc).isoformat(),
        "pool_considered": len(ranked),
        "liquidity_floor_usd": MIN_DOLLAR_VOLUME,
        "components": COMPONENTS,
        "pinned": PINNED,
        "targets": targets,
        "detail": {s: scored[s] for s in picks},
    }
    if args.dry_run:
        print("\n--dry-run: not written")
        return 0
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
