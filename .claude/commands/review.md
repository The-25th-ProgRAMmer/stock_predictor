---
description: Weekly human review — grade any open predictions, then print accuracy + calibration stats and a human summary.
---

You are helping the human reviewer assess the trading agent's performance for the week.

## 1. Grade any open predictions

Run: `python grade.py`

## 2. Print the stats

Run: `python review.py`

Report the raw output verbatim.

## 3. Give a short human summary (max 6 bullets)

Based ONLY on the stats printed by review.py, tell the reviewer:

- Sample size: how many graded predictions total, and how many since the last review (if the log is longer than 20, use the last 20 as "this week").
- Vs. baselines: is the agent beating "always up" and "prev day direction"? By how much? (Point out if it's below either baseline — that's a red flag.)
- Calibration: do the high-confidence buckets (70%+) actually have higher accuracy than the low ones? If not, confidence numbers are noise.
- Any obvious pattern in recent misses (e.g. all wrong on down days, all wrong when RSI was high). Only mention if visible from the recent-10 list; do NOT invent patterns.
- One concrete question the human should think about before deciding whether to change the prompt.

Do NOT:
- Recommend prompt changes yourself. That's the human's job.
- Congratulate the agent on small samples (<30 graded).
- Claim the agent has "edge" unless it beats both baselines by a margin larger than random noise over 50+ predictions.
