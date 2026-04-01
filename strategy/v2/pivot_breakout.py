"""
strategy/pivot_breakout.py
──────────────────────────
Pivot Point Breakout intraday strategy.

Interface contract:
  detect(symbol: str, state: SessionState) -> Signal | None

Logic:
  - Pre-calculate daily Classic Pivot, R1, R2, S1, S2 from PREVIOUS day OHLC
  - LONG  when price breaks above R1 with volume + confirmation candle
  - SHORT when price breaks below S1 with volume + confirmation candle
  - Skip if price is already far extended beyond the level
  - Each level can only be traded ONCE per day (no re-entry at same level)

Best stocks: BANKNIFTY, RELIANCE, HDFCBANK, SBIN, ICICIBANK
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from core.signal import Signal
from core.session import SessionState
from strategy.indicators import avg_volume, completed_candles, position_size
from data import nse_provider as nse
from config.settings import strategy_cfg
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════════

@dataclass
class PivotBreakoutConfig:
    # ── Time window ──
    start_time:               str   = "09:30"   # skip first 15 min noise
    end_time:                 str   = "14:30"   # no fresh entries after this

    # ── Breakout confirmation ──
    breakout_buffer_pct:      float = 0.05      # price must be this % ABOVE level to confirm
    max_chase_pct:            float = 0.40      # skip if price is >0.40% past the level (too late)

    # ── Volume ──
    volume_multiplier_min:    float = 1.5       # breakout candle must be 1.5x avg volume
    min_session_volume:       int   = 300_000   # skip illiquid stocks

    # ── Candle quality ──
    min_body_pct:             float = 0.50      # body / range >= 50% (no doji)

    # ── Trend filter ──
    trend_filter_enabled:     bool  = True
    ema_period:               int   = 20        # price must be above EMA20 for longs

    # ── Market filter ──
    market_filter_enabled:    bool  = True
    market_symbol:            str   = "NIFTY 50"
    enable_shorts:            bool  = False      # Current bot execution flow is long-only

    # ── Risk ──
    reward_ratio:             float = 2.0       # RR — override per symbol if needed
    max_sl_atr_multiple:      float = 1.5       # SL must be ≤ 1.5 ATR wide

    # ── Per-symbol overrides ──
    symbol_overrides: dict = field(default_factory=lambda: {
        "BANKNIFTY": {
            "breakout_buffer_pct":   0.03,
            "max_chase_pct":         0.30,
            "volume_multiplier_min": 1.3,
        },
        "RELIANCE": {
            "breakout_buffer_pct":   0.05,
            "max_chase_pct":         0.35,
        },
        "HDFCBANK": {
            "breakout_buffer_pct":   0.05,
            "max_chase_pct":         0.35,
            "volume_multiplier_min": 1.4,
        },
        "SBIN": {
            "volume_multiplier_min": 1.6,   # needs strong volume confirmation
        },
    })

    def for_symbol(self, symbol: str) -> "PivotBreakoutConfig":
        """Return config copy with symbol-specific overrides applied."""
        overrides = self.symbol_overrides.get(symbol, {})
        if not overrides:
            return self
        import copy
        cfg = copy.copy(self)
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg


pivot_breakout_cfg = PivotBreakoutConfig()


# ══════════════════════════════════════════════════════════════════
#  PIVOT POINT CALCULATION
# ══════════════════════════════════════════════════════════════════

@dataclass
class PivotLevels:
    """Classic pivot points calculated from previous day OHLC."""
    pivot: float
    r1:    float
    r2:    float
    r3:    float
    s1:    float
    s2:    float
    s3:    float
    prev_high:  float
    prev_low:   float
    prev_close: float

    def __str__(self) -> str:
        return (
            f"Pivot=₹{self.pivot:.2f} | "
            f"R1=₹{self.r1:.2f}  R2=₹{self.r2:.2f}  R3=₹{self.r3:.2f} | "
            f"S1=₹{self.s1:.2f}  S2=₹{self.s2:.2f}  S3=₹{self.s3:.2f}"
        )

    def nearest_resistance(self, price: float) -> tuple[str, float]:
        """Return (level_name, level_price) of nearest resistance above price."""
        levels = [("R1", self.r1), ("R2", self.r2), ("R3", self.r3)]
        above  = [(name, lvl) for name, lvl in levels if lvl > price]
        return min(above, key=lambda x: x[1]) if above else ("R3", self.r3)

    def nearest_support(self, price: float) -> tuple[str, float]:
        """Return (level_name, level_price) of nearest support below price."""
        levels = [("S1", self.s1), ("S2", self.s2), ("S3", self.s3)]
        below  = [(name, lvl) for name, lvl in levels if lvl < price]
        return max(below, key=lambda x: x[1]) if below else ("S1", self.s1)

    def all_levels(self) -> dict[str, float]:
        return {
            "S3": self.s3, "S2": self.s2, "S1": self.s1,
            "PIVOT": self.pivot,
            "R1": self.r1, "R2": self.r2, "R3": self.r3,
        }


def calculate_pivots(prev_high: float, prev_low: float, prev_close: float) -> PivotLevels:
    """
    Classic Pivot Point formula (most reliable for NSE intraday):

    Pivot = (H + L + C) / 3
    R1    = 2*P - L
    R2    = P + (H - L)
    R3    = H + 2*(P - L)
    S1    = 2*P - H
    S2    = P - (H - L)
    S3    = L - 2*(H - P)
    """
    p  = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * p - prev_low
    r2 = p + (prev_high - prev_low)
    r3 = prev_high + 2 * (p - prev_low)
    s1 = 2 * p - prev_high
    s2 = p - (prev_high - prev_low)
    s3 = prev_low - 2 * (prev_high - p)

    return PivotLevels(
        pivot=round(p, 2),
        r1=round(r1, 2), r2=round(r2, 2), r3=round(r3, 2),
        s1=round(s1, 2), s2=round(s2, 2), s3=round(s3, 2),
        prev_high=prev_high, prev_low=prev_low, prev_close=prev_close,
    )


def fetch_pivot_levels(symbol: str) -> Optional[PivotLevels]:
    """
    Fetch previous day OHLC and return PivotLevels.

    Uses nse_provider to get daily candles.
    Falls back gracefully if data is unavailable.
    """
    try:
        daily_df = nse.get_daily_candles(symbol, days=5)
        if daily_df is None or len(daily_df) < 2:
            log.warning(f"⚠️ {symbol} — insufficient daily data for pivot calc")
            return None

        prev_day  = daily_df.iloc[-2]         # yesterday's candle
        prev_high  = float(prev_day["high"])
        prev_low   = float(prev_day["low"])
        prev_close = float(prev_day["close"])

        if prev_high <= 0 or prev_low <= 0 or prev_close <= 0:
            log.warning(f"⚠️ {symbol} — invalid prev-day OHLC")
            return None

        levels = calculate_pivots(prev_high, prev_low, prev_close)
        log.debug(f"📐 {symbol} pivots | {levels}")
        return levels

    except Exception as e:
        log.error(f"❌ {symbol} pivot fetch failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
#  HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════

def _atr(df: pd.DataFrame, period: int = 10) -> float:
    """Average True Range."""
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def _candle_body_pct(row: pd.Series) -> float:
    """Return body-to-range ratio (0–1)."""
    candle_range = float(row["high"]) - float(row["low"])
    if candle_range <= 0:
        return 0.0
    body = abs(float(row["close"]) - float(row["open"]))
    return body / candle_range


def _market_supports_long(cfg: PivotBreakoutConfig) -> bool:
    """NIFTY must be green and above its own pivot."""
    if not cfg.market_filter_enabled:
        return True
    try:
        mdf = nse.get_index_candles(cfg.market_symbol)
        mdf = completed_candles(mdf)
        if mdf.empty or len(mdf) < 3:
            return True     # assume ok if data unavailable
        last = mdf.iloc[-1]
        return float(last["close"]) > float(last["open"])
    except Exception:
        return True


def _market_supports_short(cfg: PivotBreakoutConfig) -> bool:
    """NIFTY must be red for short bias."""
    if not cfg.market_filter_enabled:
        return True
    try:
        mdf = nse.get_index_candles(cfg.market_symbol)
        mdf = completed_candles(mdf)
        if mdf.empty or len(mdf) < 3:
            return True
        last = mdf.iloc[-1]
        return float(last["close"]) < float(last["open"])
    except Exception:
        return True


def _already_traded_level(state: SessionState, symbol: str, level: str) -> bool:
    """
    Prevent re-entry at the same pivot level on the same day.
    Looks up state.traded_levels[symbol] set.

    Expects state to have: traded_levels: dict[str, set[str]]
    Add this to SessionState if not already there.
    """
    traded = getattr(state, "traded_levels", {})
    sym_levels = traded.get(symbol, set())
    return level in sym_levels


def _mark_level_traded(state: SessionState, symbol: str, level: str):
    """Mark a pivot level as traded for this session."""
    if not hasattr(state, "traded_levels"):
        state.traded_levels = {}
    state.traded_levels.setdefault(symbol, set()).add(level)
    log.debug(f"🔒 {symbol} level {level} locked for today")


def _prior_candle_respected_level(
    df: pd.DataFrame,
    level_price: float,
    direction: str,          # "LONG" or "SHORT"
    tolerance_pct: float = 0.15,
) -> bool:
    """
    Check that the PRIOR candle was BELOW (for long) or ABOVE (for short)
    the pivot level — i.e. the current candle is the actual breakout candle,
    not a candle that's already been above the level for several bars.
    """
    if len(df) < 2:
        return False
    prev = df.iloc[-2]
    prev_close = float(prev["close"])
    tolerance  = level_price * (tolerance_pct / 100)

    if direction == "LONG":
        return prev_close < level_price + tolerance
    else:
        return prev_close > level_price - tolerance


# ══════════════════════════════════════════════════════════════════
#  LONG SETUP — BREAKOUT ABOVE R1 (or R2)
# ══════════════════════════════════════════════════════════════════

def _check_long(
    symbol: str,
    df: pd.DataFrame,
    levels: PivotLevels,
    state: SessionState,
    cfg: PivotBreakoutConfig,
) -> Optional[Signal]:
    """
    Long entry when price breaks and closes above R1 (primary) or R2.

    Conditions:
      1. Market is green (broad bias)
      2. Price above EMA20 (trend filter)
      3. Current candle closed above the resistance level
      4. Breakout is fresh (prior candle was at/below level)
      5. Not over-extended past the level (avoid chasing)
      6. Strong green candle body
      7. Volume spike > 1.5x
      8. This level not already traded today
      9. SL (below level) is within ATR range
    """
    if not _market_supports_long(cfg):
        return None

    last        = df.iloc[-1]
    close_price = float(last["close"])
    open_price  = float(last["open"])
    candle_low  = float(last["low"])

    # EMA trend filter
    if cfg.trend_filter_enabled:
        ema20 = float(df["close"].ewm(span=cfg.ema_period, adjust=False).mean().iloc[-1])
        if close_price < ema20:
            log.debug(f"  ⛔ {symbol} LONG — below EMA{cfg.ema_period}")
            return None

    # Must be green candle
    if close_price <= open_price:
        return None

    # Check R1 first, then R2
    for level_name, level_price in [("R1", levels.r1), ("R2", levels.r2)]:

        # Already traded this level today?
        if _already_traded_level(state, symbol, f"LONG_{level_name}"):
            log.debug(f"  ⛔ {symbol} — {level_name} already traded today")
            continue

        # Price must be above level by buffer %
        dist_pct = ((close_price - level_price) / level_price) * 100
        if dist_pct < cfg.breakout_buffer_pct:
            log.debug(f"  ⛔ {symbol} {level_name} — not broken yet ({dist_pct:.2f}%)")
            continue

        # Not too extended (chasing)
        if dist_pct > cfg.max_chase_pct:
            log.debug(f"  ⛔ {symbol} {level_name} — too extended ({dist_pct:.2f}% > {cfg.max_chase_pct}%)")
            continue

        # Prior candle must have been near/below level (fresh breakout)
        if not _prior_candle_respected_level(df, level_price, "LONG"):
            log.debug(f"  ⛔ {symbol} {level_name} — not a fresh breakout")
            continue

        # Candle body quality
        if _candle_body_pct(last) < cfg.min_body_pct:
            log.debug(f"  ⛔ {symbol} {level_name} — weak candle body")
            continue

        # Volume confirmation
        vol_avg   = avg_volume(df["volume"], exclude_last=1)
        vol_ratio = float(last["volume"]) / vol_avg if vol_avg > 0 else 0.0
        if vol_ratio < cfg.volume_multiplier_min:
            log.debug(f"  ⛔ {symbol} {level_name} — low volume ({vol_ratio:.2f}x)")
            continue

        # SL = just below the broken level (level acts as new support)
        entry     = round(close_price, 2)
        stop_loss = round(level_price * 0.998, 2)   # 0.2% below level

        if stop_loss >= entry:
            continue

        # ATR-based SL width check
        atr_val = _atr(df)
        risk    = entry - stop_loss
        if atr_val > 0 and risk > atr_val * cfg.max_sl_atr_multiple:
            log.debug(f"  ⛔ {symbol} {level_name} — SL too wide ({risk:.2f} > {atr_val * cfg.max_sl_atr_multiple:.2f})")
            continue

        # Next resistance level = target
        next_level = levels.r2 if level_name == "R1" else levels.r3
        target_by_rr    = round(entry + risk * cfg.reward_ratio, 2)
        target_by_level = round(next_level, 2)
        target          = max(target_by_rr, target_by_level)  # take the higher target

        qty = position_size(
            entry, stop_loss,
            strategy_cfg.account_capital,
            strategy_cfg.risk_pct_per_trade,
            strategy_cfg.max_capital_per_trade,
            strategy_cfg.max_exposure_multiple,
        )

        _mark_level_traded(state, symbol, f"LONG_{level_name}")

        log.info(
            f"✅ PIVOT BREAKOUT LONG | {symbol} | {level_name}=₹{level_price:.2f} broken | "
            f"entry=₹{entry}  sl=₹{stop_loss}  target=₹{target}  qty={qty} | "
            f"vol={vol_ratio:.2f}x  risk=₹{risk:.2f}  atr=₹{atr_val:.2f}  dist={dist_pct:.2f}%"
        )

        return Signal(
            symbol=symbol,
            entry=entry,
            stop_loss=stop_loss,
            target=target,
            quantity=qty,
            capital=round(entry * qty, 2),
            gap_pct=0.0,
            vwap=0.0,
            rsi=0.0,
            vol_ratio=round(vol_ratio, 2),
        )

    return None


# ══════════════════════════════════════════════════════════════════
#  SHORT SETUP — BREAKDOWN BELOW S1 (or S2)
# ══════════════════════════════════════════════════════════════════

def _check_short(
    symbol: str,
    df: pd.DataFrame,
    levels: PivotLevels,
    state: SessionState,
    cfg: PivotBreakoutConfig,
) -> Optional[Signal]:
    """
    Short entry when price breaks and closes below S1 (primary) or S2.

    Conditions mirror the long setup, inverted.
    NOTE: MIS shorting requires the stock to be in F&O or have
    sufficient liquidity. Caller should check this upstream.
    """
    if not _market_supports_short(cfg):
        return None

    last        = df.iloc[-1]
    close_price = float(last["close"])
    open_price  = float(last["open"])
    candle_high = float(last["high"])

    # Must be red candle
    if close_price >= open_price:
        return None

    # EMA trend filter for short (price must be below EMA for short)
    if cfg.trend_filter_enabled:
        ema20 = float(df["close"].ewm(span=cfg.ema_period, adjust=False).mean().iloc[-1])
        if close_price > ema20:
            log.debug(f"  ⛔ {symbol} SHORT — above EMA{cfg.ema_period}, no short bias")
            return None

    # Check S1 first, then S2
    for level_name, level_price in [("S1", levels.s1), ("S2", levels.s2)]:

        if _already_traded_level(state, symbol, f"SHORT_{level_name}"):
            log.debug(f"  ⛔ {symbol} — {level_name} already traded today")
            continue

        # Price must be below level by buffer %
        dist_pct = ((level_price - close_price) / level_price) * 100
        if dist_pct < cfg.breakout_buffer_pct:
            log.debug(f"  ⛔ {symbol} {level_name} — not broken yet ({dist_pct:.2f}%)")
            continue

        if dist_pct > cfg.max_chase_pct:
            log.debug(f"  ⛔ {symbol} {level_name} — too extended ({dist_pct:.2f}%)")
            continue

        if not _prior_candle_respected_level(df, level_price, "SHORT"):
            log.debug(f"  ⛔ {symbol} {level_name} — not a fresh breakdown")
            continue

        if _candle_body_pct(last) < cfg.min_body_pct:
            log.debug(f"  ⛔ {symbol} {level_name} — weak candle body")
            continue

        vol_avg   = avg_volume(df["volume"], exclude_last=1)
        vol_ratio = float(last["volume"]) / vol_avg if vol_avg > 0 else 0.0
        if vol_ratio < cfg.volume_multiplier_min:
            log.debug(f"  ⛔ {symbol} {level_name} — low volume ({vol_ratio:.2f}x)")
            continue

        # SL = just above the broken level (level acts as new resistance)
        entry     = round(close_price, 2)
        stop_loss = round(level_price * 1.002, 2)   # 0.2% above level

        if stop_loss <= entry:
            continue

        atr_val = _atr(df)
        risk    = stop_loss - entry
        if atr_val > 0 and risk > atr_val * cfg.max_sl_atr_multiple:
            log.debug(f"  ⛔ {symbol} {level_name} — SL too wide ({risk:.2f})")
            continue

        # Next support level = target
        next_level      = levels.s2 if level_name == "S1" else levels.s3
        target_by_rr    = round(entry - risk * cfg.reward_ratio, 2)
        target_by_level = round(next_level, 2)
        target          = min(target_by_rr, target_by_level)   # take the lower target

        qty = position_size(
            entry, stop_loss,
            strategy_cfg.account_capital,
            strategy_cfg.risk_pct_per_trade,
            strategy_cfg.max_capital_per_trade,
            strategy_cfg.max_exposure_multiple,
        )

        _mark_level_traded(state, symbol, f"SHORT_{level_name}")

        log.info(
            f"✅ PIVOT BREAKDOWN SHORT | {symbol} | {level_name}=₹{level_price:.2f} broken | "
            f"entry=₹{entry}  sl=₹{stop_loss}  target=₹{target}  qty={qty} | "
            f"vol={vol_ratio:.2f}x  risk=₹{risk:.2f}  atr=₹{atr_val:.2f}  dist={dist_pct:.2f}%"
        )

        return Signal(
            symbol=symbol,
            entry=entry,
            stop_loss=stop_loss,
            target=target,
            quantity=qty,
            capital=round(entry * qty, 2),
            gap_pct=0.0,
            vwap=0.0,
            rsi=0.0,
            vol_ratio=round(vol_ratio, 2),
        )

    return None


# ══════════════════════════════════════════════════════════════════
#  MAIN DETECT
# ══════════════════════════════════════════════════════════════════

def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    """
    Pivot Point Breakout strategy — main entry point.

    Checks LONG (R1/R2 breakout) and SHORT (S1/S2 breakdown).
    Returns the first valid signal found, or None.
    """
    cfg = pivot_breakout_cfg.for_symbol(symbol)

    now = current_hhmm()
    if now < cfg.start_time or now > cfg.end_time:
        return None

    # ── Fetch pivot levels ────────────────────────────────────────
    levels = fetch_pivot_levels(symbol)
    if levels is None:
        return None

    # ── Fetch intraday candles ────────────────────────────────────
    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty or len(df) < 10:
        return None

    # ── Session volume gate ───────────────────────────────────────
    session_volume = float(df["volume"].sum())
    if session_volume < cfg.min_session_volume:
        log.debug(f"  ⛔ {symbol} — low session volume ({session_volume:,.0f})")
        return None

    # ── Log levels once per session ──────────────────────────────
    if not getattr(state, f"_pivot_logged_{symbol}", False):
        log.info(f"📐 {symbol} | {levels}")
        setattr(state, f"_pivot_logged_{symbol}", True)

    # ── Try LONG first ────────────────────────────────────────────
    signal = _check_long(symbol, df, levels, state, cfg)
    if signal:
        return signal

    # ── Try SHORT only if explicitly enabled ─────────────────────
    if cfg.enable_shorts:
        signal = _check_short(symbol, df, levels, state, cfg)
        if signal:
            return signal

    return None


# ══════════════════════════════════════════════════════════════════
#  UTILITY — PRE-MARKET PIVOT REPORT
# ══════════════════════════════════════════════════════════════════

def print_pivot_levels_report(symbols: list[str]):
    """
    Print pivot levels for all symbols before market open.
    Call this at 09:00 AM so you know key levels for the day.

    Example output:
      RELIANCE  | Pivot=₹2945.33 | R1=₹2978.67  R2=₹3008.33 | S1=₹2915.67  S2=₹2882.33
    """
    print("\n" + "═" * 80)
    print("  📐 PIVOT LEVELS REPORT")
    print("═" * 80)
    print(f"  {'SYMBOL':<14}  {'PIVOT':>9}  {'R1':>9}  {'R2':>9}  {'S1':>9}  {'S2':>9}")
    print("  " + "─" * 60)

    for symbol in symbols:
        levels = fetch_pivot_levels(symbol)
        if levels:
            print(
                f"  {symbol:<14}  "
                f"₹{levels.pivot:>8.2f}  "
                f"₹{levels.r1:>8.2f}  "
                f"₹{levels.r2:>8.2f}  "
                f"₹{levels.s1:>8.2f}  "
                f"₹{levels.s2:>8.2f}"
            )
        else:
            print(f"  {symbol:<14}  ⚠️ Data unavailable")

    print("═" * 80 + "\n")


# ══════════════════════════════════════════════════════════════════
#  STANDALONE TEST
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    Test pivot calculation with known values.
    Run: python -m strategy.pivot_breakout
    """
    # Known example: prev day H=2980, L=2900, C=2950
    levels = calculate_pivots(prev_high=2980, prev_low=2900, prev_close=2950)
    print("\n📐 Test Pivot Calculation")
    print(f"  Input  → H=2980  L=2900  C=2950")
    print(f"  Pivot  = ₹{levels.pivot}")
    print(f"  R1     = ₹{levels.r1}   (expected ~2977.33)")
    print(f"  R2     = ₹{levels.r2}   (expected ~3057.33)")
    print(f"  S1     = ₹{levels.s1}   (expected ~2897.33)")
    print(f"  S2     = ₹{levels.s2}   (expected ~2817.33)")

    # Report for key symbols
    print_pivot_levels_report([
        "RELIANCE", "SBIN", "ICICIBANK",
        "TATASTEEL", "HINDALCO", "NATIONALUM"
    ])
