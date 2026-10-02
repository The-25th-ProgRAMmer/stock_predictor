"""
Submit opening-auction entries for today's predictions.

Runs in GitHub Actions before the open (the cloud routine cannot reach Alpaca).
Reads today's predictions, scores the trend, resolves target positions, and
submits whole-share market-on-open orders.

Idempotent: every order carries a deterministic client_order_id of
    entry-<for_date>-<symbol>
so a second run on the same day is rejected by Alpaca as a duplicate rather
than doubling the position. The script also exits early if it finds existing
positions or open orders for the day.

Dry run:  python enter_trades.py --dry-run     (no orders sent)
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import broker
import position
import sizing
from trend_score import load_bars, score_all
from universe import TARGETS

LOG = Path(__file__).parent / "logs" / "predictions.jsonl"
TRADES = Path(__file__).parent / "logs" / "trades.jsonl"

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


def record(rows: list[dict]) -> None:
    TRADES.parent.mkdir(exist_ok=True)
    with TRADES.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


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

    for_date = nxt.date().isoformat() if not clk["is_open"] else now.date().isoformat()
    print(f"trading session {for_date} (opens in {minutes:.0f} min)")

    existing = broker.positions()
    if existing:
        print(f"REFUSING: {len(existing)} position(s) already open: "
              f"{', '.join(p['symbol'] for p in existing)}")
        print("The previous session did not flatten. Resolve before trading again.")
        return 1

    preds = todays_predictions(for_date)
    if not preds:
        print(f"no predictions logged for {for_date} - nothing to trade")
        return 0
    print(f"{len(preds)} predictions for {for_date}")

    bars = load_bars()
    trends = score_all(TARGETS, bars)
    vols = sizing.size_universe(TARGETS, bars, TARGET_RISK_PCT, portfolio)
    base = {s: vols[s]["full_notional"] for s in TARGETS}
    targets = position.resolve_all(TARGETS, preds, trends, base)

    print()
    print(position.summarise(targets))
    print()

    live = [t for t in targets if t.qty > 0]
    if not live:
        print("no positions to take today")
        return 0

    broker.check_batch([
        {"symbol": t.symbol, "notional_estimate": t.notional_actual} for t in live
    ])
    print(f"safety checks passed for {len(live)} order(s)")

    if args.dry_run:
        print("\n--dry-run: no orders sent")
        return 0

    rows, failures = [], []
    for t in live:
        side = "buy" if t.side == "long" else "sell"
        coid = f"entry-{for_date}-{t.symbol}"
        try:
            o = broker.submit(t.symbol, t.qty, side, "opg", coid)
            print(f"  {t.symbol:<6} {side:<4} {t.qty:>4} opg  -> {o['id']} ({o['status']})")
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
        record(rows)
        print(f"\nrecorded {len(rows)} entry order(s) to {TRADES.name}")
    if failures:
        print(f"\n{len(failures)} order(s) failed:")
        for sym, err in failures:
            print(f"  {sym}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
