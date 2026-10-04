"""
The paper account's October goal, and progress toward it.

    start   $100,000
    target  $105,000  (+5%) by the close on 2026-10-30

The goal is a yardstick for the reviews, NOT an input to trading. Nothing here
feeds sizing, entries or exits: TARGET_RISK_PCT in enter_trades.py stays where it
is whether the account is ahead of pace or behind it. Raising risk to catch up
is how a paper account that "nearly made it" turns into a live account that
blows up, and it would also make October's result useless as evidence of what
the unmodified strategy does.

Equity comes from logs/equity.jsonl, one snapshot per reconcile run, written by
reconcile.py in GitHub Actions (the only place that can reach Alpaca). The cloud
routine only reads it.
"""

import json
from datetime import date, timedelta
from pathlib import Path

START_EQUITY = 100_000.0
TARGET_EQUITY = 105_000.0
GOAL_START = "2026-10-01"
GOAL_END = "2026-10-30"   # last trading session of October 2026

EQUITY_LOG = Path(__file__).parent / "logs" / "equity.jsonl"
TRADES_LOG = Path(__file__).parent / "logs" / "trades.jsonl"


def snapshots() -> list[dict]:
    """One snapshot per date - the latest taken that day - oldest first."""
    if not EQUITY_LOG.exists():
        return []
    by_date = {}
    for line in EQUITY_LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            s = json.loads(line)
            by_date[s["date"]] = s
    return [by_date[d] for d in sorted(by_date)]


def sessions_between(after: str, through: str) -> int:
    """Weekdays in (after, through]. No US exchange holiday falls in October."""
    d, end, n = date.fromisoformat(after), date.fromisoformat(through), 0
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def round_trips(since: str | None = None) -> list[dict]:
    if not TRADES_LOG.exists():
        return []
    rows = [
        json.loads(l)
        for l in TRADES_LOG.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    rts = [r for r in rows if r.get("event") == "round_trip"]
    if since:
        rts = [r for r in rts if (r.get("exit_at") or "")[:10] >= since]
    return rts


def trade_lines(rts: list[dict], label: str) -> list[str]:
    """Closed-trade P&L: the measure of 'more profitable trades than losing ones'."""
    if not rts:
        return [f"  {label}: no closed trades yet"]
    wins = [r for r in rts if r["pnl"] > 0]
    losses = [r for r in rts if r["pnl"] <= 0]
    gross_win = sum(r["pnl"] for r in wins)
    gross_loss = -sum(r["pnl"] for r in losses)
    lines = [
        f"  {label}: {len(rts)} closed, {len(wins)} profitable / {len(losses)} not "
        f"({len(wins) / len(rts):.0%} win rate), net ${sum(r['pnl'] for r in rts):+,.2f}",
    ]
    if wins:
        lines.append(f"    avg win  ${gross_win / len(wins):+,.2f}")
    if losses:
        lines.append(f"    avg loss ${-gross_loss / len(losses):+,.2f}")
    if gross_loss:
        lines.append(f"    profit factor {gross_win / gross_loss:.2f} "
                     f"(gross wins / gross losses; above 1.0 makes money)")
    for r in sorted(rts, key=lambda x: x.get("exit_at") or ""):
        lines.append(
            f"    {(r.get('exit_at') or '?')[:10]}  {r['symbol']:<6} {r['side']:<5} "
            f"{r.get('exit_kind') or '?':<9} held {r.get('hold_sessions', '?')}s  "
            f"${r['pnl']:>+9,.2f} ({r['return_pct']:+.2f}%)"
        )
    return lines


def progress_lines() -> list[str]:
    target_pct = (TARGET_EQUITY / START_EQUITY - 1) * 100
    lines = [
        f"  Goal: ${START_EQUITY:,.0f} -> ${TARGET_EQUITY:,.0f} "
        f"(+{target_pct:.1f}%) by {GOAL_END}",
    ]
    snaps = snapshots()
    if not snaps:
        lines.append("  No equity snapshot yet - reconcile.py writes one on its "
                     "next Actions run.")
        return lines

    last = snaps[-1]
    eq = float(last["equity"])
    gained = eq - START_EQUITY
    to_go = TARGET_EQUITY - eq
    progress = gained / (TARGET_EQUITY - START_EQUITY)
    peak = max(float(s["equity"]) for s in snaps)
    drawdown = (eq / peak - 1) * 100

    lines += [
        f"  Equity {last['date']}: ${eq:,.2f}  ({gained / START_EQUITY * 100:+.2f}%, "
        f"{progress:.0%} of the way)",
        f"  Peak ${peak:,.2f}, now {drawdown:+.2f}% from peak",
    ]
    if to_go <= 0:
        lines.append("  TARGET REACHED. Keep trading the same rules - do not "
                     "change risk now that the number is hit.")
        return lines

    left = sessions_between(last["date"], GOAL_END)
    if left <= 0:
        lines.append(f"  Goal window closed ${to_go:,.2f} short.")
        return lines

    need = ((TARGET_EQUITY / eq) ** (1 / left) - 1) * 100
    lines.append(f"  ${to_go:,.2f} to go over {left} session(s): needs "
                 f"{need:+.3f}% per session, compounded")

    in_window = [s for s in snaps if s["date"] >= GOAL_START]
    if len(in_window) >= 2:
        first = in_window[0]
        n = sessions_between(first["date"], last["date"])
        if n:
            realised = ((eq / float(first["equity"])) ** (1 / n) - 1) * 100
            verdict = "on pace" if realised >= need else "behind pace"
            lines.append(f"  Realised so far {realised:+.3f}% per session - {verdict}")
    lines.append("  (Pace is a yardstick only; it never changes position size.)")
    return lines
