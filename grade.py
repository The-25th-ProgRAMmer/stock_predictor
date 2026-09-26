"""
Grade any un-graded predictions in logs/predictions.jsonl by comparing
the predicted direction to the actual close on the prediction's for_date.
Reads bar history from data/spy_bars.json (pre-fetched, no network calls).
"""

import json
from pathlib import Path

LOG = Path(__file__).parent / "logs" / "predictions.jsonl"
BARS_FILE = Path(__file__).parent / "data" / "spy_bars.json"


def bars_around(target_date: str) -> dict[str, dict]:
    if not BARS_FILE.exists():
        raise FileNotFoundError(f"{BARS_FILE} not found")

    with open(BARS_FILE) as f:
        bars = json.load(f)

    bars_dict = {bar["t"]: bar for bar in bars}
    return bars_dict


def main() -> None:
    if not LOG.exists():
        print("No prediction log yet — nothing to grade.")
        return

    lines = [l for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    graded_count = 0

    try:
        bars = bars_around("")
    except FileNotFoundError:
        print("No bar data available (data/spy_bars.json not found)")
        return

    for i, line in enumerate(lines):
        rec = json.loads(line)
        if rec.get("outcome") is not None:
            continue

        for_date = rec["for_date"]
        target = bars.get(for_date)
        if target is None:
            print(f"  skip {for_date}: no bar in history (market not closed?)")
            continue

        dates = sorted(bars.keys())
        try:
            idx = dates.index(for_date)
        except ValueError:
            print(f"  skip {for_date}: not in bar history")
            continue

        if idx == 0:
            print(f"  skip {for_date}: no prior bar found")
            continue
        prev = bars[dates[idx - 1]]

        actual_dir = "up" if target["c"] > prev["c"] else "down"
        pct = (target["c"] - prev["c"]) / prev["c"] * 100
        pred_dir = rec["prediction"]["direction"]

        from datetime import datetime, timezone
        rec["outcome"] = {
            "actual_close": target["c"],
            "prev_close": prev["c"],
            "actual_direction": actual_dir,
            "actual_pct": round(pct, 4),
            "correct": actual_dir == pred_dir,
            "always_up_correct": actual_dir == "up",
            "prev_day_baseline_correct": actual_dir
            == rec["baselines"]["prev_day_direction"],
            "graded_at": datetime.now(timezone.utc).isoformat(),
        }
        lines[i] = json.dumps(rec)
        graded_count += 1
        mark = "OK" if rec["outcome"]["correct"] else "X "
        print(
            f"  {for_date}  pred:{pred_dir}@{rec['prediction']['confidence']}%  "
            f"actual:{actual_dir} ({pct:+.2f}%)  {mark}"
        )

    if graded_count:
        LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Graded {graded_count} prediction(s).")


if __name__ == "__main__":
    main()
