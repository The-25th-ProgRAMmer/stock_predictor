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

## Paper trading (v0.3)

**This is a plumbing test, not a strategy go-live.** It runs on Alpaca paper
account `PA3F0MXFHL8Z` with $100k of fake money. The keys are paper-only — they
return 401 against the live endpoint, so they cannot place a real-money order.

The point is not profit. It is to debug order submission, fills, reconciliation
and slippage *before* there is any edge to risk, and to answer a question the
prediction log structurally cannot: **direction accuracy is not profitability.**
At 60% accuracy you still lose money if you are right on small-move days and
wrong on big ones. Only a P&L log shows that.

### How a position is decided

1. **Trend score** (`trend_score.py`) — compare the current price against its
   own price 5, 10, 21 and 42 sessions ago. Each comparison is ±1, so the score
   lands on exactly one of five rungs: +4 full long, +2 half long, 0 flat,
   −2 half short, −4 full short.
2. **The prediction must confirm it** (`position.py`). A conflict, or a score of
   0, means no position. Every trade therefore has two independent reasons.
3. **Volatility-targeted sizing** (`sizing.py`):

   ```
   position = score weight x (target risk % / annualised volatility %) x portfolio
   ```

   where annualised volatility is the average daily close-to-close % move over
   30 sessions, times 19.1 (√365). **Target risk is fixed at 2%.** A quiet name
   gets a large notional and a violent one a small one, so each position
   contributes comparable *risk* rather than comparable *money* — SPY at 9.4%
   vol gets $10,000 at half weight while META at 41% gets $2,193 at the same
   half weight.

Whole shares only: Alpaca has no fractional short selling, and opening/closing
auction orders are whole-share regardless. Sizes round down; a position that
rounds to zero shares is reported as a skip, never silently dropped.

### Timing, and a mismatch worth knowing

Entry is a market-on-open (`opg`) order, exit is market-on-close (`cls`), so
fills land at the official auction prices and the hold is exactly open-to-close.

**Predictions are graded close-to-close but traded open-to-close.** The
overnight gap cannot be captured on this schedule, so realised P&L will
systematically differ from graded accuracy — a correct prediction can lose
money and vice versa. `reconcile.py` records `gap_pct` per round trip so you can
see how much of each graded move happened before you could act.

### Safety

- `broker.py` hard-codes the paper URL; it is never read from config.
- Every order is preceded by a check that the account number starts with `PA`
  and is ACTIVE. A live account fails this even with valid credentials.
- Caps: $30k per symbol, $150k gross per batch, 12 positions. A batch over the
  gross cap is refused **in full**, not partially filled.
- Kill switch: set repository variable `TRADING_DISABLED=1` and nothing trades.
- Orders use deterministic `client_order_id`s (`entry-<date>-<symbol>`), so a
  duplicate run is rejected by Alpaca rather than doubling a position.
- Entry refuses to run at all if positions are already open from a prior session.

`logs/trades.jsonl` is kept **separate** from `logs/predictions.jsonl` so a
trading bug can never contaminate the accuracy experiment.

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
