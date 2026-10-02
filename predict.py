"""
Read pre-fetched market data from data/market_context.json and print it on
stdout for the LLM predictor to consume.
No network calls (runs inside a cloud routine with no egress).
"""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

MAX_STALENESS_DAYS = 4


def main() -> None:
    context_path = Path(__file__).parent / "data" / "market_context.json"
    if not context_path.exists():
        print(
            json.dumps({"error": "data/market_context.json not found"}),
            file=sys.stderr,
        )
        sys.exit(1)

    with open(context_path) as f:
        context = json.load(f)

    as_of = date.fromisoformat(context["as_of"])
    age = (datetime.now(timezone.utc).date() - as_of).days
    if age > MAX_STALENESS_DAYS:
        print(
            json.dumps(
                {
                    "error": f"data is {age} days old (as_of {context['as_of']}); "
                    "the fetch workflow has not published fresh data"
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    print(json.dumps(context, indent=2))


if __name__ == "__main__":
    main()
