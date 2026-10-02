"""
Reconcile submitted orders against actual fills, and record realised P&L.

Reads logs/trades.jsonl for orders this script has submitted, asks Alpaca what
actually happened to each, and writes a `round_trip` row per symbol pairing the
entry fill with the exit fill.

This log is deliberately SEPARATE from logs/predictions.jsonl. The prediction
experiment measures directional accuracy; this measures money. Keeping them
apart means a trading bug can never contaminate the accuracy record, and the
two can be compared without either being derived from the other.

The comparison that matters: predictions are graded close-to-close, but the
tradeable window is open-to-close. The overnight gap is not capturable on this
schedule, so a correct prediction can still lose money and vice versa. The
`gap_pct` field on each round trip quantifies exactly how much of the graded
move happened before we could act.
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import broker

TRADES = Path(__file__).parent / "logs" / "trades.jsonl"


def load_rows() -> list[dict]:
    if not TRADES.exists():
        return []
    return [
        json.loads(l) for l in TRADES.read_text(encoding="utf-8").splitlines() if l.strip()
    ]


def append(rows: list[dict]) -> None:
    with TRADES.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def fill_info(order_id: str) -> dict | None:
    try:
        o = broker.get_order(order_id)
    except broker.BrokerError as e:
        return {"error": str(e)}
    return {
        "status": o.get("status"),
        "filled_qty": float(o.get("filled_qty") or 0),
        "filled_avg_price": (
            float(o["filled_avg_price"]) if o.get("filled_avg_price") else None
        ),
        "filled_at": o.get("filled_at"),
    }


def main() -> int:
    broker.assert_paper_account()
    rows = load_rows()
    if not rows:
        print("no trade log yet - nothing to reconcile")
        return 0

    done = {
        (r["symbol"], r.get("for_date") or r.get("date"))
        for r in rows if r["event"] == "round_trip"
    }

    entries, exits = {}, {}
    for r in rows:
        if r["event"] == "entry_submitted":
            entries[(r["symbol"], r["for_date"])] = r
        elif r["event"] == "exit_submitted":
            exits[(r["symbol"], r["date"])] = r

    new = []
    for key, entry in sorted(entries.items()):
        if key in done:
            continue
        exit_row = exits.get(key)
        if exit_row is None:
            print(f"  {key[0]} {key[1]}: no exit recorded yet - still open or not flattened")
            continue

        ef = fill_info(entry["order_id"])
        xf = fill_info(exit_row["order_id"])
        if not ef or not xf or ef.get("status") != "filled" or xf.get("status") != "filled":
            print(f"  {key[0]} {key[1]}: not both filled "
                  f"(entry={ef and ef.get('status')}, exit={xf and xf.get('status')})")
            continue

        qty = min(ef["filled_qty"], xf["filled_qty"])
        entry_px, exit_px = ef["filled_avg_price"], xf["filled_avg_price"]
        if not qty or entry_px is None or exit_px is None:
            print(f"  {key[0]} {key[1]}: missing fill price")
            continue

        direction = 1 if entry["side"] == "long" else -1
        pnl = (exit_px - entry_px) * qty * direction
        ret_pct = (exit_px - entry_px) / entry_px * 100 * direction

        # How much of the graded close-to-close move happened overnight, before
        # the open-to-close window we could actually trade.
        ref = entry.get("reference_price")
        gap_pct = ((entry_px - ref) / ref * 100) if ref else None

        row = {
            "event": "round_trip",
            "symbol": key[0],
            "for_date": key[1],
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
            "side": entry["side"],
            "qty": qty,
            "entry_price": entry_px,
            "exit_price": exit_px,
            "pnl": round(pnl, 2),
            "return_pct": round(ret_pct, 4),
            "prior_close": ref,
            "gap_pct": round(gap_pct, 4) if gap_pct is not None else None,
            "predicted": entry.get("predicted"),
            "confidence": entry.get("confidence"),
            "trend_score": entry.get("trend_score"),
            "size_fraction": entry.get("size_fraction"),
            "annual_vol_pct": entry.get("annual_vol_pct"),
            "prediction_correct": (
                None if not entry.get("predicted")
                else (exit_px > entry_px) == (entry["predicted"] == "up")
            ),
        }
        new.append(row)
        print(f"  {key[0]:<6} {key[1]}  {entry['side']:<5} {qty:>4} "
              f"{entry_px:>8.2f} -> {exit_px:>8.2f}  "
              f"P&L ${pnl:>+9.2f} ({ret_pct:>+6.2f}%)  gap {gap_pct:>+6.2f}%"
              if gap_pct is not None else "")

    if new:
        append(new)
        total = sum(r["pnl"] for r in new)
        print(f"\nreconciled {len(new)} round trip(s), total P&L ${total:+,.2f}")
    else:
        print("nothing new to reconcile")

    all_rt = [r for r in load_rows() if r["event"] == "round_trip"]
    if all_rt:
        total = sum(r["pnl"] for r in all_rt)
        wins = sum(1 for r in all_rt if r["pnl"] > 0)
        by_sym = defaultdict(float)
        for r in all_rt:
            by_sym[r["symbol"]] += r["pnl"]
        print(f"\nall-time: {len(all_rt)} round trips, {wins} profitable, "
              f"total P&L ${total:+,.2f}")
        print("  per symbol: " + "  ".join(
            f"{s}:{p:+,.0f}" for s, p in sorted(by_sym.items(), key=lambda x: -x[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
