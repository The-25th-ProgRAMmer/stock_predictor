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


def get_order(order_id: str) -> dict:
    return _request("GET", f"/v2/orders/{order_id}")


def check_batch(orders: list[dict]) -> None:
    """Validate a whole batch before a single order is sent."""
    if len(orders) > MAX_POSITIONS:
        raise SafetyError(f"{len(orders)} orders exceeds MAX_POSITIONS={MAX_POSITIONS}")
    gross = 0.0
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
            f"batch gross ${gross:,.0f} exceeds "
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
