"""
Rest a take-profit / stop pair on every open position that has none.

Runs shortly after the opening auction, once the entries have real fill prices to
anchor on. Each position gets ONE one-cancels-other order, good till cancelled: a
take-profit and a stop the same distance away, both scaled to that symbol's own
daily volatility (exits.py). From then on Alpaca manages the exit - whichever leg
trades first cancels the other - so a position cannot be closed twice and nothing
here needs to watch the market. This is why there is no intraday polling job.

A position counts as unprotected only when it has no working order against it, so
a second run in the same session arms nothing. When arming fails the script exits
non-zero and the next session retries, because an unprotected position held
overnight is the one state this design must not sit in quietly.

A position that has already run past one of its own levels in the first minutes
cannot be bracketed - Alpaca rejects a leg sitting on the wrong side of the
market - so it is closed at market instead, which is what the bracket would have
done had it been there.

Dry run:  python arm_exits.py --dry-run
"""

import argparse
import sys
from datetime import datetime, timezone

import broker
import exits
import sizing
import tradelog
from trend_score import load_bars


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="compute and print, send nothing")
    ap.add_argument("--force", action="store_true", help="arm outside market hours too")
    args = ap.parse_args()

    acct = broker.assert_paper_account()
    print(f"paper account {acct['account_number']}  equity ${float(acct['equity']):,.2f}")

    clk = broker.clock()
    today = datetime.fromisoformat(clk["timestamp"]).date().isoformat()
    if not clk["is_open"] and not args.force and not args.dry_run:
        print("market is closed - a position that has run past a level could not "
              "be closed at market, so arming waits for the next session")
        return 0

    pos = broker.positions()
    if not pos:
        print("no open positions - nothing to arm")
        return 0

    protected = {o["symbol"] for o in broker.open_orders()}
    bars = load_bars()
    log = tradelog.load_rows()

    print(f"\n{len(pos)} open position(s), {len(protected & {p['symbol'] for p in pos})} "
          f"already protected")

    rows, failures = [], []
    for p in sorted(pos, key=lambda p: p["symbol"]):
        sym = p["symbol"]
        qty = abs(int(float(p["qty"])))
        side = "long" if float(p["qty"]) > 0 else "short"
        entry_px = float(p["avg_entry_price"])
        last = float(p["current_price"])

        if sym in protected:
            print(f"  {sym:<6} {side:<5} {qty:>4}  working order present - leaving alone")
            continue

        vol = sizing.annualised_vol(bars.get(sym) or [])
        b = exits.bracket(entry_px, side, vol)
        if b is None:
            print(f"  {sym:<6} {side:<5} {qty:>4}  NO BRACKET: cannot size "
                  f"(annual vol {vol!r}) - position left UNPROTECTED")
            failures.append((sym, f"no volatility estimate (vol={vol!r})"))
            continue

        entry = tradelog.entry_for_symbol(log, sym)
        entry_for_date = entry["for_date"] if entry else today
        close_side = "sell" if side == "long" else "buy"
        hit = exits.already_past(last, side, b)

        print(f"  {sym:<6} {side:<5} {qty:>4} @ {entry_px:>8.2f}   "
              f"tp {b['take_profit']:>8.2f}  sl {b['stop_loss']:>8.2f}  "
              f"+-{b['distance_pct']:>5.2f}% (${exits.expected_pnl(qty, b):,.0f})  "
              f"last {last:>8.2f}" + (f"   ALREADY AT {hit.upper()}" if hit else ""))

        if args.dry_run:
            continue

        if hit:
            coid = f"imm-{today}-{sym}"
            try:
                o = broker.submit(sym, qty, close_side, "day", coid)
                print(f"         -> closing at market ({hit} already reached): "
                      f"{o['id']} ({o['status']})")
                rows.append({
                    "event": "exit_submitted",
                    "symbol": sym,
                    "entry_for_date": entry_for_date,
                    "date": today,
                    "submitted_at": datetime.now(timezone.utc).isoformat(),
                    "exit_kind": "immediate",
                    "side": side,
                    "qty": qty,
                    "avg_entry_price": entry_px,
                    "reached": hit,
                    "take_profit": b["take_profit"],
                    "stop_loss": b["stop_loss"],
                    "order_id": o["id"],
                    "client_order_id": coid,
                    "status": o["status"],
                })
            except broker.BrokerError as e:
                print(f"         FAILED to close: {e}")
                failures.append((sym, f"immediate close failed: {e}"))
            continue

        coid = f"oco-{today}-{sym}"
        try:
            o = broker.submit_oco(
                sym, qty, close_side, b["take_profit"], b["stop_loss"], coid
            )
            print(f"         -> bracket resting: {o['id']} ({o['status']})")
            rows.append({
                "event": "bracket_armed",
                "symbol": sym,
                "entry_for_date": entry_for_date,
                "date": today,
                "armed_at": datetime.now(timezone.utc).isoformat(),
                "side": side,
                "qty": qty,
                "entry_price": entry_px,
                "take_profit": b["take_profit"],
                "stop_loss": b["stop_loss"],
                "distance_pct": b["distance_pct"],
                "distance_dollars": b["distance_dollars"],
                "multiple": b["multiple"],
                "annual_vol_pct": b["annual_vol_pct"],
                "expected_pnl": exits.expected_pnl(qty, b),
                "order_id": o["id"],
                "client_order_id": coid,
                "status": o["status"],
            })
        except broker.BrokerError as e:
            print(f"         FAILED to arm: {e}")
            failures.append((sym, str(e)))

    if args.dry_run:
        print("\n--dry-run: nothing sent")
        return 0

    tradelog.append(rows)
    if rows:
        print(f"\nrecorded {len(rows)} event(s)")

    if failures:
        print(f"\n{len(failures)} position(s) left UNPROTECTED - no take-profit, "
              f"no stop:")
        for sym, err in failures:
            print(f"  {sym}: {err}")
        print("The next session's arming run will retry.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
