"""
Flatten EVERY open position into the closing auction. Manual panic button.

This is no longer part of the daily cycle. Positions are held until their
take-profit or stop trades (arm_exits.py) or until the time stop fires
(sweep_exits.py); nothing closes a position just because the day ended. This
script exists for when you want out of everything at once regardless of where
the brackets sit - a bad data day, a change of strategy, or simply wanting the
book empty.

It is reachable only through the workflow's manual dispatch (action: flatten) or
by running it yourself. Nothing schedules it.

Each position's resting bracket is cancelled first. The two legs reserve the
shares, so a closing order sent while they are still working is rejected for
insufficient quantity.

Dry run:  python exit_trades.py --dry-run
"""

import argparse
import sys
from datetime import datetime, timezone

import broker
import tradelog

MAX_MINUTES_BEFORE_CLOSE = 60   # don't run hours early
MIN_MINUTES_BEFORE_CLOSE = 12   # cls orders must be in before ~15:50 ET


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
                print(f"too late for a closing-auction order "
                      f"(< {MIN_MINUTES_BEFORE_CLOSE} min) - exiting")
                return 1

    pos = broker.positions()
    if not pos:
        print("no open positions - nothing to flatten")
        return 0

    today = now.date().isoformat()
    log = tradelog.load_rows()
    working = {}
    for o in broker.open_orders():
        working.setdefault(o["symbol"], []).append(o)

    print(f"\nflattening {len(pos)} open position(s):")
    rows, failures = [], []
    for p in sorted(pos, key=lambda p: p["symbol"]):
        sym = p["symbol"]
        qty = abs(int(float(p["qty"])))
        side = "long" if float(p["qty"]) > 0 else "short"
        upl = float(p.get("unrealized_pl", 0) or 0)
        print(f"  {sym:<6} {side:<5} {qty:>4} @ ${float(p['avg_entry_price']):>8.2f}  "
              f"unrealised ${upl:>+9.2f}")

        if args.dry_run:
            continue

        cancelled = True
        for o in working.get(sym, []):
            try:
                broker.cancel_order(o["id"])
                print(f"         cancelled resting {o['id']}")
            except broker.BrokerError as e:
                print(f"         FAILED to cancel {o['id']}: {e}")
                failures.append((sym, f"cancel failed, not flattening: {e}"))
                cancelled = False
                break
        if not cancelled:
            continue

        entry = tradelog.entry_for_symbol(log, sym)
        coid = f"flat-{today}-{sym}"
        try:
            o = broker.submit(sym, qty, "sell" if side == "long" else "buy", "cls", coid)
            print(f"         -> market-on-close {o['id']} ({o['status']})")
            rows.append({
                "event": "exit_submitted",
                "symbol": sym,
                "entry_for_date": entry["for_date"] if entry else today,
                "date": today,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "exit_kind": "manual",
                "side": side,
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

    tradelog.append(rows)
    if rows:
        print(f"\nrecorded {len(rows)} exit order(s)")
    if failures:
        print(f"\n{len(failures)} exit(s) FAILED - positions may still be open:")
        for sym, err in failures:
            print(f"  {sym}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
