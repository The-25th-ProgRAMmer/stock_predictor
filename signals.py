"""
Deterministic per-symbol signals and the daily call built from them.

Every function here is a pure transform of a bar DataFrame (columns t, o, h, l,
c, v; oldest first). The live predictor reads the LAST row; backtest_signals.py
reads every row. Both run this same code, so what was validated is exactly what
is used.

Why the call is computed here and not by the LLM
-------------------------------------------------
Left to weigh twenty indicators freely, the LLM flipped NVDA from "up 56%" to
"down 56%" on identical data: trend said up, RSI 83.9 said down, and nothing said
which wins. At a near-50% call, ordinary sampling variation decides the side.

Three years of bars across 48 names (backtest_signals.py) show why weighing them
cannot help. On next-day direction NO component beats "always up", and agreement
between them adds nothing: with all five trend-family votes bullish the next day
was up 53.3%, with all five bearish it was up 53.8%. Specifically:

  * Overbought RSI inside an uptrend - the NVDA case - was followed by an up day
    50.3% of the time. It is not a reversal signal and must not flip a call.
  * A heavy-volume move was followed by a REVERSAL more often than continuation
    (a heavy-volume down day led to an up day 55.4% of the time in training) and
    even that faded in the held-out year. "Volume confirms the move" is not
    supported, so volume is reported as evidence, never used to vote.
  * SMA trend alignment is the only component whose sign held in both periods,
    and only at a multi-day horizon (+2.3 / +2.7 points at 10 days).

So the rule is deliberately simple and fully reproducible: direction follows the
SMA trend, confidence is the measured hit rate (which is honest, and low), and
every other reading is shown as context rather than allowed to vote.
"""

import re

import numpy as np
import pandas as pd

# Measured next-day hit rates, rounded DOWN, from backtest_signals.py. A down
# call in a downtrend was right less than half the time (the next day was still
# up 52.4% / 51.2%), but confidence cannot go below 50, so it sits at the floor.
CONFIDENCE = {"up": 52, "down": 50}

# Overrides have no track record yet, so they get no extra conviction. Logged
# with method "news_override" so their accuracy can be measured on its own.
OVERRIDE_CONFIDENCE = 52

OVERBOUGHT, OVERSOLD = 70, 30
STRETCHED_ATR = 2.5        # this many ATRs from SMA20 counts as stretched
HEAVY_VOLUME = 1.5         # this multiple of 20-day average volume


def _sign(s: pd.Series) -> pd.Series:
    return np.sign(s).fillna(0).astype(int)


def rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["c"].shift(1)
    tr = pd.concat(
        [df["h"] - df["l"], (df["h"] - prev).abs(), (df["l"] - prev).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(n).mean()


def components(df: pd.DataFrame) -> pd.DataFrame:
    """
    One row per bar: each candidate vote in {-1, 0, +1}, plus the raw
    measurements behind them. A row only ever uses data up to its own close.
    """
    c, v = df["c"].astype(float), df["v"].astype(float)
    out = pd.DataFrame(index=df.index)
    out["t"] = df["t"]

    sma20 = c.rolling(20).mean()
    sma50 = c.rolling(50).mean()
    ret1 = c.pct_change()
    have50 = sma50.notna()

    out["vote_close_vs_sma20"] = _sign(c - sma20).where(sma20.notna(), 0)
    out["vote_close_vs_sma50"] = _sign(c - sma50).where(have50, 0)
    out["vote_sma20_vs_sma50"] = _sign(sma20 - sma50).where(have50, 0)
    out["v_trend"] = _sign(
        out["vote_close_vs_sma20"] + out["vote_close_vs_sma50"] + out["vote_sma20_vs_sma50"]
    )
    out["v_sma50_slope"] = _sign(sma50 / sma50.shift(10) - 1).where(have50, 0)

    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    out["v_macd"] = _sign(macd - macd.ewm(span=9, adjust=False).mean())
    out["v_mom20"] = _sign(c / c.shift(20) - 1)

    out["v_reversal1"] = -_sign(ret1)
    r = rsi(c)
    out["v_rsi_extreme"] = np.where(r > OVERBOUGHT, -1, np.where(r < OVERSOLD, 1, 0))

    relvol = v / v.rolling(20).mean()
    out["v_volume_confirm"] = np.where(relvol > HEAVY_VOLUME, _sign(ret1), 0)
    upv = v.where(ret1 > 0, 0).rolling(20).sum()
    dnv = v.where(ret1 < 0, 0).rolling(20).sum()
    out["v_accumulation"] = _sign(upv - dnv)
    obv = (v * _sign(ret1)).cumsum()
    out["v_obv_slope"] = _sign(obv - obv.shift(10))

    a = atr(df)
    out["close"] = c
    out["sma20"], out["sma50"] = sma20, sma50
    out["sma50_slope_10d_pct"] = (sma50 / sma50.shift(10) - 1) * 100
    out["rsi14"] = r
    out["atr14_pct"] = a / c * 100
    out["stretch_atr"] = (c - sma20) / a
    out["relvol20"] = relvol
    out["up_down_volume_ratio_20d"] = upv / dnv.replace(0, np.nan)
    out["ret1_pct"] = ret1 * 100
    return out


def regime(spy: pd.DataFrame) -> pd.DataFrame:
    """Market backdrop from SPY alone: above or below its 50-day average."""
    c = spy["c"].astype(float)
    sma50 = c.rolling(50).mean()
    return pd.DataFrame({
        "t": spy["t"],
        "v_regime": _sign(c - sma50).where(sma50.notna(), 0),
    })


def _r(x, d=2):
    return None if x is None or not np.isfinite(float(x)) else round(float(x), d)


def decide(df: pd.DataFrame, spy: pd.DataFrame | None = None) -> dict:
    """
    The day's call for one symbol, from its last bar. Same input, same output -
    there is no judgement in here to vary between runs.
    """
    row = components(df).iloc[-1]
    votes = {
        "close_vs_sma20": int(row["vote_close_vs_sma20"]),
        "close_vs_sma50": int(row["vote_close_vs_sma50"]),
        "sma20_vs_sma50": int(row["vote_sma20_vs_sma50"]),
    }
    net = sum(votes.values())
    # A tie (only possible with missing averages) falls back to the base rate.
    direction = "down" if net < 0 else "up"
    words = {1: "above", -1: "below", 0: "level with"}
    basis = (
        f"close {words[votes['close_vs_sma20']]} SMA20, "
        f"{words[votes['close_vs_sma50']]} SMA50; "
        f"SMA20 {words[votes['sma20_vs_sma50']]} SMA50 (net {net:+d} of 3)"
    )

    spy_state = None
    if spy is not None and len(spy) >= 50:
        s = spy["c"].astype(float)
        spy_state = "above_sma50" if s.iloc[-1] > s.rolling(50).mean().iloc[-1] else "below_sma50"

    rsi14, stretch, relvol = row["rsi14"], row["stretch_atr"], row["relvol20"]
    notes = []
    if np.isfinite(rsi14) and rsi14 > OVERBOUGHT:
        notes.append(
            f"RSI {rsi14:.1f} is overbought. Not a reversal signal: after RSI>75 "
            f"inside an uptrend the next day was up 50.3% of the time."
        )
    elif np.isfinite(rsi14) and rsi14 < OVERSOLD:
        notes.append(f"RSI {rsi14:.1f} is oversold. Shown for context; it does not vote.")
    if np.isfinite(stretch) and abs(stretch) > STRETCHED_ATR:
        notes.append(
            f"Price is {stretch:+.1f} ATRs from SMA20 - stretched. Context only."
        )
    if np.isfinite(relvol) and relvol > HEAVY_VOLUME:
        notes.append(
            f"Volume {relvol:.1f}x its 20-day average. Heavy volume has preceded "
            f"reversals slightly more often than continuation; context only."
        )

    return {
        "direction": direction,
        "confidence": CONFIDENCE[direction],
        "rule": "direction follows SMA trend alignment; tie -> up (base rate)",
        "basis": basis,
        "trend_votes": votes,
        "evidence": {
            "sma20": _r(row["sma20"]),
            "sma50": _r(row["sma50"]),
            "sma50_slope_10d_pct": _r(row["sma50_slope_10d_pct"], 3),
            "rsi14": _r(rsi14, 1),
            "stretch_atr_from_sma20": _r(stretch),
            "relative_volume_20d": _r(relvol),
            "up_down_volume_ratio_20d": _r(row["up_down_volume_ratio_20d"]),
            "obv_10d": {1: "rising", -1: "falling", 0: "flat"}[int(row["v_obv_slope"])],
            "macd_hist_sign": {1: "positive", -1: "negative", 0: "zero"}[int(row["v_macd"])],
            "pct_20d": _r(row["close"] / df["c"].astype(float).iloc[-21] * 100 - 100)
            if len(df) > 21 else None,
            "spy_regime": spy_state,
        },
        "notes": notes,
    }


# --------------------------------------------------------------------- news ---
#
# The LLM may override the computed call only on a MATERIAL, symbol-specific
# event. Materiality is decided here, by fixed rules, so it cannot drift between
# runs. The LLM only reads the flagged headlines and decides which way they cut.

MATERIAL_EVENTS = {
    "earnings": r"\b(earnings|eps|quarterly results|q[1-4] (results|revenue|sales)|"
                r"beats?|miss(es|ed)?|revenue (rose|fell|jumped|slumped))\b",
    "guidance": r"\b(guidance|outlook|forecast|raises (its )?(full[- ]year|annual)|"
                r"cuts (its )?(full[- ]year|annual|outlook))\b",
    "rating_change": r"\b(upgrade[sd]?|downgrade[sd]?|initiates? (coverage|at)|"
                     r"(raises|lowers|cuts|boosts) (its )?price target)\b",
    "deal": r"\b(acquir(e|es|ed|ing)|acquisition|merger|takeover|to buy|buyout|"
            r"divest|spin[- ]?off)\b",
    "regulatory_legal": r"\b(sec|ftc|doj|antitrust|lawsuit|sues?|sued|probe|"
                        r"investigation|fined?|fda|approval|approved|ban(s|ned)?|"
                        r"export (curbs|controls|restrictions)|tariffs? on)\b",
    "corporate": r"\b(ceo|cfo|resign(s|ed)?|steps? down|layoffs?|job cuts|"
                 r"buyback|repurchase|dividend|stock split|recall|delist)\b",
}

# Opinion, previews and round-ups are not events, however alarming. Two pieces
# exactly like this ("bull case exaggerated", "everybody's in trouble") are what
# tipped NVDA from up to down.
OPINION = re.compile(
    r"(\?$|^(why|how|what|is|are|should|could|can|will|here'?s)\b|"
    r"\b(opinion|could|might|may|says|warns?|thinks?|believes?|bull case|"
    r"bear case|exaggerated|bubble|stocks? to (watch|buy)|top picks|"
    r"live on cnbc|announces bought|announces sold|trade idea|"
    r"in trouble|what to expect|ahead of)\b)",
    re.IGNORECASE,
)

MAX_TAGS = 2   # a headline tagged with many tickers is a round-up, not an event


def classify_headline(item: dict, symbol: str) -> dict | None:
    """The material event a headline reports for `symbol`, or None."""
    tags = item.get("symbols") or []
    if symbol not in tags or len(tags) > MAX_TAGS:
        return None
    headline = (item.get("headline") or "").strip()
    if not headline or OPINION.search(headline):
        return None
    for event, pattern in MATERIAL_EVENTS.items():
        if re.search(pattern, headline, re.IGNORECASE):
            return {
                "event": event,
                "headline": headline,
                "url": item.get("url"),
                "created_at": item.get("created_at"),
            }
    return None


def material_news(news: list[dict], symbol: str) -> list[dict]:
    out = []
    for item in news:
        if "error" in item:
            continue
        hit = classify_headline(item, symbol)
        if hit:
            out.append(hit)
    return out
