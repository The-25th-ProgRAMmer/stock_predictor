"""
Gather SPY market data, compute technical indicators, fetch news headlines,
and print a JSON context block on stdout for the LLM predictor to consume.

Zero-LLM: this file is deterministic data collection only.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import ssl
import numpy as np
import pandas as pd
import requests
import truststore
from dotenv import load_dotenv

truststore.inject_into_ssl()

load_dotenv()

API_KEY = os.environ["ALPACA_API_KEY"]
SECRET = os.environ["ALPACA_SECRET_KEY"]
HEADERS = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET}
DATA_BASE = "https://data.alpaca.markets"

SYMBOL = "SPY"
BAR_LOOKBACK_DAYS = 120


def fetch_bars(symbol: str, days: int) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    url = f"{DATA_BASE}/v2/stocks/{symbol}/bars"
    params = {
        "timeframe": "1Day",
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "limit": 1000,
        "adjustment": "all",
        "feed": "iex",
    }
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    bars = r.json().get("bars", [])
    if not bars:
        raise RuntimeError(f"No bars returned for {symbol}")
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"])
    return df.sort_values("t").reset_index(drop=True)


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).rolling(n).mean()
    loss = (-delta.clip(upper=0)).rolling(n).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


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


def fetch_news(symbol: str, limit: int = 10) -> list[dict]:
    url = f"{DATA_BASE}/v1beta1/news"
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=2)
    params = {
        "symbols": symbol,
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
    return [
        {
            "headline": n.get("headline"),
            "summary": (n.get("summary") or "")[:400],
            "source": n.get("source"),
            "url": n.get("url"),
            "created_at": n.get("created_at"),
        }
        for n in items
    ]


def next_trading_day(today: datetime) -> str:
    d = today + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def main() -> None:
    df = compute_indicators(fetch_bars(SYMBOL, BAR_LOOKBACK_DAYS))
    if len(df) < 51:
        print(
            json.dumps({"error": "not enough bars", "count": len(df)}),
            file=sys.stderr,
        )
        sys.exit(1)

    latest, prev = df.iloc[-1], df.iloc[-2]
    last_5 = df.tail(5)[["t", "o", "h", "l", "c", "v"]].to_dict("records")
    for row in last_5:
        row["t"] = row["t"].strftime("%Y-%m-%d")
        for k in ("o", "h", "l", "c"):
            row[k] = float(row[k])
        row["v"] = int(row["v"])

    context = {
        "symbol": SYMBOL,
        "as_of": latest["t"].strftime("%Y-%m-%d"),
        "for_date": next_trading_day(latest["t"].to_pydatetime()),
        "latest_close": float(latest["c"]),
        "prev_close": float(prev["c"]),
        "prev_day_direction": "up" if latest["c"] > prev["c"] else "down",
        "prev_day_pct": round(
            (float(latest["c"]) - float(prev["c"])) / float(prev["c"]) * 100, 4
        ),
        "indicators": {
            "sma20": float(latest["sma20"]),
            "sma50": float(latest["sma50"]),
            "close_vs_sma20_pct": round(
                (float(latest["c"]) - float(latest["sma20"]))
                / float(latest["sma20"])
                * 100,
                3,
            ),
            "close_vs_sma50_pct": round(
                (float(latest["c"]) - float(latest["sma50"]))
                / float(latest["sma50"])
                * 100,
                3,
            ),
            "sma20_vs_sma50": "above" if latest["sma20"] > latest["sma50"] else "below",
            "rsi14": round(float(latest["rsi14"]), 2),
            "macd": round(float(latest["macd"]), 4),
            "macd_signal": round(float(latest["macd_signal"]), 4),
            "macd_hist": round(float(latest["macd_hist"]), 4),
            "atr14": round(float(latest["atr14"]), 3),
            "atr14_pct_of_close": round(
                float(latest["atr14"]) / float(latest["c"]) * 100, 3
            ),
        },
        "last_5_days": last_5,
        "news": fetch_news(SYMBOL, limit=10),
    }
    print(json.dumps(context, indent=2))


if __name__ == "__main__":
    main()
