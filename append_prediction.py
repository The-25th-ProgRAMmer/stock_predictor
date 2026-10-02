"""
Read prediction JSON from stdin (or a file path arg), validate required fields,
and append to logs/predictions.jsonl as one line per prediction.

Accepts either a single prediction object or a list of them, so one routine run
can log every target symbol. Dedupe is on (symbol, for_date): several symbols
may share a date, but no symbol may be predicted twice for the same date.

Either every prediction in the batch is written, or none is.
"""

import json
import sys
from pathlib import Path

from universe import TARGETS

REQUIRED = {
    "made_at",
    "for_date",
    "symbol",
    "prediction",
    "baselines",
    "context_snapshot",
    "outcome",
}
PRED_REQUIRED = {"direction", "confidence", "reasoning", "key_signals", "sources"}
LOG = Path(__file__).parent / "logs" / "predictions.jsonl"


def validate(data: dict, idx: int) -> tuple[str, str]:
    where = f"prediction[{idx}]"
    if not isinstance(data, dict):
        sys.exit(f"{where}: expected a JSON object, got {type(data).__name__}")

    missing = REQUIRED - data.keys()
    if missing:
        sys.exit(f"{where}: missing top-level fields: {sorted(missing)}")

    symbol = data["symbol"]
    if symbol not in TARGETS:
        sys.exit(f"{where}: {symbol!r} is not a target symbol (targets: {TARGETS})")

    p = data["prediction"]
    pmissing = PRED_REQUIRED - p.keys()
    if pmissing:
        sys.exit(f"{where} ({symbol}): missing prediction fields: {sorted(pmissing)}")

    if p["direction"] not in ("up", "down"):
        sys.exit(f"{where} ({symbol}): direction must be 'up' or 'down', got {p['direction']!r}")
    if not (50 <= int(p["confidence"]) <= 100):
        sys.exit(f"{where} ({symbol}): confidence must be 50..100, got {p['confidence']!r}")

    return symbol, data["for_date"]


def main() -> None:
    raw = (
        Path(sys.argv[1]).read_text(encoding="utf-8")
        if len(sys.argv) > 1
        else sys.stdin.read()
    )
    parsed = json.loads(raw)
    batch = parsed if isinstance(parsed, list) else [parsed]
    if not batch:
        sys.exit("no predictions supplied")

    keys = [validate(d, i) for i, d in enumerate(batch)]

    dupes_in_batch = {k for k in keys if keys.count(k) > 1}
    if dupes_in_batch:
        sys.exit(f"batch contains duplicate (symbol, for_date): {sorted(dupes_in_batch)}")

    LOG.parent.mkdir(exist_ok=True)
    if LOG.exists():
        existing = set()
        for line in LOG.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                existing.add((rec["symbol"], rec["for_date"]))
        clash = [k for k in keys if k in existing]
        if clash:
            sys.exit(
                "already logged: "
                + ", ".join(f"{s} for {d}" for s, d in clash)
                + " - nothing was written"
            )

    with LOG.open("a", encoding="utf-8") as f:
        for data in batch:
            f.write(json.dumps(data) + "\n")

    for data in batch:
        p = data["prediction"]
        print(
            f"appended prediction for {data['symbol']} {data['for_date']} "
            f"({p['direction']} @ {p['confidence']}%)"
        )
    print(f"appended {len(batch)} prediction(s)")


if __name__ == "__main__":
    main()
