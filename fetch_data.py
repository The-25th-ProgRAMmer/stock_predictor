"""
Fetch the whole symbol universe from Alpaca in one call and commit it for the
routine to consume. Runs in GitHub Actions with Alpaca credentials from repo
secrets. Writes data/ files that predict.py and grade.py read (no network calls
in the routine).

Output is tiered on purpose:
  - targets      full indicator block + 3 recent bars  (the agent predicts these)
  - context      one compact line each                 (background only)
  - cross_asset  derived risk-on/risk-off features     (where the real signal is)

Raw levels of 25 symbols would swamp the routine's context window and tell the
agent little. Ratios, breadth counts and percentile ranks tell it a lot.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

# Both of these are for running this script BY HAND on a Windows box when the
# Actions workflow is down: truststore because some AV/proxy setups intercept
# TLS to Alpaca with a root certifi rejects, dotenv to pick up local
# credentials. On the Actions runner both are absent and unnecessary - the
# system roots work and the keys arrive as secrets. Same guard as broker.py.
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

import broker
import signals
from universe import ALL_SYMBOLS, CONTEXT, MAG7, SEMIS, TARGETS

API_KEY = os.environ.get("ALPACA_API_KEY", "")
SECRET = os.environ.get("ALPACA_SECRET_KEY", "")
HEADERS = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET}
DATA_BASE = "https://data.alpaca.markets"

BAR_LOOKBACK_DAYS = 120
ANCHOR = "SPY"  # the symbol whose last bar sets as_of / for_date


def fetch_all_bars(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    """One request for the whole universe. Returns {symbol: sorted DataFrame}."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    url = f"{DATA_BASE}/v2/stocks/bars"
    params = {
        "symbols": ",".join(symbols),
        "timeframe": "1Day",
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "limit": 10000,
        "adjustment": "all",
        "feed": "iex",
    }

    out: dict[str, pd.DataFrame] = {}
    page_token = None
    while True:
        if page_token:
            params["page_token"] = page_token
        r = requests.get(url, headers=HEADERS, params=params, timeout=60)
        r.raise_for_status()
        payload = r.json()
        for sym, bars in (payload.get("bars") or {}).items():
            df = pd.DataFrame(bars)
            out[sym] = pd.concat([out[sym], df]) if sym in out else df
        page_token = payload.get("next_page_token")
        if not page_token:
            break

    for sym, df in out.items():
        df["t"] = pd.to_datetime(df["t"])
        out[sym] = df.sort_values("t").drop_duplicates("t").reset_index(drop=True)
    return out


def drop_unfinished_session(raw: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """
    Drop today's bar while today's session has not closed. GitHub starts scheduled
    runs hours late, so this can run mid-session, and a partial daily bar would
    otherwise be read as a finished day and predicted from.
    """
    clk = broker.clock()
    now = datetime.fromisoformat(clk["timestamp"])
    if datetime.fromisoformat(clk["next_close"]).date() != now.date():
        return raw
    print(f"session {now.date()} has not closed - ignoring its partial bar")
    return {s: df[df["t"].dt.date < now.date()].reset_index(drop=True) for s, df in raw.items()}


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    # Wilder's smoothing, the standard RSI. A simple rolling mean was used here
    # before and overstated readings after a run - NVDA showed 83 against a
    # standard 63 - which fed a spurious "overbought" into the prediction.
    return signals.rsi(s, n)


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(s, fast) - ema(s, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    high, low, close = df["h"], df["l"], df["c"]
    prev = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev).abs(), (low - prev).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(n).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["sma20"] = sma(df["c"], 20)
    df["sma50"] = sma(df["c"], 50)
    df["rsi14"] = rsi(df["c"], 14)
    m, s, h = macd(df["c"])
    df["macd"], df["macd_signal"], df["macd_hist"] = m, s, h
    df["atr14"] = atr(df, 14)
    return df


def num(v, digits: int = 4):
    """JSON-safe float: NaN/inf become null rather than invalid JSON."""
    if v is None:
        return None
    f = float(v)
    return None if not np.isfinite(f) else round(f, digits)


def pct_change(series: pd.Series, periods: int = 1):
    if len(series) <= periods:
        return None
    a, b = float(series.iloc[-1]), float(series.iloc[-1 - periods])
    if not np.isfinite(a) or not np.isfinite(b) or b == 0:
        return None
    return round((a - b) / b * 100, 3)


def percentile_rank(series: pd.Series, window: int = 60):
    """Where the latest value sits within its own trailing window, 0-100."""
    s = series.dropna().tail(window)
    if len(s) < 10:
        return None
    return round((s < s.iloc[-1]).sum() / (len(s) - 1) * 100, 1)


def target_block(df: pd.DataFrame) -> dict:
    latest, prev = df.iloc[-1], df.iloc[-2]
    close, sma20, sma50 = float(latest["c"]), latest["sma20"], latest["sma50"]

    def vs(level):
        if level is None or not np.isfinite(float(level)) or float(level) == 0:
            return None
        return round((close - float(level)) / float(level) * 100, 3)

    recent = []
    for _, row in df.tail(3).iterrows():
        recent.append(
            {
                "t": row["t"].strftime("%Y-%m-%d"),
                "o": num(row["o"], 2),
                "h": num(row["h"], 2),
                "l": num(row["l"], 2),
                "c": num(row["c"], 2),
                "v": int(row["v"]),
            }
        )

    sma_cross = None
    if np.isfinite(float(sma20)) and np.isfinite(float(sma50)):
        sma_cross = "above" if sma20 > sma50 else "below"

    return {
        "latest_close": num(close, 2),
        "prev_close": num(prev["c"], 2),
        "prev_day_direction": "up" if close > float(prev["c"]) else "down",
        "prev_day_pct": pct_change(df["c"], 1),
        "pct_5d": pct_change(df["c"], 5),
        "pct_20d": pct_change(df["c"], 20),
        "indicators": {
            "sma20": num(sma20, 2),
            "sma50": num(sma50, 2),
            "close_vs_sma20_pct": vs(sma20),
            "close_vs_sma50_pct": vs(sma50),
            "sma20_vs_sma50": sma_cross,
            "rsi14": num(latest["rsi14"], 2),
            "macd_hist": num(latest["macd_hist"], 4),
            "atr14_pct_of_close": (
                None
                if not np.isfinite(float(latest["atr14"])) or close == 0
                else round(float(latest["atr14"]) / close * 100, 3)
            ),
        },
        "last_3_days": recent,
    }


def context_line(df: pd.DataFrame) -> dict:
    """One compact row. Context symbols get no indicator block of their own."""
    latest = df.iloc[-1]
    close, sma20 = float(latest["c"]), latest["sma20"]
    vs20 = None
    if np.isfinite(float(sma20)) and float(sma20) != 0:
        vs20 = round((close - float(sma20)) / float(sma20) * 100, 2)
    return {
        "close": num(close, 2),
        "pct_1d": pct_change(df["c"], 1),
        "pct_5d": pct_change(df["c"], 5),
        "vs_sma20_pct": vs20,
        "rsi14": num(latest["rsi14"], 1),
    }


def ratio_series(a: pd.DataFrame, b: pd.DataFrame) -> pd.Series:
    """Close-over-close ratio aligned on trading date."""
    sa = a.set_index(a["t"].dt.strftime("%Y-%m-%d"))["c"]
    sb = b.set_index(b["t"].dt.strftime("%Y-%m-%d"))["c"]
    joined = pd.concat([sa, sb], axis=1, join="inner").dropna()
    if joined.empty:
        return pd.Series(dtype=float)
    return joined.iloc[:, 0] / joined.iloc[:, 1]


def ratio_block(bars: dict[str, pd.DataFrame], a: str, b: str, note: str) -> dict | None:
    if a not in bars or b not in bars:
        return None
    r = ratio_series(bars[a], bars[b])
    if len(r) < 2:
        return None
    return {
        "pair": f"{a}/{b}",
        "value": num(r.iloc[-1], 4),
        "pct_1d": pct_change(r, 1),
        "pct_5d": pct_change(r, 5),
        "percentile_60d": percentile_rank(r, 60),
        "reads": note,
    }


def breadth(bars: dict[str, pd.DataFrame], symbols: list[str]) -> dict:
    up = above = counted = 0
    moves = []
    for s in symbols:
        df = bars.get(s)
        if df is None or len(df) < 2:
            continue
        counted += 1
        chg = pct_change(df["c"], 1)
        if chg is not None:
            moves.append(chg)
            if chg > 0:
                up += 1
        sma20 = df.iloc[-1]["sma20"]
        if np.isfinite(float(sma20)) and float(df.iloc[-1]["c"]) > float(sma20):
            above += 1
    return {
        "n": counted,
        "up_today": up,
        "above_sma20": above,
        "median_move_pct": num(np.median(moves), 3) if moves else None,
    }


def realized_vol(df: pd.DataFrame, window: int = 20):
    """Annualized realized volatility — the honest substitute for a VIX level."""
    rets = df["c"].pct_change().dropna().tail(window)
    if len(rets) < window // 2:
        return None
    return round(float(rets.std()) * np.sqrt(252) * 100, 2)


def correlation(a: pd.DataFrame, b: pd.DataFrame, window: int = 20):
    sa = a.set_index(a["t"].dt.strftime("%Y-%m-%d"))["c"].pct_change()
    sb = b.set_index(b["t"].dt.strftime("%Y-%m-%d"))["c"].pct_change()
    joined = pd.concat([sa, sb], axis=1, join="inner").dropna().tail(window)
    if len(joined) < window // 2:
        return None
    c = joined.iloc[:, 0].corr(joined.iloc[:, 1])
    return None if not np.isfinite(c) else round(float(c), 3)


def cross_asset(bars: dict[str, pd.DataFrame]) -> dict:
    """Derived risk-on/risk-off structure. This is the part worth reading."""
    out: dict = {}

    out["mag7_breadth"] = breadth(bars, MAG7)
    out["semi_breadth"] = breadth(bars, SEMIS)

    ratios = [
        ratio_block(bars, "HYG", "LQD", "credit risk appetite; rising = risk-on"),
        ratio_block(bars, "IWM", "SPY", "small-cap breadth; rising = broad participation"),
        ratio_block(bars, "GLD", "SLV", "rising = defensive within metals"),
        ratio_block(bars, "QQQ", "SPY", "rising = tech leadership, narrow market"),
    ]
    out["ratios"] = [r for r in ratios if r]

    if "VIXY" in bars:
        v = bars["VIXY"]
        out["volatility"] = {
            "VIXY_pct_1d": pct_change(v["c"], 1),
            "VIXY_pct_5d": pct_change(v["c"], 5),
            "VIXY_percentile_60d": percentile_rank(v["c"], 60),
            "note": "VIXY bleeds to contango - read the change and percentile, not the level",
        }
    if ANCHOR in bars:
        out.setdefault("volatility", {})["SPY_realized_vol_20d_annualized_pct"] = (
            realized_vol(bars[ANCHOR], 20)
        )

    if ANCHOR in bars:
        out["correlations_20d_vs_SPY"] = {
            s: correlation(bars[ANCHOR], bars[s])
            for s in ("TLT", "GLD", "HYG", "UUP")
            if s in bars
        }

    # Composite: how many independent cross-asset reads point risk-on today.
    checks: dict[str, bool | None] = {}

    def direction(sym: str, risk_on_when_up: bool):
        df = bars.get(sym)
        if df is None:
            return None
        chg = pct_change(df["c"], 1)
        if chg is None:
            return None
        return (chg > 0) if risk_on_when_up else (chg < 0)

    hyg_lqd = next((r for r in out["ratios"] if r["pair"] == "HYG/LQD"), None)
    iwm_spy = next((r for r in out["ratios"] if r["pair"] == "IWM/SPY"), None)
    checks["credit_risk_on"] = (
        None if not hyg_lqd or hyg_lqd["pct_1d"] is None else hyg_lqd["pct_1d"] > 0
    )
    checks["breadth_risk_on"] = (
        None if not iwm_spy or iwm_spy["pct_1d"] is None else iwm_spy["pct_1d"] > 0
    )
    checks["vol_risk_on"] = direction("VIXY", risk_on_when_up=False)
    checks["rates_risk_on"] = direction("TLT", risk_on_when_up=False)
    checks["gold_risk_on"] = direction("GLD", risk_on_when_up=False)

    scored = [v for v in checks.values() if v is not None]
    out["risk_on_score"] = {
        "score": sum(1 for v in scored if v),
        "of": len(scored),
        "components": checks,
        "reads": "count of cross-asset signals pointing risk-on today; 0-1 is risk-off, 4-5 risk-on",
    }
    return out


def fetch_news(symbols: list[str], limit: int = 40, per_symbol_cap: int = 3) -> list[dict]:
    url = f"{DATA_BASE}/v1beta1/news"
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=2)
    params = {
        "symbols": ",".join(symbols),
        "limit": limit,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sort": "desc",
    }
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=30)
        r.raise_for_status()
        items = r.json().get("news", [])
    except Exception as e:
        return [{"error": f"news fetch failed: {e}"}]

    # Cap per symbol so one noisy name cannot crowd out the rest.
    seen: dict[str, int] = {}
    out = []
    for n in items:
        tags = [s for s in (n.get("symbols") or []) if s in symbols]
        if tags and all(seen.get(s, 0) >= per_symbol_cap for s in tags):
            continue
        for s in tags:
            seen[s] = seen.get(s, 0) + 1
        out.append(
            {
                "symbols": tags,
                "headline": n.get("headline"),
                "summary": (n.get("summary") or "")[:250],
                "source": n.get("source"),
                "url": n.get("url"),
                "created_at": n.get("created_at"),
            }
        )
    return out[:25]


def next_trading_day(today: datetime) -> str:
    d = today + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def main() -> None:
    os.makedirs("data", exist_ok=True)

    raw = drop_unfinished_session(fetch_all_bars(ALL_SYMBOLS, BAR_LOOKBACK_DAYS))
    missing = [s for s in ALL_SYMBOLS if s not in raw]
    if ANCHOR not in raw:
        print(f"Error: no bars for anchor symbol {ANCHOR}", file=sys.stderr)
        sys.exit(1)

    bars = {s: compute_indicators(df) for s, df in raw.items()}

    anchor = bars[ANCHOR]
    if len(anchor) < 51:
        print(f"Error: not enough {ANCHOR} bars ({len(anchor)})", file=sys.stderr)
        sys.exit(1)

    as_of = anchor.iloc[-1]["t"]

    # A target with too little history cannot be scored fairly — drop it loudly
    # rather than feeding the agent half-formed indicators.
    usable_targets, thin = [], []
    for s in TARGETS:
        if s in bars and len(bars[s]) >= 51:
            usable_targets.append(s)
        else:
            thin.append(s)
    if not usable_targets:
        print("Error: no target has enough history", file=sys.stderr)
        sys.exit(1)

    context_out = {}
    for group, syms in CONTEXT.items():
        rows = {s: context_line(bars[s]) for s in syms if s in bars and len(bars[s]) >= 21}
        if rows:
            context_out[group] = rows

    news = fetch_news(usable_targets)

    # The call itself is computed here, deterministically, rather than left to
    # the LLM to weigh. See signals.py for the evidence behind the rule.
    target_data = {}
    for s in usable_targets:
        block = target_block(bars[s])
        block["signal"] = signals.decide(raw[s], raw[ANCHOR])
        block["material_news"] = signals.material_news(news, s)
        target_data[s] = block

    context = {
        "as_of": as_of.strftime("%Y-%m-%d"),
        "for_date": next_trading_day(as_of.to_pydatetime()),
        "anchor": ANCHOR,
        "targets": usable_targets,
        "skipped_targets": thin,
        "missing_symbols": missing,
        "target_data": target_data,
        "context_data": context_out,
        "cross_asset": cross_asset(bars),
        "news": news,
    }

    with open("data/market_context.json", "w") as f:
        json.dump(context, f, indent=2)
    print(
        f"Wrote data/market_context.json (as_of {context['as_of']}, "
        f"{len(usable_targets)} targets, {len(bars)} symbols)"
    )

    # Full OHLC history per symbol, for grading. Indicators are not needed here.
    history = {}
    for sym, df in bars.items():
        rows = []
        for _, row in df.iterrows():
            rows.append(
                {
                    "t": row["t"].strftime("%Y-%m-%d"),
                    "o": num(row["o"], 4),
                    "h": num(row["h"], 4),
                    "l": num(row["l"], 4),
                    "c": num(row["c"], 4),
                    "v": int(row["v"]),
                }
            )
        history[sym] = rows

    with open("data/bars.json", "w") as f:
        json.dump(history, f)
    print(f"Wrote data/bars.json ({len(history)} symbols)")

    if missing:
        print(f"Note: no data returned for {missing}")
    if thin:
        print(f"Note: targets skipped for thin history: {thin}")


if __name__ == "__main__":
    main()
