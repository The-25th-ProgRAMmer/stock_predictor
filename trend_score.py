"""
Four-timeframe trend score, -4 to +4.

Compare the current price against its own price 1 week, 2 weeks, 1 month and
2 months back. Each comparison contributes +1 if the current price is higher,
-1 if lower. Four comparisons therefore land on exactly one of five rungs:

    +4  all four higher    -> strong uptrend     -> full long
    +2  three higher       -> moderate uptrend   -> half long
     0  two and two        -> neutral            -> no trade
    -2  three lower        -> moderate downtrend -> half short
    -4  all four lower     -> strong downtrend   -> full short

Reference price: the most recent close. The trade is placed at the next open,
but the score is computed from the same prior-close snapshot the prediction was
made from, so the whole decision is reproducible from one set of data. Pass
reference="open" to score against a live opening price instead.
"""

import json
from pathlib import Path

BARS_FILE = Path(__file__).parent / "data" / "bars.json"

# Trading days, not calendar days: ~5 sessions a week.
LOOKBACKS = {
    "1w": 5,
    "2w": 10,
    "1m": 21,
    "2m": 42,
}

# score -> (label, fraction of full size, side)
LADDER = {
    4: ("strong uptrend", 1.0, "long"),
    2: ("moderate uptrend", 0.5, "long"),
    0: ("neutral", 0.0, None),
    -2: ("moderate downtrend", 0.5, "short"),
    -4: ("strong downtrend", 1.0, "short"),
}


def load_bars(path: Path = BARS_FILE) -> dict[str, list[dict]]:
    with open(path) as f:
        return json.load(f)


def score_symbol(bars: list[dict], reference_price: float | None = None) -> dict:
    """
    Score one symbol. bars must be chronological, oldest first.
    reference_price overrides the latest close (e.g. a live opening print).
    """
    closes = [b["c"] for b in bars if b.get("c") is not None]
    needed = max(LOOKBACKS.values()) + 1
    if len(closes) < needed:
        return {
            "score": None,
            "error": f"need {needed} bars, have {len(closes)}",
            "components": {},
        }

    current = float(reference_price) if reference_price is not None else float(closes[-1])

    components = {}
    score = 0
    for name, n in LOOKBACKS.items():
        past = float(closes[-1 - n])
        higher = current > past
        step = 1 if higher else -1
        score += step
        components[name] = {
            "sessions_back": n,
            "past_close": round(past, 2),
            "higher_now": higher,
            "step": step,
            "pct_change": round((current - past) / past * 100, 2) if past else None,
        }

    label, fraction, side = LADDER[score]
    return {
        "score": score,
        "label": label,
        "side": side,
        "size_fraction": fraction,
        "reference_price": round(current, 2),
        "reference": "open" if reference_price is not None else "last_close",
        "components": components,
    }


def score_all(
    symbols: list[str],
    bars: dict[str, list[dict]] | None = None,
    reference_prices: dict[str, float] | None = None,
) -> dict[str, dict]:
    bars = bars if bars is not None else load_bars()
    reference_prices = reference_prices or {}
    out = {}
    for sym in symbols:
        if sym not in bars:
            out[sym] = {"score": None, "error": "no bar history", "components": {}}
            continue
        out[sym] = score_symbol(bars[sym], reference_prices.get(sym))
    return out


def main() -> None:
    from universe import TARGETS

    scores = score_all(TARGETS)
    print(f"{'symbol':<8}{'score':>6}  {'1w':>8}{'2w':>8}{'1m':>8}{'2m':>8}  "
          f"{'size':>6}  side    label")
    for sym, s in scores.items():
        if s["score"] is None:
            print(f"{sym:<8}{'n/a':>6}  {s['error']}")
            continue
        c = s["components"]
        print(
            f"{sym:<8}{s['score']:>+6}  "
            + "".join(f"{c[k]['pct_change']:>+8.2f}" for k in ("1w", "2w", "1m", "2m"))
            + f"  {s['size_fraction']:>6.1f}  {str(s['side'] or '-'):<6}  {s['label']}"
        )


if __name__ == "__main__":
    main()
