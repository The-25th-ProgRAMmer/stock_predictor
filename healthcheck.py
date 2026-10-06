"""
Did the paper-trading morning actually happen?

Run by a cloud routine after the open, from a fresh pull of main. The routine
cannot reach Alpaca, so this reads what it can: GitHub's public Actions API for
the push-triggered "open" run, and the logs that run commits back. GitHub only
serves run *logs* to signed-in users, so step results stand in for them.

    PASS  predictions logged, the open run fired inside the entry window and
          succeeded, every entry has a resting bracket, equity was snapshotted
    WARN  nothing broke, but no new entry went in although some predicted
          symbols were not already held - worth a look, not necessarily a bug
    FAIL  something a person has to fix

Exit status is 1 on FAIL, 0 otherwise.

    python healthcheck.py                 # today (UTC)
    python healthcheck.py --date 2026-10-07
"""

import argparse
import json
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import goal
import tradelog

API = "https://api.github.com/repos/The-25th-ProgRAMmer/stock_predictor"
PREDICTIONS = Path(__file__).parent / "logs" / "predictions.jsonl"
NY = ZoneInfo("America/New_York")
OPEN_STEPS = (
    "Enter positions",
    "Wait for the opening fills",
    "Arm take-profit and stop",
    "Reconcile fills and P&L",
)


def gh(path: str) -> dict:
    req = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def entry_window(day: date) -> tuple[datetime, datetime]:
    """When enter_trades.py will act: 75..3 minutes before the 09:30 ET open."""
    opens = datetime.combine(day, time(9, 30), NY).astimezone(timezone.utc)
    return opens - timedelta(minutes=75), opens - timedelta(minutes=3)


def utc(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def check(day: date) -> tuple[list[str], list[str], list[str]]:
    iso = day.isoformat()
    fails, warns, notes = [], [], []

    preds = []
    if PREDICTIONS.exists():
        preds = [json.loads(l) for l in PREDICTIONS.read_text(encoding="utf-8").splitlines() if l.strip()]
    predicted = {p["symbol"] for p in preds if p["for_date"] == iso}
    if predicted:
        notes.append(f"{len(predicted)} predictions logged for {iso}")
    else:
        fails.append(f"no predictions logged for {iso}: the predictor routine did not run "
                     "or did not push (or the market is closed today)")

    lo, hi = entry_window(day)
    run = None
    runs = gh(f"/actions/workflows/paper-trade.yml/runs?event=push&created={iso}&per_page=30")["workflow_runs"]
    in_window = [r for r in runs if lo <= utc(r["created_at"]) <= hi]
    if not runs:
        fails.append("no push-triggered Paper Trade run today: the predictions push did not "
                     "start the trade workflow")
    elif not in_window:
        first = min(utc(r["created_at"]) for r in runs)
        fails.append(f"the trade run started {first:%H:%M} UTC, outside the entry window "
                     f"{lo:%H:%M}-{hi:%H:%M} UTC, so opening orders were refused: the "
                     "predictions landed late")
    else:
        run = min(in_window, key=lambda r: r["created_at"])
        notes.append(f"open run started {utc(run['created_at']):%H:%M} UTC "
                     f"(window {lo:%H:%M}-{hi:%H:%M}): {run['html_url']}")
        if run["status"] != "completed":
            fails.append(f"the open run is still {run['status']}")
        elif run["conclusion"] != "success":
            fails.append(f"the open run ended '{run['conclusion']}'")
        steps = {s["name"]: s["conclusion"] for j in gh(f"/actions/runs/{run['id']}/jobs")["jobs"]
                 for s in j["steps"]}
        for name in OPEN_STEPS:
            if steps.get(name) != "success":
                fails.append(f"step '{name}' was {steps.get(name) or 'missing'}")

    rows = tradelog.load_rows()
    brackets = tradelog.of_event(rows, "bracket_armed")
    exits = tradelog.of_event(rows, "exit_submitted")
    still_open = tradelog.unreconciled(rows)
    entered = sorted(k[0] for k in tradelog.of_event(rows, "entry_submitted") if k[1] == iso)
    if entered:
        notes.append(f"entered today: {', '.join(entered)}")

    unprotected = sorted(f"{s} ({d})" for (s, d) in still_open if (s, d) not in brackets and (s, d) not in exits)
    if unprotected:
        fails.append("no take-profit/stop on record for: " + ", ".join(unprotected)
                     + " - either the entry never filled or the position is unprotected; "
                       "check Alpaca")

    held = {s for (s, d) in still_open if d < iso}
    if run and predicted and not entered:
        unheld = sorted(predicted - held)
        if unheld:
            warns.append(f"no new entries, though {', '.join(unheld)} were predicted and not "
                         "held; sizing or exposure caps can skip a symbol, so check the "
                         "'Enter positions' step on the run page")
        else:
            notes.append("no new entries: every predicted symbol is already held")

    if not any(s["date"] == iso for s in goal.snapshots()):
        fails.append(f"no equity snapshot dated {iso}: reconcile did not run or did not commit")

    return fails, warns, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="session to check, YYYY-MM-DD (default: today UTC)")
    day = date.fromisoformat(ap.parse_args().date or datetime.now(timezone.utc).date().isoformat())

    if day.weekday() >= 5:
        print(f"HEALTH CHECK {day}: weekend, nothing to check")
        return 0

    fails, warns, notes = check(day)
    verdict = "FAIL" if fails else "WARN" if warns else "PASS"
    print(f"HEALTH CHECK {day}: {verdict}")
    for label, items in (("FAIL", fails), ("WARN", warns), ("ok", notes)):
        for i in items:
            print(f"  [{label}] {i}")
    print()
    print("\n".join(goal.progress_lines()))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
