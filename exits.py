"""
Where a position's take-profit and stop sit.

    distance % = MULTIPLE x (annualised vol % / 19.1)

Dividing the annualisation factor back out recovers the symbol's average daily
move, so the distance is expressed in days of typical movement for that name.
At MULTIPLE = 2, SPY needs about 1% and TSLA about 4.7%.

Why scale it that way: sizing.py already sizes positions so each one carries
comparable risk rather than comparable money. Measuring the exit distance in the
same volatility units makes each position pay or lose roughly the SAME NUMBER OF
DOLLARS when it resolves - near $105 at half weight, $209 at full - whatever the
symbol. A flat percentage would make TSLA exit on noise while SPY almost never
triggered.

The stop sits the same distance away as the target, so the average loser is the
same size as the average winner and the strategy needs a hit rate better than
50% rather than better than 2:1.
"""

from sizing import ANNUALISATION

MULTIPLE = 2.0  # in average daily moves


def distance_pct(annual_vol_pct: float | None, multiple: float = MULTIPLE) -> float | None:
    """How far the target and stop sit from entry, in percent. None if unknown."""
    if not annual_vol_pct or annual_vol_pct <= 0:
        return None
    return multiple * (annual_vol_pct / ANNUALISATION)


def bracket(
    entry_price: float,
    side: str,
    annual_vol_pct: float | None,
    multiple: float = MULTIPLE,
) -> dict | None:
    """
    Take-profit and stop prices for one position, rounded to whole cents, which
    is all Alpaca accepts above $1. Returns None when the distance cannot be
    computed or would collapse the two levels onto the same price.
    """
    d = distance_pct(annual_vol_pct, multiple)
    if d is None or entry_price <= 0:
        return None

    move = entry_price * d / 100
    if side == "long":
        tp, sl = entry_price + move, entry_price - move
    else:
        tp, sl = entry_price - move, entry_price + move

    tp, sl = round(tp, 2), round(sl, 2)
    if tp <= 0 or sl <= 0 or tp == sl:
        return None

    return {
        "take_profit": tp,
        "stop_loss": sl,
        "distance_pct": round(d, 3),
        "distance_dollars": round(move, 2),
        "multiple": multiple,
        "annual_vol_pct": round(annual_vol_pct, 2),
    }


def already_past(last_price: float, side: str, b: dict) -> str | None:
    """
    Which level the market has already reached, if either. A bracket cannot be
    placed in that state - Alpaca rejects a leg sitting on the wrong side of the
    market - so the caller closes the position at market instead.
    """
    tp, sl = b["take_profit"], b["stop_loss"]
    if side == "long":
        if last_price >= tp:
            return "target"
        if last_price <= sl:
            return "stop"
    else:
        if last_price <= tp:
            return "target"
        if last_price >= sl:
            return "stop"
    return None


def expected_pnl(qty: int, b: dict) -> float:
    """What hitting either level is worth, in dollars. Symmetric by design."""
    return round(qty * b["distance_dollars"], 2)
