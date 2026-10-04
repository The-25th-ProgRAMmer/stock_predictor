"""
Generate a plain-text end-of-week analysis file at logs/week_YYYY-MM-DD.txt
summarizing this week's predictions vs baselines, per-symbol breakdown,
calibration, and misses.

Meant to be run on Fridays after the daily predict + grade cycle.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import goal
from stats import (
    GRADUATION_MIN_PER_SYMBOL,
    by_symbol,
    calibration,
    herding,
    load,
    score,
    symbol_table,
    warnings,
)

OUT_DIR = Path(__file__).parent / "logs"


def score_lines(s: dict | None, label: str) -> list[str]:
    if not s:
        return [f"  {label}: no data"]
    return [
        f"  {label} (n={s['n']} over {s['days']} trading days):",
        f"    Agent accuracy:          {s['correct']}/{s['n']} = {s['accuracy']:.1%}",
        f"    Baseline 'always up':    {s['always_up']:.1%}",
        f"    Baseline 'prev day':     {s['prev_day']:.1%}",
        f"    Agent vs always_up:      {s['edge_vs_always_up']:+.1%}",
        f"    Agent vs prev_day:       {s['edge_vs_prev_day']:+.1%}",
        f"    Agent up-rate:           {s['up_rate']:.1%}",
    ]


def main() -> None:
    records, graded = load()
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    out_path = OUT_DIR / f"week_{today}.txt"

    lines = [
        "=" * 78,
        f"WEEKLY TRADING PREDICTOR ANALYSIS — Week ending {today}",
        "=" * 78,
        "",
        f"Total predictions logged: {len(records)}",
        f"Graded so far:            {len(graded)}",
        "",
        "--- OCTOBER GOAL ---",
        *goal.progress_lines(),
        "",
        "--- TRADES CLOSED THIS WEEK (what actually made or lost money) ---",
        *goal.trade_lines(goal.round_trips(since=week_start), "This week"),
        *goal.trade_lines(goal.round_trips(since=goal.GOAL_START), "October to date"),
        "",
    ]

    if not graded:
        lines.append("No graded predictions yet — nothing to analyze.")
        OUT_DIR.mkdir(exist_ok=True)
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Wrote {out_path}")
        return

    # This week = the last 5 distinct trading days covered by the log.
    week_dates = sorted({r["for_date"] for r in graded})[-5:]
    this_week = [r for r in graded if r["for_date"] in week_dates]

    lines.append("--- THIS WEEK (pooled) ---")
    lines.extend(score_lines(score(this_week), "This week"))
    lines.append("")
    lines.append("--- ALL TIME (pooled) ---")
    lines.extend(score_lines(score(graded), "All-time"))
    lines.append("")

    lines.append("--- PER SYMBOL, ALL TIME (the number that counts) ---")
    lines.extend(symbol_table(by_symbol(graded)))
    lines.append("")

    week_symbols = by_symbol(this_week)
    if len(week_symbols) > 1:
        lines.append("--- PER SYMBOL, THIS WEEK ---")
        lines.extend(symbol_table(week_symbols))
        lines.append("")

    lines.append("--- CALIBRATION (all-time, pooled) ---")
    lines.append(f"  {'bucket':>10}  {'n':>4}  {'accuracy':>10}")
    for b, (c, n) in calibration(graded).items():
        lines.append(f"  {b:>4}-{b+9:>3}%  {n:>4}  {c/n:>9.1%}")
    lines.append("")

    herd = herding(graded)
    if herd["unanimous_rate"] is not None:
        lines.append("--- INDEPENDENCE CHECK ---")
        lines.append(
            f"  Agent called every symbol the same direction on "
            f"{herd['unanimous_days']}/{herd['days']} multi-symbol days "
            f"({herd['unanimous_rate']:.0%})."
        )
        lines.append(
            "  High values mean the extra targets are echoing one market call, "
            "not adding independent tests."
        )
        lines.append("")

    lines.append("--- THIS WEEK'S PREDICTIONS ---")
    for r in sorted(this_week, key=lambda x: (x["for_date"], x["symbol"])):
        o, p = r["outcome"], r["prediction"]
        mark = "CORRECT" if o["correct"] else "WRONG"
        lines.append(
            f"  {r['for_date']}  {r['symbol']:<6} pred:{p['direction']:<4}@{p['confidence']}%  "
            f"actual:{o['actual_direction']:<4} ({o['actual_pct']:+.2f}%)  [{mark}]"
        )
        lines.append(f"    Reasoning: {p['reasoning']}")
    lines.append("")

    misses = [r for r in this_week if not r["outcome"]["correct"]]
    if misses:
        lines.append(f"--- MISSES THIS WEEK ({len(misses)}) ---")
        for r in sorted(misses, key=lambda x: (x["for_date"], x["symbol"])):
            o, p = r["outcome"], r["prediction"]
            lines.append(
                f"  {r['for_date']} {r['symbol']}: predicted {p['direction']}@{p['confidence']}%, "
                f"actual {o['actual_direction']} ({o['actual_pct']:+.2f}%)"
            )
            lines.append(f"    Signals cited: {', '.join(p['key_signals'])}")
        lines.append("")

    warns = warnings(graded, score(graded), herd)
    if warns:
        lines.append("--- CAVEATS ---")
        for w in warns:
            lines.append(f"  ! {w}")
        lines.append("")

    lines.append("=" * 78)
    lines.append(
        f"Note: the bar is {GRADUATION_MIN_PER_SYMBOL}+ graded predictions PER SYMBOL,"
    )
    lines.append("      not pooled across symbols. Pooled counts flatter the sample.")
    lines.append("=" * 78)

    OUT_DIR.mkdir(exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
