"""
Read a prediction JSON object from stdin (or a file path arg), validate
required fields, and append as one JSONL line to logs/predictions.jsonl.
"""

import json
import sys
from pathlib import Path

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


def main() -> None:
    raw = Path(sys.argv[1]).read_text(encoding="utf-8") if len(sys.argv) > 1 else sys.stdin.read()
    data = json.loads(raw)

    missing = REQUIRED - data.keys()
    if missing:
        sys.exit(f"missing top-level fields: {sorted(missing)}")

    p = data["prediction"]
    pmissing = PRED_REQUIRED - p.keys()
    if pmissing:
        sys.exit(f"missing prediction fields: {sorted(pmissing)}")

    if p["direction"] not in ("up", "down"):
        sys.exit(f"direction must be 'up' or 'down', got {p['direction']!r}")
    if not (50 <= int(p["confidence"]) <= 100):
        sys.exit(f"confidence must be 50..100, got {p['confidence']!r}")

    LOG.parent.mkdir(exist_ok=True)
    if LOG.exists():
        existing = {
            json.loads(l)["for_date"]
            for l in LOG.read_text(encoding="utf-8").splitlines()
            if l.strip()
        }
        if data["for_date"] in existing:
            sys.exit(f"a prediction for {data['for_date']} is already logged")

    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data) + "\n")
    print(f"appended prediction for {data['for_date']} ({p['direction']} @ {p['confidence']}%)")


if __name__ == "__main__":
    main()
