"""
Submit opening-auction entries for today's predictions.

Runs in GitHub Actions before the open (the cloud routine cannot reach Alpaca).
Reads today's predictions, scores the trend, resolves target positions, and
submits whole-share market-on-open orders.

Idempotent: every order carries a deterministic client_order_id of
    entry-<for_date>-<symbol>
so a second run on the same day is rejected by Alpaca as a duplicate rather
than doubling the position.

Entries are no longer flattened at the close - each one gets a take-profit and a
stop (arm_exits.py) and is held until one of them trades or the time stop fires
(sweep_exits.py). A symbol already in the book is therefore skipped here rather
than blocking the whole run, and the exposure caps are checked against held
positions plus new orders rather than the new batch alone.

Dry run:  python enter_trades.py --dry-run     (no orders sent)
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import broker
import position
import sizing
import tradelog
from trend_score import load_bars, score_all
from universe import TARGETS

LOG = Path(__file__).parent / "logs" / "predictions.jsonl"

TARGET_RISK_PCT = 2.0   # picked once, held fixed (see sizing.py)
MAX_MINUTES_BEFORE_OPEN = 75   # don't run hours early
MIN_MINUTES_BEFORE_OPEN = 3    # opg orders must be in before ~9:28 ET


def todays_predictions(for_date: str) -> dict[str, dict]:
    if not LOG.exists():
        return {}
    out = {}
    for line in LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["for_date"] == for_date:
            out[r["symbol"]] = r
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="resolve and print, send nothing")
    ap.add_argument("--force", action="store_true", help="skip the pre-open timing window check")
    args = ap.parse_args()

    acct = broker.assert_paper_account()
    portfolio = float(acct["equity"])
    print(f"paper account {acct['account_number']}  equity ${portfolio:,.2f}")

    clk = broker.clock()
    now = datetime.fromisoformat(clk["timestamp"])
    nxt = datetime.fromisoformat(clk["next_open"])
    minutes = (nxt - now).total_seconds() / 60

    if clk["is_open"]:
        print("market is already open - opening-auction orders are no longer accepted")
        if not args.force and not args.dry_run:
            return 0
    elif not args.force and not args.dry_run:
        if minutes > MAX_MINUTES_BEFORE_OPEN:
            print(f"next open is {minutes:.0f} min away (> {MAX_MINUTES_BEFORE_OPEN}) - too early, exiting")
            return 0
        if minutes < MIN_MINUTES_BEFORE_OPEN:
            print(f"next open is {minutes:.0f} min away (< {MIN_MINUTES_BEFORE_OPEN}) - too late, exiting")
            return 0

    # A forced run during the session is a manual catch-up after a missed open:
    # the auction is gone, so enter at market in the regular session instead.
    tif = "day" if clk["is_open"] else "opg"
    for_date = nxt.date().isoformat() if not clk["is_open"] else now.date().isoformat()
    print(f"trading session {for_date} (opens in {minutes:.0f} min)")

    # Positions are held until their bracket or the time stop closes them, so a
    # symbol already in the book is skipped rather than added to. Never stack:
    # a second entry would blend the average price the bracket is anchored on.
    existing = broker.positions()
    held = {p["symbol"] for p in existing}
    working = {o["symbol"] for o in broker.open_orders()}
    busy = held | working
    if busy:
        print(f"already in the book, skipping: {', '.join(sorted(busy))}")
        if held:
            gross = sum(abs(float(p.get("market_value", 0) or 0)) for p in existing)
            print(f"  {len(held)} position(s) held, gross ${gross:,.0f}")

    preds = todays_predictions(for_date)
    if not preds:
        print(f"no predictions logged for {for_date} - nothing to trade")
        return 0
    print(f"{len(preds)} predictions for {for_date}")

    tradeable = [s for s in TARGETS if s not in busy]
    if not tradeable:
        print("every target is already in the book - nothing to do")
        return 0

    bars = load_bars()
    trends = score_all(tradeable, bars)
    vols = sizing.size_universe(tradeable, bars, TARGET_RISK_PCT, portfolio)
    base = {s: vols[s]["full_notional"] for s in tradeable}
    targets = position.resolve_all(tradeable, preds, trends, base)

    print()
    print(position.summarise(targets))
    print()

    live = [t for t in targets if t.qty > 0]
    if not live:
        print("no new positions to take today")
        return 0

    broker.check_batch(
        [{"symbol": t.symbol, "notional_estimate": t.notional_actual} for t in live],
        existing=existing,
    )
    print(f"safety checks passed for {len(live)} order(s)")

    if args.dry_run:
        print("\n--dry-run: no orders sent")
        return 0

    rows, failures = [], []
    for t in live:
        side = "buy" if t.side == "long" else "sell"
        coid = f"entry-{for_date}-{t.symbol}"
        try:
            o = broker.submit(t.symbol, t.qty, side, tif, coid)
            print(f"  {t.symbol:<6} {side:<4} {t.qty:>4} {tif:<4} -> {o['id']} ({o['status']})")
            rows.append({
                "event": "entry_submitted",
                "for_date": for_date,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "symbol": t.symbol,
                "side": t.side,
                "qty": t.qty,
                "order_id": o["id"],
                "client_order_id": coid,
                "status": o["status"],
                "time_in_force": tif,
                "target_risk_pct": TARGET_RISK_PCT,
                "annual_vol_pct": vols[t.symbol]["annual_vol_pct"],
                "trend_score": t.trend_score,
                "size_fraction": t.size_fraction,
                "predicted": t.predicted,
                "confidence": t.confidence,
                "reference_price": t.reference_price,
                "notional_target": t.notional_target,
                "reason": t.reason,
            })
        except broker.BrokerError as e:
            print(f"  {t.symbol:<6} FAILED: {e}")
            failures.append((t.symbol, str(e)))

    if rows:
        tradelog.append(rows)
        print(f"\nrecorded {len(rows)} entry order(s) to {tradelog.TRADES.name}")
        print("arm_exits.py attaches the take-profit / stop pair once these fill")
    if failures:
        print(f"\n{len(failures)} order(s) failed:")
        for sym, err in failures:
            print(f"  {sym}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
