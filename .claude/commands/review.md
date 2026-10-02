---
description: Weekly human review — grade any open predictions, then print accuracy + calibration stats and a human summary.
---

You are helping the human reviewer assess the trading agent's performance for the week.

## 1. Grade any open predictions

Run: `python grade.py`

## 2. Print the stats

Run: `python review.py`

Report the raw output verbatim.

## 3. Give a short human summary (max 7 bullets)

Based ONLY on the stats printed by review.py, tell the reviewer:

- **Sample size, honestly.** Report graded predictions *and* distinct trading days. Same-day calls across correlated symbols are not independent tests, so the distinct-day count is the conservative sample size for any pooled number. Lead with whichever is less flattering.
- **Per symbol vs. baselines.** Use the PER SYMBOL table, not the pooled line. Which symbols beat "always up" and "prev day"? Which are below either baseline — that's a red flag. Note that `always_up` is a harder baseline on the megacaps than on SPY.
- **Independence.** If the same-direction rate is high, the agent is making one market call stamped across ten rows. Say so plainly; the extra symbols are then buying volume, not information.
- **Drift.** If the agent's up-rate is far from ~50%, it may be drifting rather than predicting. Check it against the `always_up` column.
- **Calibration.** Do the 70%+ buckets actually beat the low ones? If not, the confidence numbers are noise.
- **Patterns in recent misses** — only if visible in the recent-graded list. Do NOT invent patterns.
- **One concrete question** the human should think about before deciding whether to change the prompt.

Do NOT:
- Recommend prompt changes yourself. That's the human's job.
- Congratulate the agent on small samples. The bar is 30+ graded **per symbol**, not pooled.
- Quote the pooled accuracy without the distinct-day count beside it.
- Claim the agent has "edge" unless a symbol beats both baselines by a margin larger than noise over 50+ predictions for that symbol.
