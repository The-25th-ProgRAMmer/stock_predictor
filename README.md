# Trading Agent — Predictor Phase (v0.2)

A daily multi-symbol direction predictor. **No trades yet.** The goal of this phase is
to find out whether an LLM-driven predictor built on price data, technical indicators,
cross-asset context, and news can beat two dumb baselines. If it can't, don't bother
wiring it to real trades.

## What it does

Once per trading day (at or after market close):

1. Grades yesterday's predictions against today's actual closes, per symbol.
2. Fetches the whole universe from Alpaca in **one** API call, computes indicators, and
   derives cross-asset risk-on/risk-off features.
3. Asks Claude to predict tomorrow's direction (up/down) with a confidence 50–100% for
   each of the 10 target symbols.
4. Appends them to `logs/predictions.jsonl`.

You review weekly.

## The universe, and why it's split

Defined in `universe.py`. Two roles, and the distinction is the whole design:

**Targets (predicted and graded)** — `SPY QQQ AAPL MSFT GOOGL AMZN NVDA META TSLA SMH`

SPY alone is the most arbitraged instrument on earth. Predicting ten symbols is not
about volume; it's about **resolution** — finding out *where* skill lives. It's quite
possible the agent has no edge on SPY but some on single names driven by news flow.
Individual semis (AMD, AVGO, TSM, MU, INTC) are deliberately *not* targets: SMH already
captures the sector call, and five more correlated targets add noise, not signal.

**Context (never predicted)** — volatility `VIXY VXX`, metals `GLD SLV`, bonds
`TLT IEF`, credit `HYG LQD`, dollar `UUP`, energy `USO`, breadth `IWM`, plus the
individual semis.

You don't care whether the agent can call GLD tomorrow. You care that gold ripping while
credit sags and VIXY pops is a risk-off tell that should move the equity calls.

**VIX itself is not available.** It's an index, not a tradeable symbol, and the Alpaca
stocks API won't serve it. VIXY/VXX are the proxies — and because both bleed value to
contango, their *level* is meaningless over months. The context reports daily change and
60-day percentile instead, plus SPY's realized volatility as an honest substitute.

## Derived cross-asset features

Raw levels for 25 symbols would swamp the prompt and say little. Ratios and breadth say
a lot, so `fetch_data.py` computes:

- `risk_on_score` — how many of five independent cross-asset reads point risk-on (0–5)
- `HYG/LQD` — credit risk appetite; credit usually leads equities
- `IWM/SPY` — breadth; falling while SPY rises means a narrow, fragile tape
- `QQQ/SPY` — tech leadership vs. crowding
- `GLD/SLV` — defensiveness within metals
- `mag7_breadth` / `semi_breadth` — up-today and above-SMA20 counts
- 20-day correlations of SPY to TLT, GLD, HYG, UUP — regime detection
- VIXY percentile + SPY realized vol

## Baselines the agent must beat

- **always up**: predict "up" every day. SPY closes up ~54% of trading days. This
  baseline is *harder* on the megacaps, which have trended up more strongly.
- **prev day direction**: predict the same direction as yesterday's close.

## Reading the numbers without fooling yourself

Ten predictions a day is **not** ten independent tests. On an up day most of the
universe closes up together. Two guards are built into the reporting:

- **The bar is 30+ graded predictions _per symbol_**, never pooled. The per-symbol table
  is the number that counts; the pooled line always prints the distinct-day count beside
  it as the conservative sample size.
- **Same-direction rate.** If the agent calls every symbol the same way most days, it's
  making one market call stamped ten times, and the extra targets are buying volume
  rather than information. `review.py` and `weekly_report.py` both measure this, along
  with the agent's overall up-rate — because an agent that just says "up" everywhere
  will look great in aggregate while predicting nothing.

Expanding the universe does not create edge. It creates measurement resolution.

## Bar for graduating to paper trading

- ≥ 30 graded predictions **for the symbol in question**.
- Accuracy > both baselines by a margin that isn't noise.
- Confidence roughly calibrated (70%-confidence predictions come true ~70% of the time).
- Same-direction rate low enough that per-symbol calls are real calls.

Until all four hold, don't touch trade execution.

## Files

```
universe.py             symbol universe, split into targets and context
fetch_data.py           one-call multi-symbol fetch + TA + cross-asset features (no LLM)
predict.py              reads data/market_context.json, guards staleness, prints it
grade.py                grades un-graded predictions per symbol against actual closes
append_prediction.py    validates and appends a batch; all-or-nothing, deduped on
                        (symbol, for_date)
stats.py                shared scoring: per-symbol, calibration, herding, warnings
review.py               prints per-symbol accuracy, calibration, caveats
weekly_report.py        Friday write-up to logs/week_YYYY-MM-DD.txt
.claude/commands/
  predict.md            slash command: /predict — grade + predict all targets + log
  review.md             slash command: /review  — weekly human review
.github/workflows/
  fetch-market-data.yml daily 11:45 UTC fetch, commits data/
data/market_context.json  tiered context the routine reads (~6k tokens)
data/bars.json            full OHLC history per symbol, for grading only
logs/predictions.jsonl    one JSON object per line
.env                      Alpaca credentials (gitignored)
```

## Architecture

The fetch and the prediction are deliberately separate processes:

```
GitHub Actions (11:45 UTC)      cloud routine (12:00 UTC)
  fetch_data.py                   grade.py  -> reads data/bars.json
  -> Alpaca, one call             predict.py -> reads data/market_context.json
  -> commits data/                Claude analyzes, writes predictions
                                  append_prediction.py -> logs/predictions.jsonl
                                  git push
```

The routine makes **no network calls to market data APIs** — the sandbox blocks
`data.alpaca.markets` anyway. It only reads what the workflow committed. `predict.py`
exits non-zero on missing or stale (>4 days) data rather than letting the agent invent
numbers.

## Running

```powershell
# One-time
pip install -r requirements.txt

# In Claude Code (from this project directory):
/predict     # run at market close each trading day
/review      # run weekly to eyeball the numbers
```

## Notes

- **Alpaca free tier uses the IEX feed.** Daily bars for all 26 symbols are reliable, but
  `as_of` may lag by one session. Predictions always target the *next* trading day.
- **`truststore` is required on Windows.** Certain AV/proxy setups intercept TLS to
  Alpaca; `truststore` uses the Windows cert store instead of certifi.
- **The LLM is not allowed to modify the strategy inline.** If /review shows something is
  off, you decide whether to change `.claude/commands/predict.md`. The agent proposes,
  you dispose.
