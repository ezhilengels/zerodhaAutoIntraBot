"""
strategy/pullback.py
─────────────────────
Gap-Up Pullback to VWAP entry strategy.

Interface contract:
  detect(symbol: str, state: SessionState) -> Signal | None

This module only decides whether a signal exists — it never places orders,
sends messages, or mutates state.

To add a new strategy (e.g. ORB breakout), create strategy/orb.py with
the same detect() signature and wire it into main.py alongside this one.
"""

from typing import Optional
from core.signal         import Signal
from core.session        import SessionState
from strategy.indicators import completed_candles, position_size, rsi as calc_rsi, vwap as calc_vwap, avg_volume
from data                import nse_provider as nse
from config.settings     import strategy_cfg
from utils.logger        import get_logger

log = get_logger(__name__)


def detect(symbol: str, state: SessionState, quote: Optional[dict] = None) -> Optional[Signal]:
    """
    Run all pullback entry checks for `symbol`.
    Returns a Signal if every condition passes, otherwise None.
    """
    cfg = strategy_cfg

    # ── 1. Live quote ────────────────────────────────────────────────────
    quote = quote or nse.get_quote(symbol)
    if not quote or quote["ltp"] == 0:
        return None

    ltp        = quote["ltp"]
    open_price = quote["open"]
    prev_close = state.prev_close_map.get(symbol, quote["prev_close"])
    if prev_close <= 0 or open_price <= 0:
        return None

    # ── 2. Gap-up filter ─────────────────────────────────────────────────
    gap_pct = ((open_price - prev_close) / prev_close) * 100
    if gap_pct < cfg.gap_up_threshold:
        return None

    # ── 3. Intraday candles ───────────────────────────────────────────────
    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty or len(df) < 5:
        return None

    # ── 4. Price has pulled back near VWAP ───────────────────────────────
    vwap_val = calc_vwap(df)
    if vwap_val <= 0:
        return None

    dist_pct = ((ltp - vwap_val) / vwap_val) * 100
    if not (0 <= dist_pct <= cfg.pullback_vwap_range):
        return None

    # ── 5. Last candle is green (buyers stepping in) ──────────────────────
    last = df.iloc[-1]
    if last["close"] <= last["open"]:
        return None

    # ── 6. RSI in neutral pullback zone ──────────────────────────────────
    rsi_val = calc_rsi(df["close"])
    if not (cfg.rsi_min <= rsi_val <= cfg.rsi_max):
        return None

    # ── 7. Quiet volume on the dip ───────────────────────────────────────
    vol_avg   = avg_volume(df["volume"], exclude_last=1)
    vol_ratio = float(last["volume"]) / vol_avg if vol_avg > 0 else 1.0
    if vol_ratio > cfg.volume_ratio_max:
        return None

    # ── 8. Build the Signal ───────────────────────────────────────────────
    entry  = ltp
    sl     = round(entry * (1 - cfg.stop_loss_pct / 100), 2)
    risk   = entry - sl
    target = round(entry + risk * cfg.reward_ratio, 2)
    qty    = position_size(
        entry,
        sl,
        cfg.account_capital,
        cfg.risk_pct_per_trade,
        cfg.max_capital_per_trade,
        cfg.max_exposure_multiple,
    )

    log.info(
        f"✅ Signal | {symbol} | "
        f"entry=₹{entry}  sl=₹{sl}  target=₹{target}  "
        f"qty={qty}  gap={gap_pct:.1f}%  rsi={rsi_val:.1f}  vol={vol_ratio:.2f}x"
    )

    return Signal(
        symbol    = symbol,
        entry     = entry,
        stop_loss = sl,
        target    = target,
        quantity  = qty,
        capital   = round(entry * qty, 2),
        gap_pct   = round(gap_pct,   2),
        vwap      = round(vwap_val,  2),
        rsi       = round(rsi_val,   1),
        vol_ratio = round(vol_ratio, 2),
    )
