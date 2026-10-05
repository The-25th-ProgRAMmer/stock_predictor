"""
Read pre-fetched market data from data/market_context.json and print it on
stdout for the LLM predictor to consume.
No network calls (runs inside a cloud routine with no egress).

This script is the pipeline's gate. The routine is told to STOP if it exits
non-zero, so everything that would make the day's call unreproducible has to be
caught here rather than left for the agent to notice.

Two separate things can be wrong with the data, and both used to slip through:

  stale   the fetch workflow did not publish today's file, so the newest data
          describes a session that has already been traded. Checked on for_date
          rather than a day count: for_date is the session being predicted, so
          "for_date is in the past" is exactly the question, and it stays right
          across weekends without hardcoding how long one is.

  shaped  the file is recent enough but predates signals.py, so no target has a
          `signal` for the agent to reproduce. On 2026-10-05 this exited 0 and
          handed the agent a file with nothing to copy; it stopped only because
          the prompt made it careful. An agent that was less careful would have
          invented the call, which is the one failure this design exists to
          prevent.
"""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# A backstop only; the for_date check below is what actually catches staleness.
MAX_STALENESS_DAYS = 7

REQUIRED_TARGET_KEYS = ("signal", "material_news", "latest_close", "indicators")
REQUIRED_SIGNAL_KEYS = ("direction", "confidence", "basis", "evidence")

FETCH_HINT = (
    "The 'Fetch Market Data' workflow publishes this file at 11:45 UTC on "
    "weekdays. Check its most recent run at "
    "https://github.com/The-25th-ProgRAMmer/stock_predictor/actions "
    "- do NOT compute the call yourself."
)


def die(error: str, **extra) -> None:
    print(json.dumps({"error": error, **extra, "hint": FETCH_HINT}), file=sys.stderr)
    sys.exit(1)


def check_freshness(context: dict) -> None:
    today = datetime.now(timezone.utc).date()

    for key in ("as_of", "for_date"):
        if not context.get(key):
            die(f"market_context.json has no {key!r}")

    as_of = date.fromisoformat(context["as_of"])
    for_date = date.fromisoformat(context["for_date"])

    if as_of > today:
        die(f"as_of {as_of} is in the future - the data file is not trustworthy")

    # The session being predicted must not already be over. This is the real
    # staleness test: a file left over from a previous day always points at a
    # session that has since been traded.
    if for_date < today:
        die(
            f"data is stale: it predicts {for_date}, which has already passed "
            f"(today is {today}, as_of {as_of}). No fresh data was published "
            f"for today's session.",
            for_date=str(for_date),
            as_of=str(as_of),
        )

    age = (today - as_of).days
    if age > MAX_STALENESS_DAYS:
        die(f"data is {age} days old (as_of {as_of})", as_of=str(as_of))


def check_shape(context: dict) -> None:
    targets = context.get("targets") or []
    if not targets:
        die("market_context.json lists no usable targets")

    target_data = context.get("target_data") or {}
    problems = []
    for sym in targets:
        block = target_data.get(sym)
        if not isinstance(block, dict):
            problems.append(f"{sym}: no target_data block")
            continue
        missing = [k for k in REQUIRED_TARGET_KEYS if k not in block]
        if missing:
            problems.append(f"{sym}: missing {', '.join(missing)}")
            continue
        signal = block["signal"]
        if not isinstance(signal, dict):
            problems.append(f"{sym}: signal is not an object")
            continue
        bad = [k for k in REQUIRED_SIGNAL_KEYS if not signal.get(k)]
        if bad:
            problems.append(f"{sym}: signal missing {', '.join(bad)}")
        elif signal["direction"] not in ("up", "down"):
            problems.append(f"{sym}: signal direction {signal['direction']!r}")

    if problems:
        die(
            "the data file carries no usable computed call, so there is nothing "
            "for the agent to reproduce. It was most likely written by a fetch "
            "that predates signals.py. Problems: " + "; ".join(problems),
            as_of=context.get("as_of"),
            affected=len(problems),
            targets=len(targets),
        )


def main() -> None:
    context_path = Path(__file__).parent / "data" / "market_context.json"
    if not context_path.exists():
        die("data/market_context.json not found")

    try:
        with open(context_path, encoding="utf-8") as f:
            context = json.load(f)
    except json.JSONDecodeError as e:
        die(f"data/market_context.json is not valid JSON: {e}")

    check_freshness(context)
    check_shape(context)

    print(json.dumps(context, indent=2))


if __name__ == "__main__":
    main()
