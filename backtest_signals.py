"""
How well does each signal component call the NEXT day's direction?

Fetches ~3 years of daily bars, computes every component with signals.py (the
same code the live predictor runs), and scores each vote against the next
session's close-to-close direction.

The comparison that matters is not raw hit rate but hit rate against
ALWAYS-UP on the same rows: in a rising market almost any bullish vote looks
good. `edge` is vote hit rate minus the up-rate of the rows it voted on.

Split in time: the first two years choose the weights, the final year checks
they hold. Anything that only works in-sample is noise.

    python backtest_signals.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import signals
from universe import TARGETS

DATA_BASE = "https://data.alpaca.markets"
HEADERS = {
    "APCA-API-KEY-ID": os.environ.get("ALPACA_API_KEY", ""),
    "APCA-API-SECRET-KEY": os.environ.get("ALPACA_SECRET_KEY", ""),
}
YEARS = 3


def fetch(symbols: list[str], years: int) -> dict[str, pd.DataFrame]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * years + 90)
    out: dict[str, list] = {}
    for i in range(0, len(symbols), 50):
        params = {
            "symbols": ",".join(symbols[i:i + 50]), "timeframe": "1Day",
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
            p = r.json()
            for s, bars in (p.get("bars") or {}).items():
                out.setdefault(s, []).extend(bars)
            token = p.get("next_page_token")
            if not token:
                break
    frames = {}
    for s, bars in out.items():
        df = pd.DataFrame(bars)
        df["t"] = pd.to_datetime(df["t"]).dt.strftime("%Y-%m-%d")
        frames[s] = df.drop_duplicates("t").sort_values("t").reset_index(drop=True)
    return frames


def panel(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    reg = signals.regime(frames["SPY"])
    rows = []
    for s, df in frames.items():
        comp = signals.components(df)
        comp["symbol"] = s
        comp["next_up"] = (df["c"].shift(-1) > df["c"]).astype(float)
        comp.loc[df["c"].shift(-1).isna(), "next_up"] = np.nan
        comp = comp.merge(reg, on="t", how="left")
        rows.append(comp.iloc[60:])          # let every average warm up
    p = pd.concat(rows, ignore_index=True).dropna(subset=["next_up"])
    p["v_regime"] = p["v_regime"].fillna(0).astype(int)
    return p


def score_votes(p: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    res = []
    for col in cols:
        m = p[col] != 0
        n = int(m.sum())
        if not n:
            continue
        hit = ((p.loc[m, col] > 0) == (p.loc[m, "next_up"] == 1)).mean()
        up = p.loc[m, "next_up"].mean()
        bull = p[col] > 0
        bear = p[col] < 0
        res.append({
            "component": col[2:],
            "coverage": m.mean(),
            "n": n,
            "hit": hit,
            "up_rate": up,
            "edge": hit - up,
            "up_after_bull": p.loc[bull, "next_up"].mean() if bull.any() else np.nan,
            "up_after_bear": p.loc[bear, "next_up"].mean() if bear.any() else np.nan,
        })
    r = pd.DataFrame(res)
    r["spread"] = r["up_after_bull"] - r["up_after_bear"]
    return r


def show(title: str, r: pd.DataFrame) -> None:
    print(f"\n{title}")
    print(f"  {'component':<16}{'cover':>7}{'n':>8}{'hit':>7}{'always-up':>11}"
          f"{'edge':>7}{'P(up|bull)':>12}{'P(up|bear)':>12}{'spread':>8}")
    for _, x in r.sort_values("spread", ascending=False).iterrows():
        print(f"  {x['component']:<16}{x['coverage']:>7.0%}{x['n']:>8,}{x['hit']:>7.1%}"
              f"{x['up_rate']:>11.1%}{x['edge']:>+7.1%}{x['up_after_bull']:>12.1%}"
              f"{x['up_after_bear']:>12.1%}{x['spread']:>+8.1%}")


def main() -> int:
    screened = []
    uni = Path(__file__).parent / "data" / "universe.json"
    if uni.exists():
        screened = json.loads(uni.read_text(encoding="utf-8"))["targets"]
    symbols = sorted(set(TARGETS) | set(screened) | {"SPY"})
    print(f"fetching {YEARS}y of daily bars for {len(symbols)} symbols")
    frames = fetch(symbols, YEARS)
    p = panel(frames)
    cut = sorted(p["t"].unique())
    split = cut[int(len(cut) * 2 / 3)]
    train, test = p[p["t"] < split], p[p["t"] >= split]
    print(f"{len(p):,} symbol-days; train < {split} ({len(train):,}), "
          f"test >= {split} ({len(test):,})")
    print(f"base up-rate: train {train['next_up'].mean():.1%}, "
          f"test {test['next_up'].mean():.1%}")

    cols = [c for c in p.columns if c.startswith("v_")]
    show("TRAIN (first two years)", score_votes(train, cols))
    show("TEST (final year, untouched)", score_votes(test, cols))

    if len(sys.argv) > 1:
        p.to_pickle(sys.argv[1])
        print(f"\npanel saved to {sys.argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
