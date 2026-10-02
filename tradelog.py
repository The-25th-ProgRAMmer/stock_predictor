"""
Reader and writer for logs/trades.jsonl.

The log is append-only and event-sourced. Four event types accumulate in order:

    entry_submitted   an opening-auction order went out
    bracket_armed     a take-profit / stop pair now rests at Alpaca
    exit_submitted    we closed the position ourselves (time stop, or manual)
    round_trip        entry and exit have been paired and the P&L recorded

Positions are held across sessions, so an entry and its exit are no longer the
same date and cannot be matched on one. Every event after the entry carries
`entry_for_date` instead, making (symbol, entry_for_date) the key that ties a
position's whole life together.

The usual exit leaves NO exit_submitted row at all: the resting pair fills at
Alpaca with nothing running here. reconcile.py finds those by asking Alpaca what
became of the order recorded in bracket_armed.

Kept SEPARATE from logs/predictions.jsonl so a trading bug can never contaminate
the accuracy experiment.
"""

import json
from pathlib import Path

TRADES = Path(__file__).parent / "logs" / "trades.jsonl"


def load_rows() -> list[dict]:
    if not TRADES.exists():
        return []
    return [
        json.loads(l)
        for l in TRADES.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]


def append(rows: list[dict]) -> None:
    if not rows:
        return
    TRADES.parent.mkdir(exist_ok=True)
    with TRADES.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def key(row: dict) -> tuple[str, str]:
    """(symbol, entry_for_date) - identifies one position across all its events."""
    date = row["for_date"] if row["event"] == "entry_submitted" else row["entry_for_date"]
    return (row["symbol"], date)


def of_event(rows: list[dict], event: str) -> dict[tuple[str, str], dict]:
    """
    {key: row} for one event type. Later rows overwrite earlier ones, so a
    re-armed bracket replaces the bracket it replaced at Alpaca.
    """
    return {key(r): r for r in rows if r["event"] == event}


def unreconciled(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Entry rows with no round_trip recorded against them yet."""
    done = set(of_event(rows, "round_trip"))
    return {k: r for k, r in of_event(rows, "entry_submitted").items() if k not in done}


def entry_for_symbol(rows: list[dict], symbol: str) -> dict | None:
    """
    The open entry for a symbol. Entries are never stacked, so at most one is
    open per symbol; if the log somehow holds several, the newest is returned.
    """
    candidates = [r for k, r in unreconciled(rows).items() if k[0] == symbol]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r["for_date"])
