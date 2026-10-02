"""
Read logs/predictions.jsonl and print per-symbol accuracy vs. baselines,
calibration by confidence bucket, and the most recent predictions.
"""

from stats import (
    by_symbol,
    calibration,
    herding,
    load,
    score,
    symbol_table,
    warnings,
)


def main() -> None:
    records, graded = load()
    if not records:
        print("No predictions yet.")
        return

    print(f"Total predictions logged: {len(records)}")
    print(f"Graded so far:            {len(graded)}")
    if not graded:
        return

    overall = score(graded)
    herd = herding(graded)
    per_symbol = by_symbol(graded)

    print(f"Distinct trading days:    {overall['days']}")
    print(f"Symbols tracked:          {len(per_symbol)}")

    print("\n--- PER SYMBOL (this is the number that counts) ---")
    for line in symbol_table(per_symbol):
        print(line)

    print("\n--- POOLED (correlated - read the caveat below) ---")
    print(f"  Agent accuracy:          {overall['correct']}/{overall['n']} = {overall['accuracy']:.1%}")
    print(f"  Baseline 'always up':    {overall['always_up']:.1%}")
    print(f"  Baseline 'prev day':     {overall['prev_day']:.1%}")
    print(f"  Edge vs always_up:       {overall['edge_vs_always_up']:+.1%}")
    print(f"  Edge vs prev_day:        {overall['edge_vs_prev_day']:+.1%}")
    print(f"  Agent up-rate:           {overall['up_rate']:.1%}")

    print("\n--- CALIBRATION (pooled) ---")
    print(f"  {'bucket':>10}  {'n':>4}  {'accuracy':>10}")
    for b, (c, n) in calibration(graded).items():
        print(f"  {b:>4}-{b+9:>3}%  {n:>4}  {c/n:>9.1%}")

    if herd["unanimous_rate"] is not None:
        print(
            f"\nSame-direction days: {herd['unanimous_days']}/{herd['days']} "
            f"({herd['unanimous_rate']:.0%} of multi-symbol days)"
        )

    recent = graded[-12:]
    print(f"\n--- MOST RECENT {len(recent)} GRADED ---")
    for r in recent:
        o, p = r["outcome"], r["prediction"]
        mark = "OK" if o["correct"] else "X "
        print(
            f"  {r['for_date']}  {r['symbol']:<6} pred:{p['direction']:<4}@{p['confidence']}%  "
            f"actual:{o['actual_direction']:<4} ({o['actual_pct']:+.2f}%)  {mark}"
        )

    warns = warnings(graded, overall, herd)
    if warns:
        print("\n--- READ THIS BEFORE BELIEVING ANY NUMBER ABOVE ---")
        for w in warns:
            print(f"  ! {w}")


if __name__ == "__main__":
    main()
