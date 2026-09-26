"""
Grade any un-graded predictions in logs/predictions.jsonl by comparing
the predicted direction to the actual close on the prediction's for_date.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import truststore
from dotenv import load_dotenv

truststore.inject_into_ssl()

load_dotenv()

HEADERS = {
    "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
    "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
}
LOG = Path(__file__).parent / "logs" / "predictions.jsonl"


def bars_around(symbol: str, target_date: str) -> dict[str, dict]:
    end = datetime.fromisoformat(target_date).replace(tzinfo=timezone.utc) + timedelta(
        days=2
    )
    start = end - timedelta(days=14)
    r = requests.get(
        f"https://data.alpaca.markets/v2/stocks/{symbol}/bars",
        headers=HEADERS,
        params={
            "timeframe": "1Day",
            "start": start.strftime("%Y-%m-%d"),
            "end": end.strftime("%Y-%m-%d"),
            "limit": 30,
            "adjustment": "all",
            "feed": "iex",
        },
        timeout=30,
    )
    r.raise_for_status()
    return {b["t"][:10]: b for b in r.json().get("bars", [])}


def main() -> None:
    if not LOG.exists():
        print("No prediction log yet — nothing to grade.")
        return

    lines = [l for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    graded_count = 0

    for i, line in enumerate(lines):
        rec = json.loads(line)
        if rec.get("outcome") is not None:
            continue

        symbol = rec["symbol"]
        for_date = rec["for_date"]
        try:
            bars = bars_around(symbol, for_date)
        except Exception as e:
            print(f"  skip {for_date}: fetch failed ({e})")
            continue

        target = bars.get(for_date)
        if target is None:
            print(f"  skip {for_date}: no bar yet (market not closed?)")
            continue

        dates = sorted(bars.keys())
        idx = dates.index(for_date)
        if idx == 0:
            print(f"  skip {for_date}: no prior bar found")
            continue
        prev = bars[dates[idx - 1]]

        actual_dir = "up" if target["c"] > prev["c"] else "down"
        pct = (target["c"] - prev["c"]) / prev["c"] * 100
        pred_dir = rec["prediction"]["direction"]

        rec["outcome"] = {
            "actual_close": target["c"],
            "prev_close": prev["c"],
            "actual_direction": actual_dir,
            "actual_pct": round(pct, 4),
            "correct": actual_dir == pred_dir,
            "always_up_correct": actual_dir == "up",
            "prev_day_baseline_correct": actual_dir
            == rec["baselines"]["prev_day_direction"],
            "graded_at": datetime.now(timezone.utc).isoformat(),
        }
        lines[i] = json.dumps(rec)
        graded_count += 1
        mark = "OK" if rec["outcome"]["correct"] else "X "
        print(
            f"  {for_date}  pred:{pred_dir}@{rec['prediction']['confidence']}%  "
            f"actual:{actual_dir} ({pct:+.2f}%)  {mark}"
        )

    if graded_count:
        LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Graded {graded_count} prediction(s).")


if __name__ == "__main__":
    main()
