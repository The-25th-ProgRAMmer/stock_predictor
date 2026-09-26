"""
Read pre-fetched SPY market data and technical indicators from data/spy_context.json
and print it on stdout for the LLM predictor to consume.
No network calls (runs inside a cloud routine with no egress).
"""

import json
import sys
from pathlib import Path


def main() -> None:
    context_path = Path(__file__).parent / "data" / "spy_context.json"
    if not context_path.exists():
        print(
            json.dumps({"error": "data/spy_context.json not found"}),
            file=sys.stderr,
        )
        sys.exit(1)

    with open(context_path) as f:
        context = json.load(f)

    print(json.dumps(context, indent=2))


if __name__ == "__main__":
    main()
