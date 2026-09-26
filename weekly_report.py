"""
Generate a plain-text end-of-week analysis file at logs/week_YYYY-MM-DD.txt
summarizing this week's predictions vs baselines, calibration, and misses.

Meant to be run on Fridays after the daily predict + grade cycle.
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

LOG = Path(__file__).parent / "logs" / "predictions.jsonl"
OUT_DIR = Path(__file__).parent / "logs"


def main() -> None:
    if not LOG.exists():
        print("No prediction log yet.")
        return

    records = [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    graded = [r for r in records if r.get("outcome")]

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = OUT_DIR / f"week_{today}.txt"

    lines = []
    lines.append("=" * 70)
    lines.append(f"WEEKLY TRADING PREDICTOR ANALYSIS — Week ending {today}")
    lines.append("=" * 70)
    lines.append("")

    lines.append(f"Total predictions logged: {len(records)}")
    lines.append(f"Graded so far:            {len(graded)}")
    lines.append("")

    if not graded:
        lines.append("No graded predictions yet — nothing to analyze.")
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Wrote {out_path}")
        return

    # This week = last 5 graded (rough proxy for one trading week)
    this_week = graded[-5:] if len(graded) >= 5 else graded

    def stats(subset, label):
        n = len(subset)
        if n == 0:
            return [f"  {label}: no data"]
        correct = sum(1 for r in subset if r["outcome"]["correct"])
        always_up = sum(1 for r in subset if r["outcome"]["always_up_correct"])
        prev_day = sum(1 for r in subset if r["outcome"]["prev_day_baseline_correct"])
        return [
            f"  {label} (n={n}):",
            f"    Agent accuracy:          {correct}/{n} = {correct/n:.1%}",
            f"    Baseline 'always up':    {always_up}/{n} = {always_up/n:.1%}",
            f"    Baseline 'prev day':     {prev_day}/{n} = {prev_day/n:.1%}",
            f"    Agent vs always_up:      {(correct-always_up)/n:+.1%}",
            f"    Agent vs prev_day:       {(correct-prev_day)/n:+.1%}",
        ]

    lines.append("--- THIS WEEK ---")
    lines.extend(stats(this_week, "This week"))
    lines.append("")
    lines.append("--- ALL TIME ---")
    lines.extend(stats(graded, "All-time"))
    lines.append("")

    # Calibration
    buckets = defaultdict(lambda: [0, 0])
    for r in graded:
        b = (int(r["prediction"]["confidence"]) // 10) * 10
        buckets[b][1] += 1
        if r["outcome"]["correct"]:
            buckets[b][0] += 1

    lines.append("--- CALIBRATION (all-time) ---")
    lines.append(f"  {'bucket':>10}  {'n':>4}  {'accuracy':>10}")
    for b in sorted(buckets):
        c, n = buckets[b]
        lines.append(f"  {b:>4}-{b+9:>3}%  {n:>4}  {c/n:>9.1%}")
    lines.append("")

    # This week's predictions detail
    lines.append("--- THIS WEEK'S PREDICTIONS ---")
    for r in this_week:
        o, p = r["outcome"], r["prediction"]
        mark = "CORRECT" if o["correct"] else "WRONG"
        lines.append(f"  {r['for_date']}  pred:{p['direction']}@{p['confidence']}%  actual:{o['actual_direction']} ({o['actual_pct']:+.2f}%)  [{mark}]")
        lines.append(f"    Reasoning: {p['reasoning']}")
    lines.append("")

    # Misses only (educational)
    misses = [r for r in this_week if not r["outcome"]["correct"]]
    if misses:
        lines.append(f"--- MISSES THIS WEEK ({len(misses)}) ---")
        for r in misses:
            o, p = r["outcome"], r["prediction"]
            lines.append(f"  {r['for_date']}: predicted {p['direction']}@{p['confidence']}%, actual {o['actual_direction']} ({o['actual_pct']:+.2f}%)")
            lines.append(f"    Signals cited: {', '.join(p['key_signals'])}")
        lines.append("")

    lines.append("=" * 70)
    lines.append("Note: interpret results with sample size in mind.")
    lines.append("      <30 graded predictions is noise, not signal.")
    lines.append("=" * 70)

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
