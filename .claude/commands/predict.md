---
description: Grade any prior predictions, then predict tomorrow's direction for every target symbol and log them.
---

You are the trading agent's predictor. This command runs once per trading day (typically at or after market close). Follow these steps in order — do not skip, do not add extra steps.

## 1. Grade prior predictions

Run: `python grade.py`

Report the output verbatim in one line if anything was graded.

## 2. Gather today's context

Run: `python predict.py`

This prints a single JSON object on stdout. Parse it. Do NOT invent additional data. Its parts:

- `targets` — the symbols you must predict. One prediction each, no more, no fewer.
- `target_data` — per-symbol price action and indicators.
- `context_data` — cross-asset background (volatility, metals, bonds, credit, dollar, energy, breadth, individual semis). **Never predict these.** They exist to inform the target calls.
- `cross_asset` — derived features: breadth counts, ratios, `risk_on_score`, correlations.

## 3. Read the market backdrop once

Before looking at any single symbol, form one view from `cross_asset`:

- `risk_on_score` — 0–1 is risk-off, 4–5 is risk-on. Check `components` to see which signals disagree.
- `HYG/LQD` — credit risk appetite. Credit usually leads equities.
- `IWM/SPY` — breadth. Falling while SPY rises means a narrow, fragile tape.
- `QQQ/SPY` — tech leadership vs. narrowness. An extreme percentile here is a crowding signal.
- `mag7_breadth` / `semi_breadth` — how broad today's move actually was (`up_today` out of `n`).
- `volatility` — `VIXY_percentile_60d` and `VIXY_pct_1d`, plus `SPY_realized_vol_20d_annualized_pct`. Read VIXY's *change and percentile*, never its level: it bleeds to contango.
- `correlations_20d_vs_SPY` — when SPY's correlation to GLD or TLT is unusually high, the normal risk-on/risk-off reads are less reliable. Say so and lower confidence.

## 4. Analyze each target

For each symbol in `targets`, use its own `target_data`:

- **Trend**: sign of `close_vs_sma20_pct` and `close_vs_sma50_pct`; `sma20_vs_sma50`.
- **Momentum**: `rsi14` (overbought >70, oversold <30), `macd_hist`.
- **Volatility**: `atr14_pct_of_close`.
- **Recent action**: `prev_day_direction`, `prev_day_pct`, `pct_5d`, `pct_20d`, `last_3_days`.
- **News**: items in `news` tagged with that symbol. Ignore items clearly unrelated to equities.

Confidence: 50–60 when signals conflict, 60–75 moderate agreement, 75–85 strong agreement, above 85 only when near unanimous.

Two rules that matter more than accuracy:

1. **Be honest.** A coin flip must be logged as a coin flip. The point of this phase is to find out whether the predictor beats its baselines; inflated confidence corrupts the experiment.

2. **Do not stamp one market call onto all ten symbols.** If the backdrop is the only thing driving every prediction, say so in the reasoning and keep confidences near 50. Where a symbol's own indicators or news diverge from the backdrop, let them diverge. `weekly_report.py` measures how often you called every symbol the same direction; a high rate means the extra symbols are adding nothing.

## 5. Compose the predictions JSON

Build a JSON **array**, one object per target symbol:

```json
[
  {
    "made_at": "<ISO 8601 UTC timestamp, e.g. 2026-10-02T21:05:00Z>",
    "for_date": "<value of context.for_date>",
    "symbol": "<target symbol>",
    "prediction": {
      "direction": "up" | "down",
      "confidence": <integer 50..100>,
      "reasoning": "<2-3 sentences citing specific numeric values for THIS symbol>",
      "key_signals": ["<short signal 1>", "<short signal 2>"],
      "sources": ["<news url if any>"]
    },
    "baselines": {
      "always_up": "up",
      "prev_day_direction": "<that symbol's prev_day_direction>"
    },
    "context_snapshot": {
      "latest_close": <number>,
      "sma20": <number>,
      "sma50": <number>,
      "rsi14": <number>,
      "macd_hist": <number>,
      "risk_on_score": <cross_asset.risk_on_score.score>
    },
    "outcome": null
  }
]
```

Rules:
- `outcome` MUST be `null`. Grading happens tomorrow.
- Do not add fields the schema does not list.
- `reasoning` must cite specific numeric values or headlines, and must be different per symbol.

## 6. Append the predictions

Write the array to `scratchpad/pending_predictions.json` (create the folder if needed), then run:

```
python append_prediction.py scratchpad/pending_predictions.json
```

The append is all-or-nothing. If it reports missing or invalid fields, fix and retry. Do not proceed until it prints `appended N prediction(s)`. If it says something is already logged, STOP and report that — do not overwrite.

## 7. Summarize for the user

Print one line per symbol, then one line on the backdrop:

```
<SYMBOL>: <direction> @ <confidence>%
...
Backdrop: risk_on_score <n>/<of> — <one-sentence gist>
```

Nothing else. No apologies, no meta-commentary, no offers to trade.
