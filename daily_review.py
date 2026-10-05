"""
Daily review: summarize today's graded predictions, compare to baselines,
and highlight what went well and what went wrong.

Run after grade.py completes to see a quick daily summary.
"""

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import goal
from stats import load, score

OUT_DIR = Path(__file__).parent / "logs"


def main() -> None:
    records, graded = load()
    if not graded:
        print("No graded predictions yet.")
        print("\n".join(goal.progress_lines()))
        return

    # Today = the most recent distinct date in the log
    today = max(r["for_date"] for r in graded)
    today_graded = [r for r in graded if r["for_date"] == today]

    lines = [
        "=" * 78,
        f"DAILY REVIEW - {today}",
        "=" * 78,
        "",
        "OCTOBER GOAL:",
        *goal.progress_lines(),
        "",
        "TRADES CLOSED:",
        *goal.trade_lines(goal.round_trips(since=today), f"Since {today}"),
        *goal.trade_lines(goal.round_trips(since=goal.GOAL_START), "October to date"),
        "",
        "PREDICTIONS:",
    ]

    # Score today
    today_score = score(today_graded)
    lines.extend([
        f"Predictions graded today: {today_score['n']}",
        f"Correct: {today_score['correct']}/{today_score['n']} = {today_score['accuracy']:.0%}",
        f"Baseline 'always up': {today_score['always_up']:.0%}",
        f"Your edge vs always_up: {today_score['edge_vs_always_up']:+.0%}",
        "",
    ])

    # Correct vs wrong
    correct = [r for r in today_graded if r["outcome"]["correct"]]
    wrong = [r for r in today_graded if not r["outcome"]["correct"]]

    if correct:
        lines.append(f"CORRECT ({len(correct)}):")
        for r in sorted(correct, key=lambda x: x["symbol"]):
            p, o = r["prediction"], r["outcome"]
            lines.append(
                f"  {r['symbol']:<6} called {p['direction']:<4}@{p['confidence']:>2}%  "
                f"-> {o['actual_direction']:<4} ({o['actual_pct']:+.2f}%)"
            )
            lines.append(f"    {p['reasoning'][:80]}")
        lines.append("")

    if wrong:
        lines.append(f"WRONG ({len(wrong)}):")
        for r in sorted(wrong, key=lambda x: x["symbol"]):
            p, o = r["prediction"], r["outcome"]
            lines.append(
                f"  {r['symbol']:<6} called {p['direction']:<4}@{p['confidence']:>2}%  "
                f"-> {o['actual_direction']:<4} ({o['actual_pct']:+.2f}%)"
            )
            lines.append(f"    Signals: {', '.join(p.get('key_signals', [])[:3])}")
            lines.append(f"    {p['reasoning'][:80]}")
        lines.append("")

    # What went well
    lines.append("WHAT WENT WELL:")
    if correct:
        # Find signals in correct predictions
        signal_hits = defaultdict(int)
        for r in correct:
            for sig in r["prediction"].get("key_signals", []):
                signal_hits[sig] += 1
        if signal_hits:
            top_signals = sorted(signal_hits.items(), key=lambda x: -x[1])[:3]
            lines.append(f"  Most reliable signals today:")
            for sig, count in top_signals:
                lines.append(f"    - {sig}: {count}/{len(correct)} correct predictions")
        else:
            lines.append("  (No specific signals identified)")
    else:
        lines.append("  (No correct predictions to analyze)")
    lines.append("")

    # What went wrong
    lines.append("WHAT WENT WRONG:")
    if wrong:
        # Find signals in wrong predictions
        signal_misses = defaultdict(int)
        for r in wrong:
            for sig in r["prediction"].get("key_signals", []):
                signal_misses[sig] += 1
        if signal_misses:
            top_misses = sorted(signal_misses.items(), key=lambda x: -x[1])[:3]
            lines.append(f"  Signals that led to misses:")
            for sig, count in top_misses:
                lines.append(f"    - {sig}: {count}/{len(wrong)} wrong predictions")
        # Biggest magnitude misses
        biggest_misses = sorted(wrong, key=lambda x: abs(x["outcome"]["actual_pct"]))[-2:]
        if biggest_misses:
            lines.append(f"  Biggest magnitude misses:")
            for r in biggest_misses:
                p, o = r["prediction"], r["outcome"]
                lines.append(
                    f"    - {r['symbol']}: called {p['direction']}, "
                    f"actual {o['actual_direction']} ({o['actual_pct']:+.2f}%)"
                )
    else:
        lines.append("  (No wrong predictions today - excellent!)")
    lines.append("")

    lines.append("=" * 78)

    # Write to file
    OUT_DIR.mkdir(exist_ok=True)
    out_path = OUT_DIR / f"review_{today}.txt"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nDaily review written to {out_path.name}")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
