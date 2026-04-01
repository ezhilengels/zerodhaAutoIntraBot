"""
broker/kite_broker.py
──────────────────────
All Zerodha Kite Connect interactions.

Public interface:
  create_kite_session() -> KiteConnect
  place_orders(kite, signal) -> tuple[str|None, str|None]

No strategy logic here — this module purely executes instructions.
"""

import os
import json
import time
import uuid
import math
import pandas as pd
from datetime import date
from typing import Optional, Tuple
from kiteconnect import KiteConnect

from core.signal     import Signal
from config.settings import kite_cfg, paths_cfg, execution_cfg
from utils.logger    import get_logger
from utils.time_helpers import now_str

log = get_logger(__name__)

# File where today's access token is cached
_TOKEN_CACHE = ".kite_access_token.json"
_TICK_SIZE = 0.05


# ─────────────────────────────────────────────────────────────────────────────
#  Authentication
# ─────────────────────────────────────────────────────────────────────────────

def _save_token(access_token: str) -> None:
    """Save access token with today's date so it can be reused on restarts."""
    with open(_TOKEN_CACHE, "w") as f:
        json.dump({"date": str(date.today()), "access_token": access_token}, f)


def _load_token() -> Optional[str]:
    """Return today's cached access token, or None if not found / expired."""
    try:
        with open(_TOKEN_CACHE) as f:
            data = json.load(f)
        if data.get("date") == str(date.today()):
            return data.get("access_token")
    except Exception:
        pass
    return None


def _round_to_tick(price: float) -> float:
    """Round a price to the nearest valid exchange tick."""
    ticks = round(price / _TICK_SIZE)
    return round(ticks * _TICK_SIZE, 2)


def _ceil_to_tick(price: float) -> float:
    """Round a price up to the next valid exchange tick."""
    ticks = math.ceil(price / _TICK_SIZE)
    return round(ticks * _TICK_SIZE, 2)


def _order_product(kite: KiteConnect) -> str:
    """Resolve the configured Kite product constant."""
    return kite.PRODUCT_CNC if execution_cfg.order_product == "CNC" else kite.PRODUCT_MIS


def _fetch_order_details(kite: KiteConnect, order_id: str) -> Optional[dict]:
    """Return the latest order row for an order id."""
    try:
        history = kite.order_history(order_id)
        if history:
            return history[-1]
    except Exception as exc:
        log.warning(f"⚠️ Could not fetch order history for {order_id}: {exc}")
    return None


def _wait_for_fill(kite: KiteConnect, order_id: str, timeout_secs: int = 12) -> tuple[int, Optional[dict]]:
    """
    Wait briefly for an order to fill.
    Returns (filled_quantity, latest_order_details).
    """
    deadline = time.time() + timeout_secs
    latest: Optional[dict] = None

    while time.time() < deadline:
        latest = _fetch_order_details(kite, order_id)
        if latest:
            filled_qty = int(latest.get("filled_quantity") or 0)
            status = latest.get("status")
            if filled_qty > 0:
                return filled_qty, latest
            if status in {"CANCELLED", "REJECTED"}:
                return 0, latest
        time.sleep(1)

    latest = latest or _fetch_order_details(kite, order_id)
    return int((latest or {}).get("filled_quantity") or 0), latest


def create_kite_session() -> Optional[KiteConnect]:
    """
    Authenticate and return a ready KiteConnect instance.

    - If a valid access token for today is cached, reuse it (no request_token needed).
    - Otherwise, generate a new session from the request_token and cache the result.

    This means you only need a new request_token once per morning.
    Restarts during the day reuse the cached access token automatically.
    """
    if execution_cfg.paper_trading:
        log.info("🧪 Paper trading mode enabled — skipping Kite authentication")
        return None

    k = KiteConnect(api_key=kite_cfg.api_key)

    # Try cached token first
    cached = _load_token()
    if cached:
        try:
            k.set_access_token(cached)
            k.profile()   # Quick API call to verify token is still valid
            log.info("✅ Kite login successful (reused today's access token)")
            return k
        except Exception:
            log.warning("⚠️  Cached token invalid — generating new session…")

    # Fall back to request_token login
    try:
        data = k.generate_session(kite_cfg.request_token, api_secret=kite_cfg.api_secret)
        k.set_access_token(data["access_token"])
        _save_token(data["access_token"])
        log.info("✅ Kite login successful (new session, token cached for today)")
        return k
    except Exception as exc:
        log.error(f"❌ Kite login failed: {exc}")
        raise


# ─────────────────────────────────────────────────────────────────────────────
#  Order Placement
# ─────────────────────────────────────────────────────────────────────────────

def place_orders(kite: Optional[KiteConnect], signal: Signal) -> Tuple[Optional[str], Optional[str]]:
    """
    Place a MIS MARKET BUY + SL-M SELL for the given signal.
    Returns (buy_order_id, sl_order_id) or (None, None) on failure.
    """
    symbol = signal.symbol
    qty    = signal.quantity
    entry_price = _round_to_tick(signal.entry)
    sl_trigger  = _round_to_tick(signal.stop_loss)
    sl_price    = _round_to_tick(max(sl_trigger - _TICK_SIZE, _TICK_SIZE))

    buy_id: Optional[str] = None
    sl_id: Optional[str] = None

    if execution_cfg.paper_trading:
        buy_id = f"PAPER-BUY-{uuid.uuid4().hex[:8].upper()}"
        sl_id = f"PAPER-SL-{uuid.uuid4().hex[:8].upper()}"
        log.info(f"🧪 PAPER BUY simulated | {symbol} ×{qty} | ID: {buy_id}")
        log.info(f"🧪 PAPER SL simulated  | {symbol} trigger=₹{sl_trigger} limit=₹{sl_price} | ID: {sl_id}")
        _append_trade_log(signal, buy_id, sl_id)
        return buy_id, sl_id

    try:
        buy_id = kite.place_order(
            variety          = kite.VARIETY_REGULAR,
            exchange         = kite.EXCHANGE_NSE,
            tradingsymbol    = symbol,
            transaction_type = kite.TRANSACTION_TYPE_BUY,
            quantity         = qty,
            order_type       = kite.ORDER_TYPE_LIMIT,
            price            = entry_price,
            product          = _order_product(kite),
        )
        log.info(f"🛒 BUY placed  | {symbol} ×{qty} @ ₹{entry_price} | ID: {buy_id}")

        filled_qty, buy_details = _wait_for_fill(kite, buy_id)
        if filled_qty <= 0:
            status = (buy_details or {}).get("status", "UNKNOWN")
            log.warning(f"⚠️ BUY not filled for {symbol} yet (status={status}); skipping SL placement for now.")
            return buy_id, None

        sl_id = kite.place_order(
            variety          = kite.VARIETY_REGULAR,
            exchange         = kite.EXCHANGE_NSE,
            tradingsymbol    = symbol,
            transaction_type = kite.TRANSACTION_TYPE_SELL,
            quantity         = filled_qty,
            order_type       = kite.ORDER_TYPE_SL,
            trigger_price    = sl_trigger,
            price            = sl_price,
            product          = _order_product(kite),
        )
        log.info(
            f"🛡  SL placed   | {symbol} ×{filled_qty} trigger=₹{sl_trigger} limit=₹{sl_price} | ID: {sl_id}"
        )

        _append_trade_log(signal, buy_id, sl_id)
        return buy_id, sl_id

    except Exception as exc:
        log.error(f"❌ Order failed for {symbol}: {exc}")
        if buy_id and not sl_id:
            try:
                filled_qty, _buy_details = _wait_for_fill(kite, buy_id, timeout_secs=1)
                if filled_qty <= 0:
                    return None, None
                exit_id = kite.place_order(
                    variety          = kite.VARIETY_REGULAR,
                    exchange         = kite.EXCHANGE_NSE,
                    tradingsymbol    = symbol,
                    transaction_type = kite.TRANSACTION_TYPE_SELL,
                    quantity         = filled_qty,
                    order_type       = kite.ORDER_TYPE_LIMIT,
                    price            = _round_to_tick(max(signal.stop_loss, signal.entry * 0.99)),
                    product          = _order_product(kite),
                )
                log.warning(
                    f"⚠️ SL placement failed after BUY fill for {symbol}; "
                    f"sent emergency limit exit | Exit ID: {exit_id}"
                )
            except Exception as exit_exc:
                log.critical(
                    f"🚨 BUY succeeded but SL and emergency exit both failed for {symbol}: {exit_exc}"
                )
        return None, None


def square_off_live_mis_positions(kite: Optional[KiteConnect], state) -> list[str]:
    """
    Close bot-tracked MIS long positions before broker auto square-off.
    Returns the list of symbols successfully sent for exit.
    """
    if execution_cfg.paper_trading or execution_cfg.order_product != "MIS" or not kite:
        return []

    exited: list[str] = []

    try:
        live_symbols = set(state.live_signals.keys())
        if not live_symbols:
            return []

        orders = kite.orders()
        for order in orders:
            if (
                order.get("tradingsymbol") in live_symbols and
                order.get("product") == kite.PRODUCT_MIS and
                order.get("transaction_type") == kite.TRANSACTION_TYPE_SELL and
                order.get("status") in {"OPEN", "TRIGGER PENDING", "AMO REQ RECEIVED", "MODIFY VALIDATION PENDING"}
            ):
                try:
                    kite.cancel_order(variety=order["variety"], order_id=order["order_id"])
                    log.info(f"🧹 Cancelled pending MIS sell order for {order['tradingsymbol']} | ID: {order['order_id']}")
                except Exception as exc:
                    log.warning(f"⚠️ Could not cancel pending order {order.get('order_id')} for {order.get('tradingsymbol')}: {exc}")

        positions = kite.positions().get("day", [])
        for position in positions:
            symbol = position.get("tradingsymbol")
            qty = int(position.get("quantity", 0))
            if symbol not in live_symbols or qty <= 0 or position.get("product") != kite.PRODUCT_MIS:
                continue

            ltp = float(position.get("last_price") or position.get("average_price") or state.live_signals[symbol].entry)
            exit_price = _round_to_tick(max(ltp * 0.995, _TICK_SIZE))
            exit_id = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=kite.EXCHANGE_NSE,
                tradingsymbol=symbol,
                transaction_type=kite.TRANSACTION_TYPE_SELL,
                quantity=qty,
                order_type=kite.ORDER_TYPE_LIMIT,
                price=exit_price,
                product=kite.PRODUCT_MIS,
            )
            log.warning(f"⏰ MIS square-off sent for {symbol} ×{qty} @ ₹{exit_price} | Exit ID: {exit_id}")
            state.close_live_trade(symbol)
            exited.append(symbol)
    except Exception as exc:
        log.error(f"❌ MIS square-off check failed: {exc}")

    return exited


# ─────────────────────────────────────────────────────────────────────────────
#  Trade Log
# ─────────────────────────────────────────────────────────────────────────────

def _append_trade_log(signal: Signal, buy_id: str, sl_id: str) -> None:
    """Append an executed trade row to the CSV trade log."""
    csv_path = paths_cfg.trade_log_csv
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    row = pd.DataFrame([{
        "datetime":  now_str(),
        **signal.to_dict(),
        "buy_order_id": buy_id,
        "sl_order_id":  sl_id,
    }])
    file_exists = os.path.exists(csv_path)
    row.to_csv(csv_path, mode="a", header=not file_exists, index=False)
    log.info(f"📝 Trade logged → {csv_path}")
