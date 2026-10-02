"""
Flatten every open position into the closing auction, then record the P&L.

Runs in GitHub Actions before the close. Submits a market-on-close order for
each open position, so entries fill at the official open and exits at the
official close — the open-to-close window the strategy actually holds.

Idempotent via client_order_id "exit-<date>-<symbol>", and it skips any symbol
that already has an open exit order.

Note the metric mismatch, by design: predictions are GRADED close-to-close
(prior close -> for_date close), but the tradeable window is open-to-close. The
overnight gap cannot be captured on this schedule, so realised P&L will
systematically differ from graded accuracy. reconcile.py reports both.

Dry run:  python exit_trades.py --dry-run
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import broker

TRADES = Path(__file__).parent / "logs" / "trades.jsonl"

MAX_MINUTES_BEFORE_CLOSE = 60   # don't run hours early
MIN_MINUTES_BEFORE_CLOSE = 12   # cls orders must be in before ~15:50 ET


def record(rows: list[dict]) -> None:
    TRADES.parent.mkdir(exist_ok=True)
    with TRADES.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    acct = broker.assert_paper_account()
    print(f"paper account {acct['account_number']}  equity ${float(acct['equity']):,.2f}")

    clk = broker.clock()
    now = datetime.fromisoformat(clk["timestamp"])

    if not clk["is_open"]:
        print("market is closed - nothing to do")
        if not args.force:
            return 0
    else:
        close = datetime.fromisoformat(clk["next_close"])
        minutes = (close - now).total_seconds() / 60
        print(f"market closes in {minutes:.0f} min")
        if not args.force and not args.dry_run:
            if minutes > MAX_MINUTES_BEFORE_CLOSE:
                print(f"too early (> {MAX_MINUTES_BEFORE_CLOSE} min) - exiting")
                return 0
            if minutes < MIN_MINUTES_BEFORE_CLOSE:
                print(f"too late for a closing-auction order (< {MIN_MINUTES_BEFORE_CLOSE} min) - exiting")
                return 1

    pos = broker.positions()
    if not pos:
        print("no open positions - nothing to flatten")
        return 0

    today = now.date().isoformat()
    pending = {
        o["symbol"] for o in broker.open_orders()
        if str(o.get("client_order_id", "")).startswith(f"exit-{today}-")
    }

    print(f"\n{len(pos)} open position(s):")
    rows, failures = [], []
    for p in pos:
        sym = p["symbol"]
        qty = abs(int(float(p["qty"])))
        is_long = float(p["qty"]) > 0
        upl = float(p.get("unrealized_pl", 0))
        print(f"  {sym:<6} {'long' if is_long else 'short':<5} {qty:>4} @ "
              f"${float(p['avg_entry_price']):>8.2f}  unrealised ${upl:>+9.2f}")

        if sym in pending:
            print(f"         exit order already working - skipping")
            continue
        if args.dry_run:
            continue

        side = "sell" if is_long else "buy"
        coid = f"exit-{today}-{sym}"
        try:
            o = broker.submit(sym, qty, side, "cls", coid)
            print(f"         -> {side} {qty} cls  {o['id']} ({o['status']})")
            rows.append({
                "event": "exit_submitted",
                "date": today,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "symbol": sym,
                "side": "long" if is_long else "short",
                "qty": qty,
                "avg_entry_price": float(p["avg_entry_price"]),
                "unrealized_pl_at_submit": upl,
                "order_id": o["id"],
                "client_order_id": coid,
                "status": o["status"],
            })
        except broker.BrokerError as e:
            print(f"         FAILED: {e}")
            failures.append((sym, str(e)))

    if args.dry_run:
        print("\n--dry-run: no orders sent")
        return 0

    if rows:
        record(rows)
        print(f"\nrecorded {len(rows)} exit order(s)")
    if failures:
        print(f"\n{len(failures)} exit(s) FAILED - positions may be left open overnight:")
        for sym, err in failures:
            print(f"  {sym}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
