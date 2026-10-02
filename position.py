"""
Turn predictions + trend scores into target positions.

The rule, in order:

1. The trend score sets the side and the size rung (full / half / none).
2. The LLM prediction must CONFIRM that side, or no position is taken.
3. A neutral score (0) is no position regardless of what the prediction says.

So every position taken has two independent reasons behind it. Trades are
tagged with `agreed`, and skips carry a `reason`, so the review can separate
"both sources agreed" from "the score alone would have traded" and find out
whether the LLM is adding anything over the mechanical score.

Sizing is whole shares only: Alpaca does not support fractional short selling,
and opening-auction / market-on-close orders are whole-share regardless. The
notional is therefore rounded DOWN to a whole share count, and a position that
rounds to zero shares is reported as a skip rather than silently dropped.
"""

from dataclasses import dataclass, asdict

DIRECTION_TO_SIDE = {"up": "long", "down": "short"}


@dataclass
class Target:
    symbol: str
    side: str | None          # "long", "short", or None for no trade
    qty: int                  # whole shares, 0 when not trading
    notional_target: float    # what we wanted to deploy, before rounding
    notional_actual: float    # qty * reference_price
    reference_price: float
    trend_score: int | None
    size_fraction: float
    predicted: str | None     # "up"/"down" from the LLM
    confidence: int | None
    agreed: bool
    reason: str               # why we are trading, or why not

    def as_dict(self) -> dict:
        return asdict(self)


def resolve(
    symbol: str,
    prediction: dict | None,
    trend: dict,
    base_notional: float,
) -> Target:
    """
    Combine one symbol's prediction and trend score into a target position.
    `prediction` is a record from predictions.jsonl (or None if absent).
    `trend` is the output of trend_score.score_symbol.
    """
    score = trend.get("score")
    price = trend.get("reference_price") or 0.0
    fraction = trend.get("size_fraction", 0.0)
    trend_side = trend.get("side")

    pred_dir = (prediction or {}).get("prediction", {}).get("direction")
    conf = (prediction or {}).get("prediction", {}).get("confidence")
    pred_side = DIRECTION_TO_SIDE.get(pred_dir) if pred_dir else None

    def skip(reason: str, agreed: bool = False) -> Target:
        return Target(
            symbol=symbol, side=None, qty=0, notional_target=0.0,
            notional_actual=0.0, reference_price=price, trend_score=score,
            size_fraction=fraction, predicted=pred_dir, confidence=conf,
            agreed=agreed, reason=reason,
        )

    if score is None:
        return skip(f"no trend score: {trend.get('error', 'unknown')}")
    if prediction is None:
        return skip("no prediction logged for this symbol and date")
    if pred_side is None:
        return skip(f"unrecognised predicted direction {pred_dir!r}")
    if score == 0:
        return skip("trend score 0 (neutral) - no position by rule")
    if trend_side != pred_side:
        return skip(
            f"conflict: trend says {trend_side} (score {score:+d}), "
            f"prediction says {pred_side} @ {conf}%"
        )

    notional_target = base_notional * fraction
    if price <= 0:
        return skip("no usable reference price", agreed=True)
    qty = int(notional_target // price)
    if qty < 1:
        return skip(
            f"size rounds to 0 shares (wanted ${notional_target:,.0f} "
            f"at ${price:,.2f}); raise base_notional",
            agreed=True,
        )

    return Target(
        symbol=symbol, side=trend_side, qty=qty,
        notional_target=round(notional_target, 2),
        notional_actual=round(qty * price, 2),
        reference_price=price, trend_score=score, size_fraction=fraction,
        predicted=pred_dir, confidence=conf, agreed=True,
        reason=(
            f"trend {score:+d} ({trend['label']}) confirmed by "
            f"prediction {pred_dir} @ {conf}%"
        ),
    )


def resolve_all(
    symbols: list[str],
    predictions_by_symbol: dict[str, dict],
    trends: dict[str, dict],
    base_notionals: dict[str, float | None],
) -> list[Target]:
    """
    base_notionals is the FULL-SIZE notional per symbol, from
    sizing.size_universe — i.e. (target_risk / annual_vol) * portfolio. The
    score weight (1.0 / 0.5 / 0) is applied inside resolve().
    """
    out = []
    for s in symbols:
        base = base_notionals.get(s)
        if base is None:
            out.append(
                resolve(s, predictions_by_symbol.get(s), {"score": None,
                        "error": "no volatility estimate, cannot size"}, 0.0)
            )
            continue
        out.append(
            resolve(s, predictions_by_symbol.get(s), trends.get(s, {}), base)
        )
    return out


def summarise(targets: list[Target]) -> str:
    trading = [t for t in targets if t.qty > 0]
    gross = sum(t.notional_actual for t in trading)
    longs = sum(t.notional_actual for t in trading if t.side == "long")
    shorts = sum(t.notional_actual for t in trading if t.side == "short")
    lines = [
        f"{'symbol':<8}{'side':<7}{'qty':>5}{'px':>10}{'notional':>11}"
        f"{'score':>7}{'pred':>7}  note",
    ]
    for t in targets:
        if t.qty > 0:
            lines.append(
                f"{t.symbol:<8}{t.side:<7}{t.qty:>5}{t.reference_price:>10.2f}"
                f"{t.notional_actual:>11,.0f}{t.trend_score:>+7}"
                f"{(t.predicted or '-'):>7}  {t.size_fraction:.1f}x"
            )
        else:
            lines.append(
                f"{t.symbol:<8}{'-':<7}{0:>5}{t.reference_price:>10.2f}"
                f"{0:>11}{(t.trend_score if t.trend_score is not None else 0):>+7}"
                f"{(t.predicted or '-'):>7}  SKIP: {t.reason}"
            )
    lines.append("")
    lines.append(
        f"{len(trading)}/{len(targets)} positions - gross ${gross:,.0f} "
        f"(long ${longs:,.0f} / short ${shorts:,.0f}, net ${longs - shorts:,.0f})"
    )
    return "\n".join(lines)
