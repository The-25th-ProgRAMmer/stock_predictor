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

## 3. The call is already made — reproduce it

Each symbol's `target_data.<SYMBOL>.signal` holds the day's call, computed in
Python by `signals.py`: `direction`, `confidence`, the `basis` it rests on, the
supporting `evidence` (SMAs, SMA50 slope, RSI, relative volume, up/down volume,
OBV, MACD, SPY regime) and `notes`.

**Do not re-weigh the indicators.** That is exactly what used to flip NVDA from
"up 56%" to "down 56%" on identical data. Three years of backtesting showed none
of these indicators beats "always up" on next-day direction, and that agreement
between them adds nothing, so there is no better answer for you to reason your
way to — only a less reproducible one. In particular:

- Overbought RSI is **not** a reason to call down. Inside an uptrend the next day
  was up 50.3% of the time.
- Heavy volume is **not** confirmation. It preceded reversals slightly more often.
- The `cross_asset` backdrop is context for your reasoning text. It does not
  change the call.

## 4. The one judgement that is yours: material news

`target_data.<SYMBOL>.material_news` lists headlines that `signals.py` flagged, by
fixed rules, as a material event for that symbol alone: earnings, guidance, an
upgrade or downgrade, M&A, regulatory or legal action, or a corporate event
(CEO change, buyback, layoffs, recall). Opinion pieces, previews and round-ups
are filtered out before you see them.

For each symbol:

- **No flagged headlines** → `method: "signal"`. Copy `direction` and `confidence`
  exactly.
- **Flagged headlines that agree with the call, or are ambiguous** → still
  `method: "signal"`. Mention them in the reasoning.
- **A flagged headline that clearly cuts AGAINST the call** → you may override:
  `method: "news_override"`, the opposite `direction`, `confidence` 52, and
  `override: {"headline": <copied verbatim from material_news>, "event": <its event>}`.
  Override only when the event plainly reverses the case — a guidance cut in an
  uptrend, a major approval in a downtrend. When unsure, do not override.

`append_prediction.py` enforces all of this. A `signal` prediction that doesn't
match the computed call, or an override that doesn't cite a flagged headline
word for word, is rejected and nothing is written.

## 5. Compose the predictions JSON

Build a JSON **array**, one object per target symbol:

```json
[
  {
    "made_at": "<ISO 8601 UTC timestamp, e.g. 2026-10-02T21:05:00Z>",
    "for_date": "<value of context.for_date>",
    "symbol": "<target symbol>",
    "prediction": {
      "direction": "<signal.direction, or its opposite on an override>",
      "confidence": <signal.confidence, or 52 on an override>,
      "method": "signal" | "news_override",
      "override": {"headline": "<verbatim>", "event": "<event>"},
      "reasoning": "<2-3 sentences: the signal basis with its numbers, then any news and why it did or did not override>",
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
- Include `override` only when `method` is `"news_override"`.
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
