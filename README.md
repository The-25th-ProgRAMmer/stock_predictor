# Trading Agent — Predictor Phase (v0.1)

A daily SPY direction predictor. **No trades yet.** The goal of this phase is to
find out whether an LLM-driven predictor built on price data, technical
indicators, and news can beat two dumb baselines. If it can't, don't bother
wiring it to real trades.

## What it does

Once per trading day (at or after market close):

1. Grades yesterday's prediction against today's actual close.
2. Fetches SPY OHLCV, computes SMA20/SMA50/RSI14/MACD/ATR14, pulls news headlines from Alpaca.
3. Asks Claude to predict tomorrow's direction (up/down) with a confidence 50–100%.
4. Appends the prediction to `logs/predictions.jsonl`.

You review weekly.

## Files

```
predict.py              deterministic data + TA + news collector (no LLM)
grade.py                grades un-graded predictions against actual outcomes
append_prediction.py    validates and appends one prediction to the log
review.py               prints accuracy, calibration, recent 10
.claude/commands/
  predict.md            slash command: /predict — runs grade + predict + log
  review.md             slash command: /review  — weekly human review
logs/predictions.jsonl  one JSON object per line
.env                    Alpaca credentials (gitignored)
```

## Baselines the agent must beat

- **always up**: predict "up" every day. SPY closes up ~54% of trading days historically.
- **prev day direction**: predict the same direction as yesterday's close (naive momentum).

If the agent doesn't beat both over 50+ predictions, the LLM approach doesn't have edge for this task.

## Bar for graduating to paper trading

- ≥ 30 graded predictions.
- Accuracy > both baselines by a margin that isn't noise.
- Confidence is roughly calibrated (70%-confidence predictions come true ~70% of the time).

Until all three hold, don't touch trade execution.

## Running

```powershell
# One-time
pip install -r requirements.txt

# In Claude Code (from this project directory):
/predict     # run at market close each trading day
/review      # run weekly to eyeball the numbers
```

## Notes

- **Alpaca free tier uses IEX feed.** Daily bars for SPY are reliable, but the `as_of` date may lag by one session on the current trading day. The prediction always targets the *next* trading day.
- **`truststore` is required on Windows.** Certain AV/proxy setups intercept TLS to Alpaca; `truststore` uses the Windows cert store instead of certifi.
- **The LLM is not allowed to modify the strategy inline.** If /review shows something is off, you decide whether to change the prompt in `.claude/commands/predict.md`. The agent proposes, you dispose.
