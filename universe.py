"""
The symbol universe, split by role.

TARGETS are predicted and graded every day — the agent is scored on these.
CONTEXT symbols are never predicted. They are fetched, compressed into one line
each, and fed to the agent as cross-asset background for the target calls.

Keep TARGETS small and CONTEXT cheap: every target costs the agent a careful
call, every context symbol costs only a line.
"""

# Predicted and graded daily.
TARGETS = [
    "SPY",    # broad market — the original series, keep it continuous
    "QQQ",    # tech beta
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "TSLA",
    "SMH",    # semis as a group, instead of five correlated single-name targets
]

MAG7 = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]

# Individual semis: breadth input for the SMH call, not targets of their own.
SEMIS = ["NVDA", "AMD", "AVGO", "TSM", "MU", "INTC"]

# Cross-asset context. Never predicted.
CONTEXT = {
    "volatility": ["VIXY", "VXX"],
    "metals": ["GLD", "SLV"],
    "bonds": ["TLT", "IEF"],
    "credit": ["HYG", "LQD"],
    "dollar": ["UUP"],
    "energy": ["USO"],
    "breadth": ["IWM"],
    "semis": ["AMD", "AVGO", "TSM", "MU", "INTC"],
}

CONTEXT_SYMBOLS = sorted({s for group in CONTEXT.values() for s in group})

# One Alpaca call covers all of these.
ALL_SYMBOLS = sorted(set(TARGETS) | set(CONTEXT_SYMBOLS))

# VIX itself is an index, not a tradeable symbol — the Alpaca stocks API does not
# serve it. VIXY/VXX are the proxies, and because both bleed value to contango
# their raw level means nothing over months: use daily change and percentile rank.
