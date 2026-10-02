"""
Shared scoring helpers for review.py and weekly_report.py.

The one rule that matters here: predictions made on the same day across
correlated symbols are NOT independent tests. On an up day most of the universe
closes up together, so 10 symbols x 20 days is nowhere near 200 independent
samples. Every aggregate printed must therefore carry the distinct-day count
beside it, and the "graduate to paper trading" bar is counted PER SYMBOL.
"""

import json
from collections import defaultdict
from pathlib import Path

LOG = Path(__file__).parent / "logs" / "predictions.jsonl"

GRADUATION_MIN_PER_SYMBOL = 30


def load(log_path: Path = LOG) -> tuple[list[dict], list[dict]]:
    if not log_path.exists():
        return [], []
    records = [
        json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    return records, [r for r in records if r.get("outcome")]


def score(subset: list[dict]) -> dict | None:
    """Accuracy and baselines for a set of graded predictions."""
    n = len(subset)
    if n == 0:
        return None
    correct = sum(1 for r in subset if r["outcome"]["correct"])
    always_up = sum(1 for r in subset if r["outcome"]["always_up_correct"])
    prev_day = sum(1 for r in subset if r["outcome"]["prev_day_baseline_correct"])
    up_calls = sum(1 for r in subset if r["prediction"]["direction"] == "up")
    return {
        "n": n,
        "correct": correct,
        "accuracy": correct / n,
        "always_up": always_up / n,
        "prev_day": prev_day / n,
        "edge_vs_always_up": (correct - always_up) / n,
        "edge_vs_prev_day": (correct - prev_day) / n,
        "up_rate": up_calls / n,
        "days": len({r["for_date"] for r in subset}),
    }


def by_symbol(graded: list[dict]) -> dict[str, dict]:
    groups = defaultdict(list)
    for r in graded:
        groups[r["symbol"]].append(r)
    return {sym: score(rows) for sym, rows in sorted(groups.items())}


def calibration(graded: list[dict]) -> dict[int, tuple[int, int]]:
    buckets: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for r in graded:
        b = (int(r["prediction"]["confidence"]) // 10) * 10
        buckets[b][1] += 1
        if r["outcome"]["correct"]:
            buckets[b][0] += 1
    return {b: tuple(v) for b, v in sorted(buckets.items())}


def herding(graded: list[dict]) -> dict:
    """
    Is the agent making per-symbol calls, or one market call stamped N times?

    If on most days every symbol gets the same direction, the extra targets are
    not buying independent information, however many rows the log has.
    """
    per_day = defaultdict(list)
    for r in graded:
        per_day[r["for_date"]].append(r["prediction"]["direction"])
    multi = {d: v for d, v in per_day.items() if len(v) > 1}
    if not multi:
        return {"days": 0, "unanimous_days": 0, "unanimous_rate": None}
    unanimous = sum(1 for v in multi.values() if len(set(v)) == 1)
    return {
        "days": len(multi),
        "unanimous_days": unanimous,
        "unanimous_rate": unanimous / len(multi),
    }


def symbol_table(stats: dict[str, dict], indent: str = "  ") -> list[str]:
    lines = [
        f"{indent}{'symbol':<8}{'n':>4}{'agent':>9}{'alwaysUp':>10}"
        f"{'prevDay':>9}{'vs aUp':>9}{'upRate':>9}  status"
    ]
    for sym, s in stats.items():
        status = (
            "ready" if s["n"] >= GRADUATION_MIN_PER_SYMBOL else f"need {GRADUATION_MIN_PER_SYMBOL - s['n']}"
        )
        lines.append(
            f"{indent}{sym:<8}{s['n']:>4}{s['accuracy']:>9.1%}{s['always_up']:>10.1%}"
            f"{s['prev_day']:>9.1%}{s['edge_vs_always_up']:>+9.1%}{s['up_rate']:>9.1%}  {status}"
        )
    return lines


def warnings(graded: list[dict], overall: dict, herd: dict) -> list[str]:
    out = []
    if overall and overall["n"] > overall["days"]:
        out.append(
            f"{overall['n']} predictions span only {overall['days']} trading days. "
            f"Same-day calls are correlated, so treat ~{overall['days']} as the honest "
            "sample size for any aggregate number above."
        )
    if herd["unanimous_rate"] is not None and herd["unanimous_rate"] > 0.8:
        out.append(
            f"The agent called every symbol the same direction on "
            f"{herd['unanimous_days']}/{herd['days']} days ({herd['unanimous_rate']:.0%}). "
            "It is making one market call, not per-symbol calls - the extra targets "
            "are not adding independent information."
        )
    if overall and (overall["up_rate"] > 0.85 or overall["up_rate"] < 0.15):
        out.append(
            f"The agent said '{'up' if overall['up_rate'] > 0.5 else 'down'}' on "
            f"{max(overall['up_rate'], 1 - overall['up_rate']):.0%} of calls. That is drift, "
            "not prediction - compare it against the always-up baseline carefully."
        )
    ready = [s for s, v in by_symbol(graded).items() if v["n"] >= GRADUATION_MIN_PER_SYMBOL]
    if not ready and graded:
        out.append(
            f"No symbol has reached {GRADUATION_MIN_PER_SYMBOL} graded predictions yet. "
            "Nothing here is signal."
        )
    return out
