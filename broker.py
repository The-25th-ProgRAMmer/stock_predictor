"""
Alpaca paper-trading client.

Hard safety rules, enforced here rather than left to the caller:

1. The base URL is a constant pointing at the PAPER endpoint. It is never read
   from config, never overridden by an argument, and never built from a string
   that reaches this module from outside.
2. Before any order is sent, the account is checked: it must be ACTIVE, and its
   account number must start with "PA" (Alpaca's paper prefix). A live account
   would fail this check even if credentials somehow pointed at one.
3. Gross exposure is capped. An order batch that would exceed MAX_GROSS_NOTIONAL
   is refused in full rather than partially filled.
4. KILL_SWITCH: set TRADING_DISABLED=1 in the environment and no order is sent.
"""

import os
import time

import requests

# Some Windows AV/proxy setups intercept TLS to Alpaca with their own root, which
# certifi's bundle then rejects. truststore uses the OS trust store instead. Both
# of these are absent and unnecessary on the Actions runners, where credentials
# arrive as secrets and the system roots already work.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

PAPER_BASE = "https://paper-api.alpaca.markets"

MAX_GROSS_NOTIONAL = 150_000.0   # refuse any batch larger than this, in total
MAX_SYMBOL_NOTIONAL = 30_000.0   # refuse any single position larger than this
MAX_POSITIONS = 12               # the universe is 10; a 12 cap catches runaways


class BrokerError(RuntimeError):
    pass


class SafetyError(BrokerError):
    """Raised when a safety rule would be violated. Never retried."""


def _headers() -> dict:
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise BrokerError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _request(method: str, path: str, **kw) -> dict | list:
    url = PAPER_BASE + path
    last = None
    for attempt in range(3):
        try:
            r = requests.request(method, url, headers=_headers(), timeout=30, **kw)
        except requests.RequestException as e:
            last = e
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            last = BrokerError(f"{r.status_code} {r.text[:200]}")
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code >= 400:
            raise BrokerError(f"{method} {path} -> {r.status_code} {r.text[:300]}")
        return r.json() if r.text else {}
    raise BrokerError(f"{method} {path} failed after retries: {last}")


def assert_paper_account() -> dict:
    """Verify we are pointed at a healthy paper account. Call before trading."""
    if os.environ.get("TRADING_DISABLED") == "1":
        raise SafetyError("TRADING_DISABLED=1 is set - refusing to trade")

    acct = _request("GET", "/v2/account")
    number = str(acct.get("account_number", ""))
    if not number.startswith("PA"):
        raise SafetyError(
            f"account {number!r} is not a paper account (expected a 'PA' prefix)"
        )
    if acct.get("status") != "ACTIVE":
        raise SafetyError(f"account status is {acct.get('status')!r}, not ACTIVE")
    if acct.get("trading_blocked") or acct.get("account_blocked"):
        raise SafetyError("account is blocked for trading")
    return acct


def clock() -> dict:
    return _request("GET", "/v2/clock")


def positions() -> list[dict]:
    return _request("GET", "/v2/positions")


def open_orders() -> list[dict]:
    return _request("GET", "/v2/orders", params={"status": "open", "limit": 500})


def orders_since(after_iso: str) -> list[dict]:
    return _request(
        "GET", "/v2/orders",
        params={"status": "all", "after": after_iso, "limit": 500, "direction": "asc"},
    )


def get_order(order_id: str, nested: bool = False) -> dict:
    """nested=True also returns an order's child legs, which is how the two
    halves of a one-cancels-other pair are read back."""
    return _request(
        "GET", f"/v2/orders/{order_id}",
        params={"nested": "true"} if nested else None,
    )


def calendar(start: str, end: str) -> list[dict]:
    """Trading sessions in [start, end] as YYYY-MM-DD. The authority on how many
    sessions a position has been held, which a local bar file can understate if
    the daily fetch has been failing."""
    return _request("GET", "/v2/calendar", params={"start": start, "end": end})


def cancel_order(order_id: str) -> None:
    _request("DELETE", f"/v2/orders/{order_id}")


def check_batch(orders: list[dict], existing: list[dict] | None = None) -> None:
    """
    Validate a whole batch before a single order is sent.

    `existing` is the currently open positions. Positions are held across
    sessions rather than flattened daily, so the caps have to be portfolio-wide:
    a batch that looks small on its own can still push total exposure past the
    limit when added to what is already on.
    """
    existing = existing or []
    held_gross = sum(abs(float(p.get("market_value", 0) or 0)) for p in existing)

    total = len(existing) + len(orders)
    if total > MAX_POSITIONS:
        raise SafetyError(
            f"{len(existing)} held + {len(orders)} new = {total} positions "
            f"exceeds MAX_POSITIONS={MAX_POSITIONS}"
        )

    gross = held_gross
    for o in orders:
        notional = abs(float(o.get("notional_estimate", 0)))
        if notional > MAX_SYMBOL_NOTIONAL:
            raise SafetyError(
                f"{o['symbol']} notional ${notional:,.0f} exceeds "
                f"MAX_SYMBOL_NOTIONAL=${MAX_SYMBOL_NOTIONAL:,.0f}"
            )
        gross += notional
    if gross > MAX_GROSS_NOTIONAL:
        raise SafetyError(
            f"total gross ${gross:,.0f} (${held_gross:,.0f} already held + "
            f"${gross - held_gross:,.0f} new) exceeds "
            f"MAX_GROSS_NOTIONAL=${MAX_GROSS_NOTIONAL:,.0f} - nothing sent"
        )


def submit(symbol: str, qty: int, side: str, tif: str, client_order_id: str) -> dict:
    """
    Submit one whole-share market order.

    tif is "opg" (opening auction) or "cls" (closing auction). Alpaca requires
    whole shares for both; fractional orders are long-only and day-TIF only,
    which is why sizing rounds down to integers.
    """
    if qty < 1:
        raise SafetyError(f"{symbol}: refusing to submit qty={qty}")
    if side not in ("buy", "sell"):
        raise SafetyError(f"{symbol}: bad side {side!r}")
    if tif not in ("opg", "cls", "day"):
        raise SafetyError(f"{symbol}: bad time_in_force {tif!r}")

    body = {
        "symbol": symbol,
        "qty": str(int(qty)),
        "side": side,
        "type": "market",
        "time_in_force": tif,
        "client_order_id": client_order_id,
    }
    return _request("POST", "/v2/orders", json=body)


def submit_oco(
    symbol: str,
    qty: int,
    side: str,
    take_profit: float,
    stop_loss: float,
    client_order_id: str,
) -> dict:
    """
    Rest a one-cancels-other exit pair on an existing position, good till
    cancelled: a take-profit limit and a stop. Whichever trades first cancels
    the other, so the position can never be closed twice and no process here has
    to be watching the market.

    `side` is the CLOSING side - "sell" for a long position, "buy" for a short.

    Alpaca rejects the pair outright if either leg already sits on the wrong side
    of the market, so callers must deal with a position that has run past one of
    its own levels before this is called (see exits.already_past).
    """
    if qty < 1:
        raise SafetyError(f"{symbol}: refusing to submit qty={qty}")
    if side not in ("buy", "sell"):
        raise SafetyError(f"{symbol}: bad side {side!r}")
    if take_profit <= 0 or stop_loss <= 0:
        raise SafetyError(f"{symbol}: bad bracket tp={take_profit} sl={stop_loss}")
    if side == "sell" and not take_profit > stop_loss:
        raise SafetyError(
            f"{symbol}: long exit needs take_profit above stop_loss, "
            f"got tp={take_profit} sl={stop_loss}"
        )
    if side == "buy" and not take_profit < stop_loss:
        raise SafetyError(
            f"{symbol}: short exit needs take_profit below stop_loss, "
            f"got tp={take_profit} sl={stop_loss}"
        )

    body = {
        "symbol": symbol,
        "qty": str(int(qty)),
        "side": side,
        "type": "limit",
        "time_in_force": "gtc",
        "order_class": "oco",
        "take_profit": {"limit_price": f"{take_profit:.2f}"},
        "stop_loss": {"stop_price": f"{stop_loss:.2f}"},
        "client_order_id": client_order_id,
    }
    return _request("POST", "/v2/orders", json=body)
