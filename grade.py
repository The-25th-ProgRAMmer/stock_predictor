"""
Grade any un-graded predictions in logs/predictions.jsonl by comparing the
predicted direction to the actual close on the prediction's for_date, for that
prediction's own symbol.

Reads bar history from data/bars.json (pre-fetched, no network calls).
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

LOG = Path(__file__).parent / "logs" / "predictions.jsonl"
BARS_FILE = Path(__file__).parent / "data" / "bars.json"


def load_bars() -> dict[str, dict[str, dict]]:
    """{symbol: {date: bar}} plus a sorted date list per symbol."""
    with open(BARS_FILE) as f:
        raw = json.load(f)
    return {sym: {bar["t"]: bar for bar in bars} for sym, bars in raw.items()}


def main() -> None:
    if not LOG.exists():
        print("No prediction log yet - nothing to grade.")
        return
    if not BARS_FILE.exists():
        print(f"No bar data available ({BARS_FILE.name} not found)")
        return

    bars = load_bars()
    sorted_dates = {sym: sorted(d) for sym, d in bars.items()}

    lines = [l for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    graded_count = 0
    results = Counter()

    for i, line in enumerate(lines):
        rec = json.loads(line)
        if rec.get("outcome") is not None:
            continue

        symbol, for_date = rec["symbol"], rec["for_date"]
        by_date = bars.get(symbol)
        if by_date is None:
            print(f"  skip {symbol} {for_date}: no bar history for this symbol")
            continue

        target = by_date.get(for_date)
        if target is None:
            print(f"  skip {symbol} {for_date}: no bar on that date (market not closed?)")
            continue

        dates = sorted_dates[symbol]
        idx = dates.index(for_date)
        if idx == 0:
            print(f"  skip {symbol} {for_date}: no prior bar in history")
            continue
        prev = by_date[dates[idx - 1]]

        actual_dir = "up" if target["c"] > prev["c"] else "down"
        pct = (target["c"] - prev["c"]) / prev["c"] * 100
        pred_dir = rec["prediction"]["direction"]
        correct = actual_dir == pred_dir

        rec["outcome"] = {
            "actual_close": target["c"],
            "prev_close": prev["c"],
            "actual_direction": actual_dir,
            "actual_pct": round(pct, 4),
            "correct": correct,
            "always_up_correct": actual_dir == "up",
            "prev_day_baseline_correct": actual_dir
            == rec["baselines"]["prev_day_direction"],
            "graded_at": datetime.now(timezone.utc).isoformat(),
        }
        lines[i] = json.dumps(rec)
        graded_count += 1
        results[correct] += 1
        mark = "OK" if correct else "X "
        print(
            f"  {for_date}  {symbol:<6} pred:{pred_dir:<4}@{rec['prediction']['confidence']}%  "
            f"actual:{actual_dir:<4} ({pct:+.2f}%)  {mark}"
        )

    if graded_count:
        LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(
            f"Graded {graded_count} prediction(s): "
            f"{results[True]} correct, {results[False]} wrong."
        )
    else:
        print("Graded 0 prediction(s).")


if __name__ == "__main__":
    main()
