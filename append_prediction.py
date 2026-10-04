"""
Read prediction JSON from stdin (or a file path arg), validate required fields,
and append to logs/predictions.jsonl as one line per prediction.

Accepts either a single prediction object or a list of them, so one routine run
can log every target symbol. Dedupe is on (symbol, for_date): several symbols
may share a date, but no symbol may be predicted twice for the same date.

Either every prediction in the batch is written, or none is.

The call is computed, not chosen. data/market_context.json carries a `signal`
block per symbol (signals.decide), and a prediction must reproduce it exactly:

    method "signal"         direction and confidence equal the computed call
    method "news_override"  the opposite direction, citing a headline that
                            signals.py flagged as a material event for that
                            symbol, at the fixed override confidence

Anything else is rejected. This is enforced here rather than requested in the
prompt, because the prompt alone produced NVDA "up 56%" and "down 56%" on
identical data. The only judgement left to the model is whether a flagged event
outweighs the trend - and every such override is logged as one, so whether it
helps can be measured separately from the mechanical call.
"""

import json
import sys
from pathlib import Path

from signals import OVERRIDE_CONFIDENCE
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
PRED_REQUIRED = {"direction", "confidence", "method", "reasoning", "key_signals", "sources"}
LOG = Path(__file__).parent / "logs" / "predictions.jsonl"
CONTEXT = Path(__file__).parent / "data" / "market_context.json"


def load_context() -> dict:
    if not CONTEXT.exists():
        sys.exit(f"{CONTEXT} is missing - cannot check predictions against the "
                 f"computed signal, so nothing was written")
    return json.loads(CONTEXT.read_text(encoding="utf-8"))


def check_against_signal(p: dict, symbol: str, for_date: str, ctx: dict, where: str) -> None:
    if for_date != ctx.get("for_date"):
        sys.exit(f"{where}: for_date {for_date!r} does not match the market "
                 f"context's {ctx.get('for_date')!r}")
    block = (ctx.get("target_data") or {}).get(symbol) or {}
    sig = block.get("signal")
    if not sig:
        sys.exit(f"{where}: no computed signal for {symbol} in the market context")

    method = p["method"]
    if method == "signal":
        if p["direction"] != sig["direction"] or int(p["confidence"]) != sig["confidence"]:
            sys.exit(
                f"{where}: method 'signal' must reproduce the computed call "
                f"{sig['direction']} @ {sig['confidence']}%, got "
                f"{p['direction']} @ {p['confidence']}%"
            )
    elif method == "news_override":
        if p["direction"] == sig["direction"]:
            sys.exit(f"{where}: an override must go AGAINST the computed "
                     f"{sig['direction']}; agreeing news needs no override")
        if int(p["confidence"]) != OVERRIDE_CONFIDENCE:
            sys.exit(f"{where}: override confidence is fixed at "
                     f"{OVERRIDE_CONFIDENCE}%, got {p['confidence']}%")
        cited = (p.get("override") or {}).get("headline")
        flagged = {n["headline"] for n in block.get("material_news") or []}
        if not cited or cited not in flagged:
            sys.exit(
                f"{where}: an override must cite, verbatim, one of {symbol}'s "
                f"flagged material headlines in prediction.override.headline. "
                f"Flagged: {sorted(flagged) or 'none - so no override is possible'}"
            )
    else:
        sys.exit(f"{where}: method must be 'signal' or 'news_override', got {method!r}")


def validate(data: dict, idx: int, ctx: dict) -> tuple[str, str]:
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

    check_against_signal(p, symbol, data["for_date"], ctx, f"{where} ({symbol})")
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

    ctx = load_context()
    keys = [validate(d, i, ctx) for i, d in enumerate(batch)]

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
            f"({p['direction']} @ {p['confidence']}%, {p['method']})"
        )
    print(f"appended {len(batch)} prediction(s)")


if __name__ == "__main__":
    main()
