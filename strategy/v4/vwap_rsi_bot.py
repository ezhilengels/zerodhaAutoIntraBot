"""
VWAP + RSI Combo Trading Bot v4
───────────────────────────────

Adapted into the project strategy interface without touching v1/v2/v3.
The original standalone helpers are retained, and `detect()` exposes the
project-compatible long-only-by-default signal path.
"""

from typing import Optional

import pandas as pd
import numpy as np
from datetime import datetime, time
import warnings
warnings.filterwarnings("ignore")

from core.signal import Signal
from core.session import SessionState
from strategy.indicators import completed_candles, position_size
from data import nse_provider as nse
from config.settings import strategy_cfg
from config.v4.vwap_rsi import vwap_rsi_v4_cfg
from utils.logger import get_logger
from utils.time_helpers import current_hhmm

log = get_logger(__name__)


def _cfg_dict() -> dict:
    return {
        "symbol": vwap_rsi_v4_cfg.symbol,
        "timeframe": vwap_rsi_v4_cfg.timeframe,
        "rsi_period": vwap_rsi_v4_cfg.rsi_period,
        "ema_fast": vwap_rsi_v4_cfg.ema_fast,
        "ema_slow": vwap_rsi_v4_cfg.ema_slow,
        "atr_period": vwap_rsi_v4_cfg.atr_period,
        "atr_sl_mult": vwap_rsi_v4_cfg.atr_sl_mult,
        "atr_tp_mult": vwap_rsi_v4_cfg.atr_tp_mult,
        "volume_mult": vwap_rsi_v4_cfg.volume_mult,
        "volume_lookback": vwap_rsi_v4_cfg.volume_lookback,
        "min_signal_score": vwap_rsi_v4_cfg.min_signal_score,
        "cooldown_candles": vwap_rsi_v4_cfg.cooldown_candles,
        "session_start": _parse_time(vwap_rsi_v4_cfg.session_start),
        "session_end": _parse_time(vwap_rsi_v4_cfg.session_end),
        "capital": vwap_rsi_v4_cfg.capital,
        "risk_pct": vwap_rsi_v4_cfg.risk_pct,
        "enable_shorts": vwap_rsi_v4_cfg.enable_shorts,
        "vwap_max_dist_pct": vwap_rsi_v4_cfg.vwap_max_dist_pct,
        "blocklist": vwap_rsi_v4_cfg.blocklist,
    }


def _parse_time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))

# ─────────────────────────────────────────────
#  INDICATOR CALCULATIONS
# ─────────────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's smoothed RSI."""
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Session VWAP — resets every trading day.
    Requires columns: high, low, close, volume, and a datetime index.
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    tp_vol = typical_price * df["volume"]

    dates = df.index.normalize()          # date portion of timestamp
    vwap  = pd.Series(index=df.index, dtype=float)

    for day in dates.unique():
        mask          = dates == day
        cumulative_tv = tp_vol[mask].cumsum()
        cumulative_v  = df.loc[mask, "volume"].cumsum()
        vwap[mask]    = cumulative_tv / cumulative_v.replace(0, np.nan)
    return vwap


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    h, l, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([
        h - l,
        (h - pc).abs(),
        (l - pc).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period, adjust=False).mean()


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def add_all_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Compute and attach all indicators to the DataFrame."""
    df = df.copy()
    df["rsi"]      = compute_rsi(df["close"], cfg["rsi_period"])
    df["vwap"]     = compute_vwap(df)
    df["atr"]      = compute_atr(df, cfg["atr_period"])
    df["ema_fast"] = compute_ema(df["close"], cfg["ema_fast"])
    df["ema_slow"] = compute_ema(df["close"], cfg["ema_slow"])
    df["avg_vol"]  = df["volume"].rolling(cfg["volume_lookback"]).mean()

    # Derived helpers
    df["rsi_prev"]     = df["rsi"].shift(1)
    df["above_vwap"]   = df["close"] > df["vwap"]
    df["rsi_cross_up"] = (df["rsi_prev"] < 50) & (df["rsi"] >= 50)
    df["rsi_cross_dn"] = (df["rsi_prev"] > 50) & (df["rsi"] <= 50)
    df["vol_spike"]    = df["volume"] > cfg["volume_mult"] * df["avg_vol"]
    df["ema_bull"]     = df["ema_fast"] > df["ema_slow"]
    df["ema_bear"]     = df["ema_fast"] < df["ema_slow"]
    # How far price has stretched above/below VWAP as a fraction
    df["vwap_dist_pct"] = (df["close"] - df["vwap"]) / df["vwap"].replace(0, np.nan)
    return df


# ─────────────────────────────────────────────
#  SIGNAL SCORING
# ─────────────────────────────────────────────

def score_long(row: pd.Series, cfg: dict) -> int:
    """Score a potential LONG signal (max 5)."""
    score = 0
    score += 1 if row["above_vwap"]   else 0   # 1: price above VWAP
    score += 1 if row["rsi_cross_up"] else 0   # 2: RSI crossed 50 from below
    score += 1 if row["vol_spike"]    else 0   # 3: volume confirms
    score += 1 if row["ema_bull"]     else 0   # 4: EMA trend bullish
    score += 1 if row["rsi"] < 70     else 0   # 5: RSI not overbought
    return score


def score_short(row: pd.Series, cfg: dict) -> int:
    """Score a potential SHORT signal (max 5)."""
    score = 0
    score += 1 if not row["above_vwap"] else 0  # 1: price below VWAP
    score += 1 if row["rsi_cross_dn"]   else 0  # 2: RSI dropped below 50
    score += 1 if row["vol_spike"]      else 0  # 3: volume confirms
    score += 1 if row["ema_bear"]       else 0  # 4: EMA trend bearish
    score += 1 if row["rsi"] > 30       else 0  # 5: RSI not oversold
    return score


# ─────────────────────────────────────────────
#  SESSION FILTER
# ─────────────────────────────────────────────

def in_session(ts, cfg: dict) -> bool:
    """Returns True if the timestamp is within allowed trading hours."""
    t = ts.time() if hasattr(ts, "time") else ts
    return cfg["session_start"] <= t <= cfg["session_end"]


# ─────────────────────────────────────────────
#  POSITION SIZING
# ─────────────────────────────────────────────

def bot_position_size(entry: float, sl: float, cfg: dict) -> int:
    """
    Risk-based position sizing.
    Qty = (Capital × Risk%) / (Entry − SL)
    Returns integer quantity (lots or shares).
    """
    risk_amount  = cfg["capital"] * cfg["risk_pct"]
    risk_per_unit = abs(entry - sl)
    if risk_per_unit == 0:
        return 0
    return max(1, int(risk_amount / risk_per_unit))


def detect(symbol: str, state: SessionState) -> Optional[Signal]:
    """
    Project-compatible detector for v4.

    Current broker/signal flow is long-oriented, so shorts are disabled by
    default and only become active if explicitly enabled in v4 env config.
    """
    now = current_hhmm()
    if now < vwap_rsi_v4_cfg.session_start or now > vwap_rsi_v4_cfg.session_end:
        return None
    if symbol.upper() in vwap_rsi_v4_cfg.blocklist:
        log.debug(f"⛔ {symbol} is in blocklist — skipping")
        return None

    df = nse.get_candles(symbol)
    df = completed_candles(df)
    if df.empty:
        return None

    df = df.copy()
    if "time" in df.columns:
        df = df.set_index("time")
    if len(df) < max(vwap_rsi_v4_cfg.rsi_period, vwap_rsi_v4_cfg.ema_slow, vwap_rsi_v4_cfg.volume_lookback) + 5:
        return None

    cfg = _cfg_dict()
    enriched = add_all_indicators(df, cfg)
    last = enriched.iloc[-1]

    signal = 0
    score = 0
    if bool(last["rsi_cross_up"]) and bool(last["above_vwap"]):
        # Hard gate: price must not be extended too far above VWAP (avoids chasing)
        # vol_spike and ema_bull kept as score-only — momentum names signal before
        # volume fully catches up, so hard-gating on volume kills early-trend entries
        vwap_dist = float(last["vwap_dist_pct"]) if pd.notna(last["vwap_dist_pct"]) else 0.0
        if vwap_dist > cfg["vwap_max_dist_pct"]:
            return None
        score = score_long(last, cfg)
        if score >= cfg["min_signal_score"]:
            signal = 1
    elif cfg["enable_shorts"] and bool(last["rsi_cross_dn"]) and not bool(last["above_vwap"]):
        score = score_short(last, cfg)
        if score >= cfg["min_signal_score"]:
            signal = -1

    if signal == 0:
        return None

    entry = round(float(last["close"]), 2)
    atr = float(last["atr"])
    if pd.isna(atr) or atr <= 0:
        return None

    if signal == 1:
        stop_loss = round(entry - cfg["atr_sl_mult"] * atr, 2)
        target = round(entry + cfg["atr_tp_mult"] * atr, 2)
    else:
        return None

    if stop_loss >= entry:
        return None

    qty = position_size(
        entry,
        stop_loss,
        strategy_cfg.account_capital,
        strategy_cfg.risk_pct_per_trade,
        strategy_cfg.max_capital_per_trade,
        strategy_cfg.max_exposure_multiple,
    )
    if qty <= 0:
        return None

    vwap_val = float(last["vwap"])
    rsi_val = float(last["rsi"])
    vol_ratio = float(last["volume"] / last["avg_vol"]) if pd.notna(last["avg_vol"]) and float(last["avg_vol"]) > 0 else 0.0

    log.info(
        f"✅ VWAP+RSI v4 | {symbol} | entry=₹{entry}  sl=₹{stop_loss}  target=₹{target}  "
        f"qty={qty}  score={score}/5  vwap=₹{vwap_val:.2f}  rsi={rsi_val:.1f}  vol={vol_ratio:.2f}x"
    )

    return Signal(
        symbol=symbol,
        entry=entry,
        stop_loss=stop_loss,
        target=target,
        quantity=qty,
        capital=round(entry * qty, 2),
        gap_pct=0.0,
        vwap=round(vwap_val, 2),
        rsi=round(rsi_val, 1),
        vol_ratio=round(vol_ratio, 2),
    )


# ─────────────────────────────────────────────
#  CORE SIGNAL GENERATOR
# ─────────────────────────────────────────────

def generate_signals(df: pd.DataFrame, cfg: dict, symbol: str = "") -> pd.DataFrame:
    """
    Main signal engine.
    Returns df with columns: signal, score, entry, sl, tp, qty
    signal:  1 = LONG, -1 = SHORT, 0 = NO TRADE
    """
    if symbol.upper() in cfg.get("blocklist", set()):
        df["signal"] = 0; df["score"] = 0
        df["entry"] = np.nan; df["sl"] = np.nan
        df["tp"] = np.nan; df["qty"] = 0
        return df
    df = add_all_indicators(df, cfg)

    signals    = []
    scores     = []
    entries    = []
    sls        = []
    tps        = []
    qtys       = []

    last_signal_candle = -cfg["cooldown_candles"] - 1  # allow first trade

    for i, (ts, row) in enumerate(df.iterrows()):

        signal = 0
        score  = 0

        # ── skip if not in session ──
        if not in_session(ts, cfg):
            signals.append(0); scores.append(0)
            entries.append(np.nan); sls.append(np.nan)
            tps.append(np.nan); qtys.append(0)
            continue

        # ── cooldown guard ──
        if i - last_signal_candle < cfg["cooldown_candles"]:
            signals.append(0); scores.append(0)
            entries.append(np.nan); sls.append(np.nan)
            tps.append(np.nan); qtys.append(0)
            continue

        atr   = row["atr"]
        entry = row["close"]

        # ── LONG check ──
        if row["rsi_cross_up"] and row["above_vwap"]:
            # Hard gate: price must not be extended too far above VWAP (avoids chasing)
            # vol_spike and ema_bull kept as score-only — momentum names signal before
            # volume fully catches up, so hard-gating on volume kills early-trend entries
            if pd.notna(row["vwap_dist_pct"]) and row["vwap_dist_pct"] > cfg["vwap_max_dist_pct"]:
                signals.append(0); scores.append(0)
                entries.append(np.nan); sls.append(np.nan)
                tps.append(np.nan); qtys.append(0)
                continue
            s = score_long(row, cfg)
            if s >= cfg["min_signal_score"]:
                signal = 1
                score  = s
                sl     = entry - cfg["atr_sl_mult"] * atr
                tp     = entry + cfg["atr_tp_mult"] * atr
                qty    = bot_position_size(entry, sl, cfg)
                last_signal_candle = i

        # ── SHORT check ──
        elif row["rsi_cross_dn"] and not row["above_vwap"]:
            s = score_short(row, cfg)
            if s >= cfg["min_signal_score"]:
                signal = -1
                score  = s
                sl     = entry + cfg["atr_sl_mult"] * atr
                tp     = entry - cfg["atr_tp_mult"] * atr
                qty    = bot_position_size(entry, sl, cfg)
                last_signal_candle = i

        if signal == 0:
            signals.append(0); scores.append(0)
            entries.append(np.nan); sls.append(np.nan)
            tps.append(np.nan); qtys.append(0)
        else:
            signals.append(signal)
            scores.append(score)
            entries.append(round(entry, 2))
            sls.append(round(sl, 2))
            tps.append(round(tp, 2))
            qtys.append(qty)

    df["signal"] = signals
    df["score"]  = scores
    df["entry"]  = entries
    df["sl"]     = sls
    df["tp"]     = tps
    df["qty"]    = qtys
    return df


# ─────────────────────────────────────────────
#  BACKTESTER  (candle-close simulation)
# ─────────────────────────────────────────────

def backtest(df: pd.DataFrame) -> pd.DataFrame:
    """
    Simple next-candle-open fill simulation.
    Each signal is taken on next candle open; exits at SL/TP or EOD.
    Returns a trades DataFrame.
    """
    trades = []
    signal_rows = df[df["signal"] != 0].copy()

    for idx, row in signal_rows.iterrows():
        loc = df.index.get_loc(idx)
        if loc + 1 >= len(df):
            continue

        fill_price  = df.iloc[loc + 1]["open"]   # next candle open
        direction   = row["signal"]
        sl, tp      = row["sl"], row["tp"]
        qty         = row["qty"]
        entry_time  = df.index[loc + 1]

        pnl    = None
        reason = "EOD"

        # walk forward to find SL/TP hit
        for j in range(loc + 1, len(df)):
            c = df.iloc[j]
            if direction == 1:   # LONG
                if c["low"]  <= sl:  pnl = (sl - fill_price) * qty; reason = "SL"; break
                if c["high"] >= tp:  pnl = (tp - fill_price) * qty; reason = "TP"; break
            else:                # SHORT
                if c["high"] >= sl:  pnl = (fill_price - sl) * qty; reason = "SL"; break
                if c["low"]  <= tp:  pnl = (fill_price - tp) * qty; reason = "TP"; break

        if pnl is None:
            exit_price = df.iloc[-1]["close"]
            pnl = (exit_price - fill_price) * qty * direction

        trades.append({
            "entry_time" : entry_time,
            "direction"  : "LONG" if direction == 1 else "SHORT",
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

    total      = len(trades)
    wins       = trades[trades["pnl"] > 0]
    losses     = trades[trades["pnl"] <= 0]
    win_rate   = len(wins) / total * 100
    gross_pnl  = trades["pnl"].sum()
    avg_win    = wins["pnl"].mean()   if not wins.empty   else 0
    avg_loss   = losses["pnl"].mean() if not losses.empty else 0
    rr         = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")
    max_dd     = (trades["pnl"].cumsum() - trades["pnl"].cumsum().cummax()).min()

    print("\n" + "═"*55)
    print(f"  BACKTEST RESULTS  ·  {cfg['symbol']}  [{cfg['timeframe']}]")
    print("═"*55)
    print(f"  Total Trades    : {total}")
    print(f"  Win Rate        : {win_rate:.1f}%")
    print(f"  Gross P&L       : ₹{gross_pnl:,.2f}")
    print(f"  Avg Win         : ₹{avg_win:,.2f}")
    print(f"  Avg Loss        : ₹{avg_loss:,.2f}")
    print(f"  Reward:Risk     : {rr:.2f}")
    print(f"  Max Drawdown    : ₹{max_dd:,.2f}")
    print(f"  TP Exits        : {(trades['exit_reason']=='TP').sum()}")
    print(f"  SL Exits        : {(trades['exit_reason']=='SL').sum()}")
    print(f"  EOD Exits       : {(trades['exit_reason']=='EOD').sum()}")
    print("═"*55)
    print(trades[["entry_time","direction","score","fill","sl","tp","qty","pnl","exit_reason"]].to_string(index=False))
    print("═"*55 + "\n")


# ─────────────────────────────────────────────
#  LIVE / PAPER TRADING HOOK
# ─────────────────────────────────────────────

class VWAPRSIBot:
    """
    Real-time wrapper. Feed one candle at a time via `on_candle()`.
    Plug in your broker API in `place_order()`.
    """

    def __init__(self, cfg: Optional[dict] = None):
        self.cfg     = cfg or _cfg_dict()
        self.history = pd.DataFrame(columns=["open","high","low","close","volume"])
        self.history.index.name = "timestamp"
        self.in_trade      = False
        self.position_side = None
        self.entry_price   = None
        self.sl            = None
        self.tp            = None

    def on_candle(self, timestamp, open_, high, low, close, volume):
        """Call this on every new completed candle."""
        new_row = pd.DataFrame(
            [[open_, high, low, close, volume]],
            index   = pd.DatetimeIndex([timestamp]),
            columns = ["open","high","low","close","volume"]
        )
        self.history = pd.concat([self.history, new_row])

        # need at least RSI period + slow EMA warmup candles
        warmup = max(self.cfg["rsi_period"], self.cfg["ema_slow"]) + 5
        if len(self.history) < warmup:
            return

        df_slice = self.history.copy()
        df_slice = add_all_indicators(df_slice, self.cfg)
        last     = df_slice.iloc[-1]

        # ── Exit check if in trade ──
        if self.in_trade:
            if self.position_side == "LONG":
                if low  <= self.sl: self._exit("SL hit", close); return
                if high >= self.tp: self._exit("TP hit", close); return
            elif self.position_side == "SHORT":
                if high >= self.sl: self._exit("SL hit", close); return
                if low  <= self.tp: self._exit("TP hit", close); return
            return  # already in trade, skip new signal scan

        # ── Signal check ──
        if not in_session(timestamp, self.cfg):
            return

        atr = last["atr"]

        if last["rsi_cross_up"] and last["above_vwap"]:
            s = score_long(last, self.cfg)
            if s >= self.cfg["min_signal_score"]:
                sl  = close - self.cfg["atr_sl_mult"] * atr
                tp  = close + self.cfg["atr_tp_mult"] * atr
                qty = position_size(close, sl, self.cfg)
                self._enter("LONG", close, sl, tp, qty, s)

        elif last["rsi_cross_dn"] and not last["above_vwap"]:
            s = score_short(last, self.cfg)
            if s >= self.cfg["min_signal_score"]:
                sl  = close + self.cfg["atr_sl_mult"] * atr
                tp  = close - self.cfg["atr_tp_mult"] * atr
                qty = position_size(close, sl, self.cfg)
                self._enter("SHORT", close, sl, tp, qty, s)

    def _enter(self, side, price, sl, tp, qty, score):
        print(f"\n🟢 SIGNAL  [{side}]  Score:{score}/5")
        print(f"   Entry={price:.2f}  SL={sl:.2f}  TP={tp:.2f}  Qty={qty}")
        self.in_trade      = True
        self.position_side = side
        self.entry_price   = price
        self.sl            = sl
        self.tp            = tp
        self.place_order(side, "ENTER", price, qty)

    def _exit(self, reason, price):
        print(f"🔴 EXIT [{self.position_side}]  Reason: {reason}  @ {price:.2f}")
        self.place_order(self.position_side, "EXIT", price, 0)
        self.in_trade      = False
        self.position_side = None

    def place_order(self, side, action, price, qty):
        """
        ── BROKER INTEGRATION POINT ──
        Replace the print below with your actual broker API call.

        Example with Zerodha Kite:
            kite.place_order(
                tradingsymbol = self.cfg["symbol"],
                exchange      = "NSE",
                transaction_type = kite.TRANSACTION_TYPE_BUY if side=="LONG" else kite.TRANSACTION_TYPE_SELL,
                quantity      = qty,
                order_type    = kite.ORDER_TYPE_MARKET,
                product       = kite.PRODUCT_MIS,
            )
        """
        print(f"   [ORDER] {action} {side} | Price≈{price:.2f} | Qty={qty}")


# ─────────────────────────────────────────────
#  DEMO  (runs on synthetic data if no CSV)
# ─────────────────────────────────────────────

def generate_demo_data(n: int = 500) -> pd.DataFrame:
    """Generates realistic synthetic OHLCV data for demo/testing."""
    np.random.seed(42)
    dates  = pd.date_range("2024-01-15 09:15", periods=n, freq="5min")
    close  = 45000 + np.cumsum(np.random.randn(n) * 60)
    spread = np.abs(np.random.randn(n)) * 40 + 20
    high   = close + spread
    low    = close - spread
    open_  = close - np.random.randn(n) * 30
    volume = np.abs(np.random.randn(n) * 5000 + 20000).astype(int)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume
    }, index=dates)


if __name__ == "__main__":
    print("═"*55)
    print("  VWAP + RSI BOT  ·  Loading demo data …")
    print("═"*55)

    # ── To use your own CSV, replace this line: ──
    # df = pd.read_csv("your_data.csv", index_col="timestamp", parse_dates=True)
    CONFIG = _cfg_dict()
    df = generate_demo_data(500)

    # Run signal generation
    df_signals = generate_signals(df, CONFIG)

    # Backtest
    trades = backtest(df_signals)

    # Report
    performance_report(trades, CONFIG)

    # Show live bot usage example
    print("\n── Live Bot Usage Example ──")
    bot = VWAPRSIBot(CONFIG)
    for ts, row in df.iloc[:100].iterrows():
        bot.on_candle(ts, row["open"], row["high"], row["low"], row["close"], row["volume"])
