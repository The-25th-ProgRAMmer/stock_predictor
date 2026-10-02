"""
Volatility-targeted position sizing.

    position size = score weight x (target risk % / annualised volatility %) x portfolio

  score weight   +-4 = 1.0 (full), +-2 = 0.5 (half), 0 = flat. Comes from trend_score.
  target risk    the % of the account you are genuinely fine losing on this
                 strategy. Picked once and held fixed.
  annualised     the average daily close-to-close % move over the last 30 days,
  volatility     multiplied by 19.1 (sqrt 365) to turn a daily number into a
                 yearly one.

The effect is that a quiet name gets a large notional and a violent one gets a
small notional, so each position contributes a comparable amount of risk rather
than a comparable amount of money.

Note on convention: this uses the MEAN ABSOLUTE daily move x sqrt(365), as
specified. The more common textbook definition uses the standard deviation of
returns x sqrt(252) (trading days). For a roughly normal return series the mean
absolute deviation is about 0.8 standard deviations, and sqrt(365)/sqrt(252) is
about 1.2, so the two land within ~5% of each other. They are not identical;
this file follows the specified formula.
"""

ANNUALISATION = 19.1  # sqrt(365), per the specified formula
VOL_WINDOW = 30       # trading sessions of close-to-close moves


def daily_moves(bars: list[dict], window: int = VOL_WINDOW) -> list[float]:
    """Absolute close-to-close percentage moves, most recent `window` of them."""
    closes = [b["c"] for b in bars if b.get("c") is not None]
    moves = []
    for i in range(1, len(closes)):
        prev = float(closes[i - 1])
        if prev:
            moves.append(abs((float(closes[i]) - prev) / prev * 100))
    return moves[-window:]


def annualised_vol(bars: list[dict], window: int = VOL_WINDOW) -> float | None:
    """Average daily % move over the window, annualised by sqrt(365)."""
    moves = daily_moves(bars, window)
    if len(moves) < window // 2:
        return None
    return sum(moves) / len(moves) * ANNUALISATION


def risk_notional(
    annual_vol_pct: float | None,
    target_risk_pct: float,
    portfolio_value: float,
) -> float | None:
    """
    The FULL-SIZE notional for this symbol, before the score weight is applied.
    Returns None when volatility is unknown or degenerate.
    """
    if not annual_vol_pct or annual_vol_pct <= 0:
        return None
    return (target_risk_pct / annual_vol_pct) * portfolio_value


def size_universe(
    symbols: list[str],
    bars: dict[str, list[dict]],
    target_risk_pct: float,
    portfolio_value: float,
    window: int = VOL_WINDOW,
) -> dict[str, dict]:
    """{symbol: {annual_vol_pct, full_notional}} for the whole universe."""
    out = {}
    for sym in symbols:
        b = bars.get(sym)
        vol = annualised_vol(b, window) if b else None
        out[sym] = {
            "annual_vol_pct": round(vol, 2) if vol else None,
            "full_notional": (
                round(n, 2)
                if (n := risk_notional(vol, target_risk_pct, portfolio_value))
                else None
            ),
            "avg_daily_move_pct": (
                round(vol / ANNUALISATION, 3) if vol else None
            ),
        }
    return out
