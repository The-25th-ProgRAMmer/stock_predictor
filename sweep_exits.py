"""
Close positions that have been held too long, and report any left unprotected.

The exit policy is a take-profit and a stop the same distance away, and a symbol
that simply drifts sideways can touch neither indefinitely. MAX_HOLD_SESSIONS
caps that: after 21 trading sessions - the longest lookback the trend score uses,
so the signal that opened the position has fully rolled over by then - the
position is flattened into the closing auction wherever it happens to sit.

The resting bracket is cancelled first. Its two legs reserve the shares, so a
closing order sent while they are still working is rejected for insufficient
quantity.

This also runs before the close as the last chance to notice a position with no
bracket on it. It does not arm them - that needs the fill and volatility data
arm_exits.py works from - it reports them and exits non-zero.

Dry run:  python sweep_exits.py --dry-run
"""

import argparse
import sys
from datetime import datetime, timezone

import broker
import tradelog

MAX_HOLD_SESSIONS = 21          # the trend score's longest lookback
MAX_MINUTES_BEFORE_CLOSE = 60   # don't run hours early
MIN_MINUTES_BEFORE_CLOSE = 12   # cls orders must be in before ~15:50 ET


def sessions_held(entry_date: str, today: str) -> int | None:
    """
    Trading sessions elapsed since the entry session, from Alpaca's own calendar.
    The entry session itself is not counted, so a position opened this morning is
    0 sessions old.
    """
    if entry_date > today:
        return None
    cal = broker.calendar(entry_date, today)
    return len([c for c in cal if entry_date < c["date"] <= today])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, send nothing")
    ap.add_argument("--force", action="store_true", help="skip the pre-close timing window")
    args = ap.parse_args()

    acct = broker.assert_paper_account()
    print(f"paper account {acct['account_number']}  equity ${float(acct['equity']):,.2f}")

    clk = broker.clock()
    now = datetime.fromisoformat(clk["timestamp"])
    today = now.date().isoformat()

    if not clk["is_open"]:
        print("market is closed - nothing to sweep")
        if not args.force and not args.dry_run:
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
        print("no open positions")
        return 0

    log = tradelog.load_rows()
    orders = broker.open_orders()
    working = {}
    for o in orders:
        working.setdefault(o["symbol"], []).append(o)

    print(f"\n{len(pos)} open position(s):")
    rows, failures, unprotected = [], [], []

    for p in sorted(pos, key=lambda p: p["symbol"]):
        sym = p["symbol"]
        qty = abs(int(float(p["qty"])))
        side = "long" if float(p["qty"]) > 0 else "short"
        upl = float(p.get("unrealized_pl", 0) or 0)
        uplpc = float(p.get("unrealized_plpc", 0) or 0) * 100

        entry = tradelog.entry_for_symbol(log, sym)
        if entry is None:
            print(f"  {sym:<6} {side:<5} {qty:>4}  no entry in the trade log - "
                  f"cannot age it, leaving alone")
            continue

        held = sessions_held(entry["for_date"], today)
        age = "?" if held is None else str(held)
        flag = "" if sym in working else "   NO BRACKET"
        print(f"  {sym:<6} {side:<5} {qty:>4} @ {float(p['avg_entry_price']):>8.2f}  "
              f"held {age:>2} session(s)  unrealised ${upl:>+9.2f} ({uplpc:>+6.2f}%)"
              f"{flag}")

        if sym not in working:
            unprotected.append(sym)

        if held is None or held < MAX_HOLD_SESSIONS:
            continue

        print(f"         time stop: {held} >= {MAX_HOLD_SESSIONS} sessions - flattening")
        if args.dry_run:
            continue

        for o in working.get(sym, []):
            try:
                broker.cancel_order(o["id"])
                print(f"         cancelled resting {o.get('order_class') or o['type']} "
                      f"{o['id']}")
            except broker.BrokerError as e:
                print(f"         FAILED to cancel {o['id']}: {e}")
                failures.append((sym, f"cancel failed, not flattening: {e}"))
                break
        else:
            coid = f"time-{today}-{sym}"
            try:
                o = broker.submit(sym, qty, "sell" if side == "long" else "buy",
                                  "cls", coid)
                print(f"         -> market-on-close {o['id']} ({o['status']})")
                rows.append({
                    "event": "exit_submitted",
                    "symbol": sym,
                    "entry_for_date": entry["for_date"],
                    "date": today,
                    "submitted_at": datetime.now(timezone.utc).isoformat(),
                    "exit_kind": "time_stop",
                    "side": side,
                    "qty": qty,
                    "avg_entry_price": float(p["avg_entry_price"]),
                    "sessions_held": held,
                    "unrealized_pl_at_submit": upl,
                    "order_id": o["id"],
                    "client_order_id": coid,
                    "status": o["status"],
                })
            except broker.BrokerError as e:
                print(f"         FAILED: {e}")
                failures.append((sym, str(e)))

    if args.dry_run:
        print("\n--dry-run: nothing sent")
        return 0

    tradelog.append(rows)
    if rows:
        print(f"\nflattened {len(rows)} position(s) on the time stop")
    else:
        print("\nnothing aged out")

    rc = 0
    if unprotected:
        print(f"\nWARNING: {len(unprotected)} position(s) carry no take-profit and "
              f"no stop: {', '.join(unprotected)}")
        print("Run arm_exits.py during market hours to protect them.")
        rc = 1
    if failures:
        print(f"\n{len(failures)} time stop(s) FAILED:")
        for sym, err in failures:
            print(f"  {sym}: {err}")
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
