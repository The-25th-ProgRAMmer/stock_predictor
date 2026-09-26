---
description: Grade any prior predictions, then predict tomorrow's SPY direction and log it.
---

You are the trading agent's predictor. This command runs once per trading day (typically at or after market close). Follow these steps in order — do not skip, do not add extra steps.

## 1. Grade prior predictions

Run: `python grade.py`

Report the output verbatim in one line if anything was graded.

## 2. Gather today's context

Run: `python predict.py`

This prints a single JSON object on stdout with SPY's OHLCV, technical indicators, and news. Parse it. Do NOT invent additional data.

## 3. Analyze

Consider only what is in the context object:

- **Trend**: sign of `close_vs_sma20_pct` and `close_vs_sma50_pct`; `sma20_vs_sma50`.
- **Momentum**: `rsi14` (overbought >70, oversold <30), `macd_hist` (positive = bullish momentum, negative = bearish).
- **Volatility**: `atr14_pct_of_close` — is the market unusually volatile?
- **Recent action**: `prev_day_direction`, `prev_day_pct`, the last 5 daily bars.
- **News**: read the headlines and summaries. Are they net positive, negative, or mixed for the broad US market / SPY? Ignore items clearly unrelated to equities.

Be honest. If signals conflict, that's a low-confidence read (50–60). If they align strongly, that's higher confidence (70–85). Reserve 85+ for rare cases where multiple independent signals point the same way.

## 4. Compose the prediction JSON

Build this exact structure (fill in the values):

```json
{
  "made_at": "<ISO 8601 UTC timestamp, e.g. 2026-09-26T21:05:00Z>",
  "for_date": "<value of context.for_date>",
  "symbol": "SPY",
  "prediction": {
    "direction": "up" | "down",
    "confidence": <integer 50..100>,
    "reasoning": "<2-3 sentences citing specific signals from the context by name (e.g. 'RSI at 68 near overbought, MACD hist positive, news mildly bullish on Fed remarks')>",
    "key_signals": ["<short signal 1>", "<short signal 2>", "..."],
    "sources": ["<news url 1>", "<news url 2>"]
  },
  "baselines": {
    "always_up": "up",
    "prev_day_direction": "<value of context.prev_day_direction>"
  },
  "context_snapshot": {
    "latest_close": <context.latest_close>,
    "sma20": <context.indicators.sma20>,
    "sma50": <context.indicators.sma50>,
    "rsi14": <context.indicators.rsi14>,
    "macd_hist": <context.indicators.macd_hist>
  },
  "outcome": null
}
```

Rules:
- `outcome` MUST be `null`. Grading happens tomorrow.
- Do not add fields the schema does not list.
- `reasoning` must reference specific numeric values or specific headlines from the context.

## 5. Append the prediction

Write the JSON object to `scratchpad/pending_prediction.json` (create the folder if it doesn't exist), then run:

```
python append_prediction.py scratchpad/pending_prediction.json
```

If it prints an error about missing/invalid fields, fix and retry — do not proceed until it prints "appended prediction for ...".

## 6. Summarize for the user

Print a two-line summary:

```
Prediction for <for_date>: <direction> @ <confidence>%
Top reason: <one-sentence gist of reasoning>
```

Nothing else. No apologies, no meta-commentary, no offers to trade.
