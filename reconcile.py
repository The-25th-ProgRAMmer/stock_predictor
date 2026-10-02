"""
Pair each entry with its exit and record the realised P&L.

Most exits are not submitted by anything in this repo: the take-profit / stop
pair rests at Alpaca and one leg trades whenever the price gets there, possibly
days later and with no process running. So reconciliation cannot read exits out
of our own log - it asks Alpaca what became of the bracket order recorded in
`bracket_armed`, and falls back to the log only for exits we did send ourselves
(the time stop, or a manual flatten).

Each round trip records how it ended, in `exit_kind`:

    target      the take-profit limit traded
    stop        the stop traded
    time_stop   held MAX_HOLD_SESSIONS sessions and flattened at the close
    immediate   had already run past a level before it could be bracketed
    manual      flattened by hand with exit_trades.py

That breakdown is the point of the log. Target and stop are the same distance
from entry, so target-vs-stop counts read directly as a hit rate, and comparing
it against the graded prediction accuracy shows whether the directional calls
survive contact with a real exit policy.

`went_predicted_way` is deliberately not called "prediction_correct": the
prediction is a one-session call, and a position held for weeks long outlives it.
Read it together with `hold_sessions`; it only grades the prediction when the
hold was short.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone

import broker
import tradelog

EXIT_KIND_BY_TYPE = {"limit": "target", "stop": "stop", "stop_limit": "stop"}


def fill_info(order_id: str) -> dict:
    try:
        o = broker.get_order(order_id)
    except broker.BrokerError as e:
        return {"error": str(e)}
    return _fill(o)


def _fill(o: dict) -> dict:
    return {
        "status": o.get("status"),
        "type": o.get("type"),
        "filled_qty": float(o.get("filled_qty") or 0),
        "filled_avg_price": (
            float(o["filled_avg_price"]) if o.get("filled_avg_price") else None
        ),
        "filled_at": o.get("filled_at"),
    }


def bracket_fill(order_id: str) -> dict | None:
    """
    The filled half of a one-cancels-other pair, if either has traded.

    Alpaca returns the pair as a parent plus child legs, and which of the three
    carries the fill depends on the order class, so all of them are checked.
    """
    try:
        parent = broker.get_order(order_id, nested=True)
    except broker.BrokerError:
        return None
    for o in [parent, *(parent.get("legs") or [])]:
        if o.get("status") == "filled":
            return _fill(o)
    return None


def main() -> int:
    broker.assert_paper_account()
    rows = tradelog.load_rows()
    if not rows:
        print("no trade log yet - nothing to reconcile")
        return 0

    brackets = tradelog.of_event(rows, "bracket_armed")
    explicit = tradelog.of_event(rows, "exit_submitted")
    pending = tradelog.unreconciled(rows)

    if not pending:
        print("every entry is already reconciled")
    new = []
    for key, entry in sorted(pending.items(), key=lambda kv: kv[1]["submitted_at"]):
        sym, for_date = key
        ef = fill_info(entry["order_id"])
        if ef.get("status") != "filled":
            print(f"  {sym:<6} {for_date}  entry not filled "
                  f"({ef.get('status') or ef.get('error')})")
            continue

        xrow = explicit.get(key)
        if xrow:
            xf = fill_info(xrow["order_id"])
            kind = xrow.get("exit_kind", "manual")
        else:
            b = brackets.get(key)
            xf = bracket_fill(b["order_id"]) if b else None
            kind = EXIT_KIND_BY_TYPE.get((xf or {}).get("type"), "target") if xf else None

        if not xf or xf.get("status") != "filled":
            state = "still open" if not xf else (xf.get("status") or xf.get("error"))
            print(f"  {sym:<6} {for_date}  {state}")
            continue

        qty = min(ef["filled_qty"], xf["filled_qty"])
        entry_px, exit_px = ef["filled_avg_price"], xf["filled_avg_price"]
        if not qty or entry_px is None or exit_px is None:
            print(f"  {sym:<6} {for_date}  missing fill price")
            continue

        direction = 1 if entry["side"] == "long" else -1
        pnl = (exit_px - entry_px) * qty * direction
        ret_pct = (exit_px - entry_px) / entry_px * 100 * direction

        entry_day = (ef["filled_at"] or "")[:10] or for_date
        exit_day = (xf["filled_at"] or "")[:10] or entry_day
        try:
            cal = broker.calendar(entry_day, exit_day)
            held = len([c for c in cal if entry_day < c["date"] <= exit_day])
        except broker.BrokerError:
            held = None

        # How much of the graded close-to-close move happened overnight, before
        # the window we could actually trade.
        ref = entry.get("reference_price")
        gap_pct = ((entry_px - ref) / ref * 100) if ref else None
        b = brackets.get(key) or {}

        row = {
            "event": "round_trip",
            "symbol": sym,
            "entry_for_date": for_date,
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
            "exit_kind": kind,
            "side": entry["side"],
            "qty": qty,
            "entry_price": entry_px,
            "exit_price": exit_px,
            "entry_at": ef["filled_at"],
            "exit_at": xf["filled_at"],
            "hold_sessions": held,
            "pnl": round(pnl, 2),
            "return_pct": round(ret_pct, 4),
            "take_profit": b.get("take_profit"),
            "stop_loss": b.get("stop_loss"),
            "distance_pct": b.get("distance_pct"),
            "prior_close": ref,
            "gap_pct": round(gap_pct, 4) if gap_pct is not None else None,
            "predicted": entry.get("predicted"),
            "confidence": entry.get("confidence"),
            "trend_score": entry.get("trend_score"),
            "size_fraction": entry.get("size_fraction"),
            "annual_vol_pct": entry.get("annual_vol_pct"),
            "went_predicted_way": (
                None if not entry.get("predicted")
                else (exit_px > entry_px) == (entry["predicted"] == "up")
            ),
        }
        new.append(row)
        print(f"  {sym:<6} {for_date}  {entry['side']:<5} {qty:>4} "
              f"{entry_px:>8.2f} -> {exit_px:>8.2f}  {kind:<9} "
              f"held {str(held) if held is not None else '?':>2}s  "
              f"P&L ${pnl:>+9.2f} ({ret_pct:>+6.2f}%)"
              + (f"  gap {gap_pct:>+5.2f}%" if gap_pct is not None else ""))

    if new:
        tradelog.append(new)
        print(f"\nreconciled {len(new)} round trip(s), "
              f"total P&L ${sum(r['pnl'] for r in new):+,.2f}")

    all_rt = [r for r in tradelog.load_rows() if r["event"] == "round_trip"]
    if all_rt:
        total = sum(r["pnl"] for r in all_rt)
        wins = sum(1 for r in all_rt if r["pnl"] > 0)
        kinds = Counter(r.get("exit_kind") or "?" for r in all_rt)
        by_sym = defaultdict(float)
        for r in all_rt:
            by_sym[r["symbol"]] += r["pnl"]
        holds = [r["hold_sessions"] for r in all_rt if r.get("hold_sessions") is not None]

        print(f"\nall-time: {len(all_rt)} round trips, {wins} profitable "
              f"({wins / len(all_rt) * 100:.0f}%), total P&L ${total:+,.2f}")
        print("  ended by:   " + "  ".join(f"{k}:{n}" for k, n in kinds.most_common()))
        if holds:
            print(f"  hold:       avg {sum(holds) / len(holds):.1f} sessions, "
                  f"max {max(holds)}")
        print("  per symbol: " + "  ".join(
            f"{s}:{p:+,.0f}" for s, p in sorted(by_sym.items(), key=lambda x: -x[1])))

        # Target and stop sit the same distance from entry, so these two counts
        # are a hit rate rather than a reward-to-risk puzzle.
        t, s = kinds.get("target", 0), kinds.get("stop", 0)
        if t + s:
            print(f"  bracket hit rate: {t}/{t + s} = {t / (t + s) * 100:.0f}% "
                  f"(target and stop are equidistant, so >50% is the bar)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
