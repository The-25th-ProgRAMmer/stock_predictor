"""
Read logs/predictions.jsonl and print accuracy vs. baselines,
calibration by confidence bucket, and the most recent 10 predictions.
"""

import json
from collections import defaultdict
from pathlib import Path

LOG = Path(__file__).parent / "logs" / "predictions.jsonl"


def main() -> None:
    if not LOG.exists():
        print("No predictions yet.")
        return

    records = [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    graded = [r for r in records if r.get("outcome")]

    print(f"Total predictions logged: {len(records)}")
    print(f"Graded so far:            {len(graded)}")
    if not graded:
        return

    total = len(graded)
    correct = sum(1 for r in graded if r["outcome"]["correct"])
    always_up = sum(1 for r in graded if r["outcome"]["always_up_correct"])
    prev_day = sum(1 for r in graded if r["outcome"]["prev_day_baseline_correct"])

    print()
    print(f"Agent accuracy:          {correct}/{total} = {correct/total:.1%}")
    print(f"Baseline 'always up':    {always_up}/{total} = {always_up/total:.1%}")
    print(f"Baseline 'prev day':     {prev_day}/{total} = {prev_day/total:.1%}")

    buckets = defaultdict(lambda: [0, 0])
    for r in graded:
        b = (int(r["prediction"]["confidence"]) // 10) * 10
        buckets[b][1] += 1
        if r["outcome"]["correct"]:
            buckets[b][0] += 1

    print("\nCalibration by confidence bucket:")
    print(f"  {'bucket':>10}  {'n':>4}  {'accuracy':>10}")
    for b in sorted(buckets):
        c, n = buckets[b]
        print(f"  {b:>4}-{b+9:>3}%  {n:>4}  {c/n:>9.1%}")

    print("\nMost recent 10 graded predictions:")
    for r in graded[-10:]:
        o, p = r["outcome"], r["prediction"]
        mark = "OK" if o["correct"] else "X "
        print(
            f"  {r['for_date']}  pred:{p['direction']}@{p['confidence']}%  "
            f"actual:{o['actual_direction']} ({o['actual_pct']:+.2f}%)  {mark}"
        )


if __name__ == "__main__":
    main()
