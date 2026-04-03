"""
ATH Mean-Reversion Short Strategy v5
──────────────────────────────────────
Shorts stocks that gap-up and push to intraday ATH, then show exhaustion.

Entry logic — score ≥ min_signal_score (5 possible points):
  1. Overextended  : price is > ema_dist_threshold% above EMA-20
  2. RSI divergence: price makes higher high, RSI makes lower high at swing tops
  3. Volume climax : recent candle had climax volume + followed by reversal candle
  4. Candle pattern: Shooting Star OR Bearish Engulfing present
  5. Lower high    : most recent swing high is lower than the one before it

Additional hard gates (must ALL pass):
  - Price is within day_high_proximity% of the session's running high
  - RSI is in the overbought zone (≥ rsi_overbought)
  - Current candle is bearish (close < open)

Exit:
  - Stop-loss  : entry + atr_sl_mult × ATR  (above entry — short squeeze guard)
  - Take-profit: entry − atr_tp_mult × ATR  (mean reversion target)
  - EOD flat   : position closed at session end

This is a SHORT-ONLY strategy for intraday MIS use.
"""

from __future__ import annotations

from typing import Optional
from datetime import time

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

from core.signal import Signal
from core.session import SessionState
from strategy.indicators import completed_candles, position_size
from data import upstox_provider as nse
from config.settings import strategy_cfg
from config.v5.ath_reversal import ath_reversal_cfg
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


# ─────────────────────────────────────────────
#  CONFIG HELPERS
# ─────────────────────────────────────────────

def _parse_time(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _cfg_dict() -> dict:
    return {
        "symbol":             ath_reversal_cfg.symbol,
        "timeframe":          ath_reversal_cfg.timeframe,
        "rsi_period":         ath_reversal_cfg.rsi_period,
        "ema_period":         ath_reversal_cfg.ema_period,
        "atr_period":         ath_reversal_cfg.atr_period,
        "volume_lookback":    ath_reversal_cfg.volume_lookback,
        "ema_dist_threshold": ath_reversal_cfg.ema_dist_threshold,
        "volume_climax_mult": ath_reversal_cfg.volume_climax_mult,
        "swing_window":       ath_reversal_cfg.swing_window,
        "day_high_proximity": ath_reversal_cfg.day_high_proximity,
        "rsi_overbought":     ath_reversal_cfg.rsi_overbought,
        "min_signal_score":   ath_reversal_cfg.min_signal_score,
        "cooldown_candles":   ath_reversal_cfg.cooldown_candles,
        "atr_sl_mult":        ath_reversal_cfg.atr_sl_mult,
        "atr_tp_mult":        ath_reversal_cfg.atr_tp_mult,
        "capital":            ath_reversal_cfg.capital,
        "risk_pct":           ath_reversal_cfg.risk_pct,
        "session_start":      _parse_time(ath_reversal_cfg.session_start),
        "session_end":        _parse_time(ath_reversal_cfg.session_end),
        "blocklist":          ath_reversal_cfg.blocklist,
    }


# ─────────────────────────────────────────────
#  INDICATOR CALCULATIONS
# ─────────────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    tp     = (df["high"] + df["low"] + df["close"]) / 3
    tp_vol = tp * df["volume"]
    dates  = df.index.normalize()
    vwap   = pd.Series(index=df.index, dtype=float)
    for day in dates.unique():
        mask       = dates == day
        cum_tv     = tp_vol[mask].cumsum()
        cum_v      = df.loc[mask, "volume"].cumsum()
        vwap[mask] = cum_tv / cum_v.replace(0, np.nan)
    return vwap


def add_all_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()

    # Core indicators
    df["rsi"]     = compute_rsi(df["close"], cfg["rsi_period"])
    df["ema20"]   = compute_ema(df["close"], cfg["ema_period"])
    df["atr"]     = compute_atr(df, cfg["atr_period"])
    df["avg_vol"] = df["volume"].rolling(cfg["volume_lookback"]).mean()
    df["vwap"]    = compute_vwap(df)

    # EMA distance (kept for scoring reference)
    df["ema_dist_pct"] = (df["close"] - df["ema20"]) / df["ema20"].replace(0, np.nan)

    # VWAP distance — primary overextension signal for intraday.
    # EMA-20 on 5-min intraday candles tracks price too closely to be useful;
    # VWAP resets each day and shows where most volume traded, making
    # "price > VWAP * (1 + threshold)" a genuine intraday overextension signal.
    df["vwap_dist_pct"] = (df["close"] - df["vwap"]) / df["vwap"].replace(0, np.nan)
    df["overextended"]  = df["vwap_dist_pct"] > cfg["ema_dist_threshold"]

    # Day's running high (reset each calendar day)
    df["day_high"]      = df.groupby(df.index.date)["high"].transform("cummax")
    df["near_day_high"] = (df["day_high"] - df["close"]) / df["day_high"].replace(0, np.nan) \
                          <= cfg["day_high_proximity"]

    # Candle anatomy
    df["body"]        = (df["close"] - df["open"]).abs()
    df["upper_wick"]  = df["high"] - df[["open", "close"]].max(axis=1)
    df["lower_wick"]  = df[["open", "close"]].min(axis=1) - df["low"]
    df["bearish"]     = df["close"] < df["open"]

    # Shooting Star:  long upper wick (≥ 2× body), tiny lower wick
    df["shooting_star"] = (
        (df["upper_wick"] >= 2 * df["body"]) &
        (df["upper_wick"] >= 2 * df["lower_wick"]) &
        (df["body"] > 0)
    )

    # Bearish Engulfing: current candle (bearish) fully engulfs previous (bullish)
    prev_open  = df["open"].shift(1)
    prev_close = df["close"].shift(1)
    df["bearish_engulf"] = (
        df["bearish"] &
        (prev_close > prev_open) &          # previous was bullish
        (df["open"]  >= prev_close) &        # gap-up or equal open
        (df["close"] <= prev_open)           # close below prev open
    )

    # Volume climax + reversal:
    #   Previous candle: bullish + climax volume
    #   Current  candle: bearish (sellers have taken over)
    vol_spike_prev  = df["volume"].shift(1) > cfg["volume_climax_mult"] * df["avg_vol"].shift(1)
    bullish_prev    = df["close"].shift(1) > df["open"].shift(1)
    df["vol_climax_reversal"] = vol_spike_prev & bullish_prev & df["bearish"]

    # Swing high marker (local maximum: highest in swing_window candles each side)
    # Computed here for use in the signal loop
    sw = cfg["swing_window"]
    df["swing_high"] = False
    for i in range(sw, len(df) - sw):
        window_high = df["high"].iloc[i - sw: i + sw + 1].max()
        if df["high"].iloc[i] == window_high:
            df["swing_high"].iloc[i] = True

    return df


# ─────────────────────────────────────────────
#  SWING HIGH HELPERS
# ─────────────────────────────────────────────

def _get_swing_high_indices(df: pd.DataFrame, before_idx: int, n: int = 2) -> list:
    """Return the last n swing-high positions strictly before before_idx."""
    mask   = df["swing_high"].iloc[:before_idx]
    idxs   = list(mask[mask].index)
    # Convert to positional integers
    pos    = [df.index.get_loc(ix) for ix in idxs]
    return pos[-n:]


def _has_rsi_divergence(df: pd.DataFrame, swing_pos: list) -> bool:
    """
    Bearish RSI divergence: price makes equal/higher high at second swing,
    but RSI makes a LOWER high — momentum is fading.
    """
    if len(swing_pos) < 2:
        return False
    sh1, sh2 = swing_pos[-2], swing_pos[-1]
    price_same_or_higher = df["high"].iloc[sh2] >= df["high"].iloc[sh1] * 0.995
    rsi_lower            = df["rsi"].iloc[sh2]  <  df["rsi"].iloc[sh1]
    return price_same_or_higher and rsi_lower


def _has_lower_high(df: pd.DataFrame, swing_pos: list) -> bool:
    """
    Lower high: the most recent swing high is meaningfully lower than
    the one before it — the uptrend is losing momentum.
    """
    if len(swing_pos) < 2:
        return False
    sh1, sh2 = swing_pos[-2], swing_pos[-1]
    return df["high"].iloc[sh2] < df["high"].iloc[sh1] * 0.999


# ─────────────────────────────────────────────
#  SIGNAL SCORING
# ─────────────────────────────────────────────

def score_short(row: pd.Series, df: pd.DataFrame, i: int, cfg: dict) -> int:
    """
    Score a potential SHORT signal (max 5).
      1. Overextended above EMA-20
      2. RSI bearish divergence at recent swing highs
      3. Volume climax + reversal candle
      4. Shooting Star OR Bearish Engulfing
      5. Lower High confirmed at recent swing highs
    """
    score = 0

    # 1. Overextended
    if row["overextended"]:
        score += 1

    # 2 & 5. RSI divergence + lower high (need swing highs)
    swing_pos = _get_swing_high_indices(df, i)
    if _has_rsi_divergence(df, swing_pos):
        score += 1
    if _has_lower_high(df, swing_pos):
        score += 1

    # 3. Volume climax reversal
    if row["vol_climax_reversal"]:
        score += 1

    # 4. Candle pattern
    if row["shooting_star"] or row["bearish_engulf"]:
        score += 1

    return score


# ─────────────────────────────────────────────
#  SESSION + SIZING
# ─────────────────────────────────────────────

def in_session(ts, cfg: dict) -> bool:
    t = ts.time() if hasattr(ts, "time") else ts
    return cfg["session_start"] <= t <= cfg["session_end"]


def bot_position_size(entry: float, sl: float, cfg: dict) -> int:
    risk_amount   = cfg["capital"] * cfg["risk_pct"]
    risk_per_unit = abs(sl - entry)
    if risk_per_unit == 0:
        return 0
    return max(1, int(risk_amount / risk_per_unit))


# ─────────────────────────────────────────────
#  LIVE DETECT  (project-compatible interface)
# ─────────────────────────────────────────────

def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    """
    Project-compatible detector for v5 ATH Reversal.
    Returns a SHORT Signal when exhaustion conditions are met.
    """
    now = current_hhmm()
    if now < ath_reversal_cfg.session_start or now > ath_reversal_cfg.session_end:
        return None

    if symbol.upper() in ath_reversal_cfg.blocklist:
        log.debug(f"⛔ {symbol} in ATH reversal blocklist — skipping")
        return None

    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty:
        return None

    df = df.copy()
    if "time" in df.columns:
        df = df.set_index("time")

    warmup = max(ath_reversal_cfg.ema_period,
                 ath_reversal_cfg.rsi_period,
                 ath_reversal_cfg.volume_lookback) + ath_reversal_cfg.swing_window + 5
    if len(df) < warmup:
        return None

    cfg  = _cfg_dict()
    df   = add_all_indicators(df, cfg)
    last = df.iloc[-1]
    i    = len(df) - 1

    # ── Hard gates ──────────────────────────────────────────────────────
    if not bool(last["near_day_high"]):
        return None                                # not near intraday ATH
    if float(last["rsi"]) < cfg["rsi_overbought"]:
        return None                                # not in overbought zone
    if not bool(last["bearish"]):
        return None                                # current candle must be bearish
    if not bool(last["overextended"]):
        return None                                # must be extended above EMA

    # ── Score ───────────────────────────────────────────────────────────
    score = score_short(last, df, i, cfg)
    if score < cfg["min_signal_score"]:
        return None

    # ── Build signal ────────────────────────────────────────────────────
    entry = round(float(last["close"]), 2)
    atr   = float(last["atr"])
    if pd.isna(atr) or atr <= 0:
        return None

    stop_loss = round(entry + cfg["atr_sl_mult"] * atr, 2)   # ABOVE entry
    target    = round(entry - cfg["atr_tp_mult"] * atr, 2)   # BELOW entry

    qty = position_size(
        entry, stop_loss,
        strategy_cfg.account_capital,
        strategy_cfg.risk_pct_per_trade,
        strategy_cfg.max_capital_per_trade,
        strategy_cfg.max_exposure_multiple,
    )
    if qty <= 0:
        return None

    ema_val      = round(float(last["ema20"]), 2)
    rsi_val      = round(float(last["rsi"]), 1)
    ema_dist_pct = round(float(last["ema_dist_pct"]) * 100, 2)
    vol_ratio    = float(last["volume"] / last["avg_vol"]) \
                   if pd.notna(last["avg_vol"]) and float(last["avg_vol"]) > 0 else 0.0

    log.info(
        f"🔻 ATH Reversal v5 | {symbol} | SHORT | entry=₹{entry}  "
        f"sl=₹{stop_loss}  target=₹{target}  qty={qty}  score={score}/5  "
        f"ema=₹{ema_val}  dist={ema_dist_pct:.1f}%  rsi={rsi_val}  vol={vol_ratio:.2f}x"
    )

    return Signal(
        symbol    = symbol,
        entry     = entry,
        stop_loss = stop_loss,
        target    = target,
        quantity  = qty,
        capital   = round(entry * qty, 2),
        rsi       = rsi_val,
        vol_ratio = round(vol_ratio, 2),
        direction = "SHORT",
    )


# ─────────────────────────────────────────────
#  CORE SIGNAL GENERATOR  (backtest engine)
# ─────────────────────────────────────────────

def generate_signals(df: pd.DataFrame, cfg: dict, symbol: str = "") -> pd.DataFrame:
    """
    Walk bar-by-bar and emit SHORT signals when all conditions are met.
    Returns df with columns: signal, score, entry, sl, tp, qty.
    signal: -1 = SHORT,  0 = NO TRADE.
    """
    if symbol.upper() in cfg.get("blocklist", set()):
        df["signal"] = 0; df["score"] = 0
        df["entry"]  = np.nan; df["sl"] = np.nan
        df["tp"]     = np.nan; df["qty"] = 0
        return df

    df = add_all_indicators(df, cfg)

    signals = []; scores = []; entries = []
    sls     = []; tps    = []; qtys   = []
    last_signal_candle = -cfg["cooldown_candles"] - 1

    for i, (ts, row) in enumerate(df.iterrows()):

        # ── Session filter ─────────────────────────────────────────────
        if not in_session(ts, cfg):
            signals.append(0); scores.append(0)
            entries.append(np.nan); sls.append(np.nan)
            tps.append(np.nan); qtys.append(0)
            continue

        # ── Cooldown guard ─────────────────────────────────────────────
        if i - last_signal_candle < cfg["cooldown_candles"]:
            signals.append(0); scores.append(0)
            entries.append(np.nan); sls.append(np.nan)
            tps.append(np.nan); qtys.append(0)
            continue

        atr   = row["atr"]
        entry = row["close"]

        # ── Hard gates ─────────────────────────────────────────────────
        if not (
            bool(row["near_day_high"])   and  # near intraday peak
            float(row["rsi"]) >= cfg["rsi_overbought"] and  # overbought zone
            bool(row["bearish"])         and  # current candle is bearish
            bool(row["overextended"])    and  # extended above EMA
            pd.notna(atr) and atr > 0
        ):
            signals.append(0); scores.append(0)
            entries.append(np.nan); sls.append(np.nan)
            tps.append(np.nan); qtys.append(0)
            continue

        # ── Score ───────────────────────────────────────────────────────
        s = score_short(row, df, i, cfg)
        if s < cfg["min_signal_score"]:
            signals.append(0); scores.append(0)
            entries.append(np.nan); sls.append(np.nan)
            tps.append(np.nan); qtys.append(0)
            continue

        # ── Build levels ────────────────────────────────────────────────
        sl  = round(entry + cfg["atr_sl_mult"] * atr, 2)   # above entry
        tp  = round(entry - cfg["atr_tp_mult"] * atr, 2)   # below entry
        qty = bot_position_size(entry, sl, cfg)

        signals.append(-1); scores.append(s)
        entries.append(round(entry, 2))
        sls.append(sl); tps.append(tp); qtys.append(qty)
        last_signal_candle = i

    df["signal"] = signals;  df["score"]  = scores
    df["entry"]  = entries;  df["sl"]     = sls
    df["tp"]     = tps;      df["qty"]    = qtys
    return df


# ─────────────────────────────────────────────
#  BACKTESTER  (candle-close simulation)
# ─────────────────────────────────────────────

def backtest(df: pd.DataFrame, cfg: dict = None) -> pd.DataFrame:
    """
    Next-candle-open fill simulation for SHORT signals.
    SHORT:  SL hit when HIGH >= sl, TP hit when LOW <= tp.
    """
    trades      = []
    signal_rows = df[df["signal"] != 0].copy()

    for idx, row in signal_rows.iterrows():
        loc = df.index.get_loc(idx)
        if loc + 1 >= len(df):
            continue

        fill_price = df.iloc[loc + 1]["open"]
        sl, tp     = row["sl"], row["tp"]
        qty        = row["qty"]
        entry_time = df.index[loc + 1]

        pnl    = None
        reason = "EOD"

        for j in range(loc + 1, len(df)):
            c = df.iloc[j]
            if c["high"] >= sl:                            # SHORT stop-loss
                pnl = (fill_price - sl) * qty
                reason = "SL"
                break
            if c["low"] <= tp:                             # SHORT take-profit
                pnl = (fill_price - tp) * qty
                reason = "TP"
                break

        if pnl is None:
            exit_price = df.iloc[-1]["close"]
            pnl = (fill_price - exit_price) * qty          # SHORT EOD PnL

        trades.append({
            "entry_time" : entry_time,
            "direction"  : "SHORT",
            "score"      : row["score"],
            "fill"       : round(fill_price, 2),
            "sl"         : round(sl, 2),
            "tp"         : round(tp, 2),
            "qty"        : qty,
            "pnl"        : round(pnl, 2),
            "exit_reason": reason,
        })

    return pd.DataFrame(trades)


# ─────────────────────────────────────────────
#  PERFORMANCE REPORT
# ─────────────────────────────────────────────

def performance_report(trades: pd.DataFrame, cfg: dict):
    if trades.empty:
        print("⚠  No trades generated.")
        return

    total    = len(trades)
    wins     = trades[trades["pnl"] > 0]
    losses   = trades[trades["pnl"] <= 0]
    win_rate = len(wins) / total * 100
    gross    = trades["pnl"].sum()
    avg_win  = wins["pnl"].mean()   if not wins.empty   else 0
    avg_loss = losses["pnl"].mean() if not losses.empty else 0
    rr       = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    max_dd   = (trades["pnl"].cumsum() - trades["pnl"].cumsum().cummax()).min()

    print("\n" + "═" * 58)
    print(f"  ATH REVERSAL v5 (SHORT)  ·  {cfg['symbol']}  [{cfg['timeframe']}]")
    print("═" * 58)
    print(f"  Total Trades   : {total}")
    print(f"  Win Rate       : {win_rate:.1f}%")
    print(f"  Gross P&L      : ₹{gross:,.2f}")
    print(f"  Avg Win        : ₹{avg_win:,.2f}")
    print(f"  Avg Loss       : ₹{avg_loss:,.2f}")
    print(f"  Reward:Risk    : {rr:.2f}")
    print(f"  Max Drawdown   : ₹{max_dd:,.2f}")
    print(f"  TP Exits       : {(trades['exit_reason'] == 'TP').sum()}")
    print(f"  SL Exits       : {(trades['exit_reason'] == 'SL').sum()}")
    print(f"  EOD Exits      : {(trades['exit_reason'] == 'EOD').sum()}")
    print("═" * 58)
    print(trades[["entry_time", "score", "fill", "sl", "tp",
                  "qty", "pnl", "exit_reason"]].to_string(index=False))
    print("═" * 58 + "\n")


# ─────────────────────────────────────────────
#  LIVE / PAPER TRADING BOT
# ─────────────────────────────────────────────

class ATHReversalBot:
    """Real-time wrapper. Feed one completed candle at a time via on_candle()."""

    def __init__(self, cfg: Optional[dict] = None):
        self.cfg           = cfg or _cfg_dict()
        self.history       = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        self.history.index.name = "timestamp"
        self.in_trade      = False
        self.entry_price   = None
        self.sl            = None
        self.tp            = None

    def on_candle(self, timestamp, open_, high, low, close, volume):
        new_row = pd.DataFrame(
            [[open_, high, low, close, volume]],
            index   = pd.DatetimeIndex([timestamp]),
            columns = ["open", "high", "low", "close", "volume"],
        )
        self.history = pd.concat([self.history, new_row])

        warmup = max(self.cfg["ema_period"], self.cfg["rsi_period"],
                     self.cfg["volume_lookback"]) + self.cfg["swing_window"] + 5
        if len(self.history) < warmup:
            return

        df   = add_all_indicators(self.history.copy(), self.cfg)
        last = df.iloc[-1]
        i    = len(df) - 1

        # ── Exit check ─────────────────────────────────────────────────
        if self.in_trade:
            if high >= self.sl:
                self._exit("SL hit", self.sl); return
            if low  <= self.tp:
                self._exit("TP hit", self.tp); return
            return

        # ── Entry check ────────────────────────────────────────────────
        if not in_session(timestamp, self.cfg):
            return

        if not (
            bool(last["near_day_high"]) and
            float(last["rsi"]) >= self.cfg["rsi_overbought"] and
            bool(last["bearish"])       and
            bool(last["overextended"])
        ):
            return

        s = score_short(last, df, i, self.cfg)
        if s >= self.cfg["min_signal_score"]:
            atr = float(last["atr"])
            sl  = round(close + self.cfg["atr_sl_mult"] * atr, 2)
            tp  = round(close - self.cfg["atr_tp_mult"] * atr, 2)
            qty = bot_position_size(close, sl, self.cfg)
            self._enter(close, sl, tp, qty, s)

    def _enter(self, price, sl, tp, qty, score):
        print(f"\n🔻 SHORT SIGNAL  Score:{score}/5")
        print(f"   Entry=₹{price:.2f}  SL=₹{sl:.2f}  TP=₹{tp:.2f}  Qty={qty}")
        self.in_trade    = True
        self.entry_price = price
        self.sl          = sl
        self.tp          = tp
        self.place_order("SHORT", "ENTER", price, qty)

    def _exit(self, reason, price):
        pnl = round((self.entry_price - price) * 1, 2)   # direction-aware
        print(f"🟢 EXIT [SHORT]  Reason: {reason}  @ ₹{price:.2f}  PnL/share: {pnl:+.2f}")
        self.place_order("SHORT", "EXIT", price, 0)
        self.in_trade    = False
        self.entry_price = None

    def place_order(self, side, action, price, qty):
        """
        ── BROKER INTEGRATION POINT ──
        Replace with your actual broker API (Zerodha Kite, etc.)
        For SHORT in MIS:
            transaction_type = kite.TRANSACTION_TYPE_SELL  (entry)
            transaction_type = kite.TRANSACTION_TYPE_BUY   (exit / cover)
        """
        print(f"   [ORDER] {action} {side} | Price≈₹{price:.2f} | Qty={qty}")


# ─────────────────────────────────────────────
#  DEMO  (synthetic trending + exhaustion data)
# ─────────────────────────────────────────────

def generate_demo_data(n: int = 400) -> pd.DataFrame:
    """
    Generates synthetic intraday data that simulates a gap-up rally
    followed by exhaustion — ideal for testing ATH reversal logic.
    """
    np.random.seed(7)
    dates  = pd.date_range("2024-06-03 09:15", periods=n, freq="5min")

    # Simulate gap-up (first 30 candles rally hard), then exhaustion/reversal
    trend  = np.concatenate([
        np.linspace(0, 800, 80),        # strong initial rally
        np.linspace(800, 850, 40),       # slowing push (exhaustion zone)
        np.linspace(850, 600, 120),      # mean reversion
        np.random.randn(n - 240) * 30,   # remainder noise
    ])
    base   = 2400
    close  = base + trend + np.random.randn(n) * 15
    spread = np.abs(np.random.randn(n)) * 20 + 10
    high   = close + spread
    low    = close - spread
    open_  = close - np.random.randn(n) * 12

    # Spike volume during exhaustion zone (candles 80-120)
    volume = np.abs(np.random.randn(n) * 3000 + 12000).astype(int)
    volume[80:120] = volume[80:120] * 3

    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }, index=dates)


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("═" * 58)
    print("  ATH REVERSAL BOT v5  ·  Loading demo data …")
    print("═" * 58)

    CONFIG = _cfg_dict()
    df     = generate_demo_data(400)

    df_signals = generate_signals(df, CONFIG)
    trades     = backtest(df_signals, CONFIG)
    performance_report(trades, CONFIG)

    print("\n── Live Bot Usage Example ──")
    bot = ATHReversalBot(CONFIG)
    for ts, row in df.iloc[:150].iterrows():
        bot.on_candle(ts, row["open"], row["high"], row["low"], row["close"], row["volume"])
